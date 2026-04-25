# -*- coding: utf-8 -*-
"""
translator.py — Phase 3.2: Multilingual Article Translation via Claude
AI for Managers — Competitive Intelligence Briefing Tool

Translates non-English news articles into English using Claude, preserving
financial sentiment markers and tone — not just literal meaning.

Key design principle: a skeptical French article must FEEL skeptical in English.
Downstream sentiment scoring depends on this.

Usage:
    python translator.py                  # Translate all pending non-English articles
    python translator.py --ticker NVDA    # Translate one company only
    python translator.py --limit 10       # Translate a batch of 10 for testing
"""

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from config import settings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Language Names ────────────────────────────────────────────────────────────
# Maps ISO 639-1 codes to human-readable names for the prompt.
LANGUAGE_NAMES: dict[str, str] = {
    "zh": "Mandarin Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "ru": "Russian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "it": "Italian",
    "nl": "Dutch",
    "pl": "Polish",
    "hi": "Hindi",
    "id": "Indonesian",
    "tr": "Turkish",
}

def language_name(code: str) -> str:
    """Return a readable name for a language code, e.g. 'zh' → 'Mandarin Chinese'."""
    return LANGUAGE_NAMES.get(code.lower(), code.upper())


# ── Translation System Prompt ─────────────────────────────────────────────────
# This is the core prompt engineering deliverable for Phase 3.2.
# It instructs Claude to preserve sentiment markers — not just translate meaning.

TRANSLATION_SYSTEM_PROMPT = """You are a financial translator specializing in business journalism.

Your job is to translate news articles about publicly traded companies into English.
These translations will be used for financial sentiment analysis, so preserving
emotional tone is MORE important than literal word-for-word accuracy.

TRANSLATION RULES:
1. Preserve all sentiment markers — if the original is skeptical, alarmed, or
   optimistic, your English translation must convey the same emotional register.
2. Keep all numerical values, dates, percentages, and proper nouns exactly as written.
3. Translate idiomatic financial expressions to their closest English financial
   equivalent (e.g., do not translate literally if there is a standard English term).
4. If the article contains cultural context that would change its meaning for a
   Western financial reader, add ONE bracketed note: [Cultural context: ...]
5. Do not summarize. Translate the full text provided.
6. After the translation, output a JSON metadata block (no extra commentary):

{
  "source_language": "...",
  "tone": "positive" | "neutral" | "negative" | "mixed",
  "key_entities_mentioned": ["..."],
  "translation_confidence": "high" | "medium" | "low",
  "financial_sentiment_preserved": true | false
}

Format your full response as:
<translation>
[translated text here]
</translation>
<metadata>
[JSON block here]
</metadata>"""


def build_translation_user_prompt(article_text: str, company_name: str, source_language: str) -> str:
    """Build the user-turn message for a translation request."""
    lang_display = language_name(source_language)
    return (
        f"Translate the following article about {company_name} from {lang_display} into English.\n\n"
        f"Article:\n{article_text}"
    )


# ── Claude Translation Call ───────────────────────────────────────────────────

def translate_article(
    client: anthropic.Anthropic,
    article_text: str,
    company_name: str,
    source_language: str,
) -> dict:
    """
    Call Claude to translate one article. Returns a dict with:
        translated_text   — the English translation
        tone              — positive | neutral | negative | mixed
        key_entities      — list of entities mentioned
        confidence        — high | medium | low
        sentiment_preserved — bool
        raw_response      — full Claude response text (for debugging)
    """
    user_prompt = build_translation_user_prompt(article_text, company_name, source_language)

    response = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=2048,
        temperature=0.1,        # Low temperature for consistent, faithful translation
        system=TRANSLATION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = response.content[0].text

    # Parse translation and metadata from tagged sections
    translated_text = _extract_tag(raw_text, "translation") or raw_text
    metadata_str    = _extract_tag(raw_text, "metadata") or "{}"

    try:
        metadata = json.loads(metadata_str)
    except json.JSONDecodeError:
        log.warning("Could not parse translation metadata JSON — using defaults")
        metadata = {}

    return {
        "translated_text":      translated_text.strip(),
        "tone":                 metadata.get("tone", "unknown"),
        "key_entities":         metadata.get("key_entities_mentioned", []),
        "confidence":           metadata.get("translation_confidence", "unknown"),
        "sentiment_preserved":  metadata.get("financial_sentiment_preserved", False),
        "raw_response":         raw_text,
    }


def _extract_tag(text: str, tag: str) -> str | None:
    """Extract content between <tag>...</tag> XML-style markers."""
    start_marker = f"<{tag}>"
    end_marker   = f"</{tag}>"
    start = text.find(start_marker)
    end   = text.find(end_marker)
    if start == -1 or end == -1:
        return None
    return text[start + len(start_marker):end].strip()


# ── Database Helpers ──────────────────────────────────────────────────────────

def open_db(db_path: Path) -> sqlite3.Connection:
    """Open the existing SQLite database (must be initialised by news_fetcher.py first)."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}.\n"
            "Run news_fetcher.py first to create and populate it."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Add translation columns if they don't exist yet (safe migration)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(news_articles)")}
    new_cols = {
        "translated_headline": "TEXT",
        "translated_snippet":  "TEXT",
        "translation_tone":    "TEXT",
        "translation_confidence": "TEXT",
        "translated_at":       "TEXT",
    }
    for col, col_type in new_cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE news_articles ADD COLUMN {col} {col_type}")
    conn.commit()
    return conn


def get_untranslated_articles(
    conn: sqlite3.Connection,
    ticker: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """
    Fetch articles that are non-English and not yet translated.
    Skips 'unknown' language — these are usually very short snippets
    where translation would add no value.
    """
    sql = """
        SELECT article_id, ticker, company_name, headline, content_snippet, language
        FROM news_articles
        WHERE language NOT IN ('en', 'unknown')
          AND translated_at IS NULL
    """
    params: list = []
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    sql += " ORDER BY published_at DESC"
    if limit:
        sql += f" LIMIT {limit}"

    return conn.execute(sql, params).fetchall()


def save_translation(conn: sqlite3.Connection, article_id: str, result: dict) -> None:
    """Write translation results back to the news_articles row."""
    conn.execute("""
        UPDATE news_articles
        SET translated_headline    = :headline,
            translated_snippet     = :snippet,
            translation_tone       = :tone,
            translation_confidence = :confidence,
            translated_at          = :translated_at
        WHERE article_id = :article_id
    """, {
        "article_id":   article_id,
        "headline":     result.get("translated_text", "")[:500],   # headline portion
        "snippet":      result.get("translated_text", "")[:1000],  # snippet portion
        "tone":         result.get("tone", "unknown"),
        "confidence":   result.get("confidence", "unknown"),
        "translated_at": datetime.now(timezone.utc).isoformat(),
    })
    conn.commit()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(ticker_filter: str | None = None, limit: int | None = None) -> None:
    """
    Translate all non-English articles that haven't been translated yet.

    Args:
        ticker_filter: Only translate articles for this ticker (e.g. 'NVDA')
        limit:         Translate at most this many articles (useful for testing)
    """
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    conn   = open_db(settings.SQLITE_DB_PATH)

    articles = get_untranslated_articles(conn, ticker=ticker_filter, limit=limit)
    log.info("Found %d non-English articles to translate", len(articles))

    if not articles:
        log.info("Nothing to translate — all articles are already in English or translated.")
        conn.close()
        return

    translated_count = 0
    failed_count     = 0

    for i, row in enumerate(articles):
        article_id   = row["article_id"]
        company_name = row["company_name"]
        language     = row["language"]
        text_to_translate = " ".join(filter(None, [row["headline"], row["content_snippet"]]))

        log.info("[%d/%d] Translating %s (%s) — %s",
                 i + 1, len(articles), company_name, row["ticker"], language_name(language))

        try:
            result = translate_article(client, text_to_translate, company_name, language)
            save_translation(conn, article_id, result)
            translated_count += 1
            log.info("  → tone: %s | confidence: %s | sentiment preserved: %s",
                     result["tone"], result["confidence"], result["sentiment_preserved"])

        except anthropic.RateLimitError:
            log.warning("  Rate limited — sleeping 30 s")
            time.sleep(30)
            failed_count += 1

        except Exception as e:
            log.error("  Translation failed for article %s: %s", article_id, e)
            failed_count += 1

        # Pause between Claude calls to stay within rate limits
        time.sleep(0.5)

    conn.close()
    log.info("Done. Translated: %d | Failed/skipped: %d", translated_count, failed_count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translate non-English news articles via Claude")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Only translate articles for this ticker (e.g. --ticker NVDA)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max articles to translate (useful for testing, e.g. --limit 10)")
    args = parser.parse_args()

    main(ticker_filter=args.ticker, limit=args.limit)
