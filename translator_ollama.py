# -*- coding: utf-8 -*-
"""
translator_ollama.py — Phase 3.2: Multilingual Translation via Ollama
AI for Managers — Competitive Intelligence Briefing Tool

Drop-in replacement for translator.py — uses a local Ollama model
instead of Claude. No API key required.

Key design principle: a skeptical French article must FEEL skeptical
in English. Downstream sentiment scoring depends on preserved tone.

Usage:
    python translator_ollama.py                  # Translate all pending
    python translator_ollama.py --ticker NVDA    # One company only
    python translator_ollama.py --limit 10       # Small test batch
    python translator_ollama.py --stats          # Show translation counts
"""

import argparse
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import ollama

from config import settings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Model ─────────────────────────────────────────────────────────────────────
OLLAMA_MODEL = "llama3.1:8b"

# ── Language Names ────────────────────────────────────────────────────────────
LANGUAGE_NAMES: dict[str, str] = {
    "zh":    "Mandarin Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-tw": "Traditional Chinese",
    "ja":    "Japanese",
    "ko":    "Korean",
    "ar":    "Arabic",
    "ru":    "Russian",
    "de":    "German",
    "fr":    "French",
    "es":    "Spanish",
    "pt":    "Portuguese",
    "it":    "Italian",
    "nl":    "Dutch",
    "pl":    "Polish",
    "hi":    "Hindi",
    "id":    "Indonesian",
    "tr":    "Turkish",
    "ro":    "Romanian",
    "da":    "Danish",
    "sv":    "Swedish",
}

def language_name(code: str) -> str:
    """Return a readable name for a language code, e.g. 'zh' → 'Mandarin Chinese'."""
    return LANGUAGE_NAMES.get(code.lower(), code.upper())


# ── Translation System Prompt ─────────────────────────────────────────────────
# Mirrors the Claude version exactly so output format is identical.
# The structured XML tags make parsing reliable even when the model
# adds surrounding commentary.

TRANSLATION_SYSTEM_PROMPT = """You are a financial translator specializing in business journalism.

Your job is to translate news articles about publicly traded companies into English.
These translations will be used for financial sentiment analysis, so preserving
emotional tone is MORE important than literal word-for-word accuracy.

TRANSLATION RULES:
1. Preserve all sentiment markers — if the original is skeptical, alarmed, or
   optimistic, your English translation must convey the same emotional register.
2. Keep all numerical values, dates, percentages, and proper nouns exactly as written.
3. Translate idiomatic financial expressions to their closest English financial
   equivalent — do not translate literally if a standard English term exists.
4. If the article contains cultural context that would change its meaning for a
   Western financial reader, add ONE bracketed note: [Cultural context: ...]
5. Do not summarize. Translate the full text provided.
6. After the translation, output a metadata block in this exact format:

<translation>
[translated text here]
</translation>
<metadata>
{
  "source_language": "...",
  "tone": "positive" or "neutral" or "negative" or "mixed",
  "key_entities_mentioned": ["..."],
  "translation_confidence": "high" or "medium" or "low",
  "financial_sentiment_preserved": true or false
}
</metadata>

Do not add any text after the closing </metadata> tag."""


def build_translation_prompt(
    article_text: str,
    company_name: str,
    source_language: str,
) -> str:
    """Build the user message for a translation request."""
    lang_display = language_name(source_language)
    return (
        f"Translate the following article about {company_name} "
        f"from {lang_display} into English.\n\n"
        f"Article:\n{article_text}"
    )


# ── Tag Extraction ────────────────────────────────────────────────────────────

def _extract_tag(text: str, tag: str) -> str | None:
    """Extract content between <tag>...</tag> markers."""
    start_marker = f"<{tag}>"
    end_marker   = f"</{tag}>"
    start = text.find(start_marker)
    end   = text.find(end_marker)
    if start == -1 or end == -1:
        return None
    return text[start + len(start_marker):end].strip()


def _extract_json_from_metadata(text: str) -> dict:
    """
    Parse the JSON block inside <metadata> tags.
    Falls back to regex extraction if the model adds extra commentary.
    """
    if not text:
        return {}

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting first {...} block
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


# ── Ollama Translation Call ───────────────────────────────────────────────────

def translate_article(
    article_text: str,
    company_name: str,
    source_language: str,
) -> dict:
    """
    Translate one article using a local Ollama model.

    Returns a dict with:
        translated_text     — English translation
        tone                — positive | neutral | negative | mixed
        key_entities        — list of entities mentioned
        confidence          — high | medium | low
        sentiment_preserved — bool
        raw_response        — full model output (for debugging)
    """
    user_prompt = build_translation_prompt(article_text, company_name, source_language)

    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        options={
            "temperature": 0.1,      # Low temp for faithful, consistent translation
            "num_predict": 2048,
        },
    )

    raw_text = response["message"]["content"]

    # Parse translation and metadata from tagged sections
    translated_text = _extract_tag(raw_text, "translation") or raw_text
    metadata_str    = _extract_tag(raw_text, "metadata") or ""
    metadata        = _extract_json_from_metadata(metadata_str)

    return {
        "translated_text":     translated_text.strip(),
        "tone":                metadata.get("tone", "unknown"),
        "key_entities":        metadata.get("key_entities_mentioned", []),
        "confidence":          metadata.get("translation_confidence", "unknown"),
        "sentiment_preserved": metadata.get("financial_sentiment_preserved", False),
        "raw_response":        raw_text,
    }


# ── Database Helpers ──────────────────────────────────────────────────────────

def open_db(db_path: Path) -> sqlite3.Connection:
    """Open the existing SQLite database and add translation columns if missing."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}.\n"
            "Run news_fetcher.py first to create and populate it."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Safe migration — add columns only if they don't exist
    existing = {row[1] for row in conn.execute("PRAGMA table_info(news_articles)")}
    new_cols = {
        "translated_headline":    "TEXT",
        "translated_snippet":     "TEXT",
        "translation_tone":       "TEXT",
        "translation_confidence": "TEXT",
        "translated_at":          "TEXT",
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
    Fetch non-English articles that have not been translated yet.
    Skips 'unknown' language — usually very short snippets where
    translation adds no value.
    """
    sql = """
        SELECT article_id, ticker, company_name, headline,
               content_snippet, language
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


def save_translation(
    conn: sqlite3.Connection,
    article_id: str,
    result: dict,
) -> None:
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
        "headline":     result.get("translated_text", "")[:500],
        "snippet":      result.get("translated_text", "")[:1000],
        "tone":         result.get("tone", "unknown"),
        "confidence":   result.get("confidence", "unknown"),
        "translated_at": datetime.now(timezone.utc).isoformat(),
    })
    conn.commit()


def print_stats(conn: sqlite3.Connection) -> None:
    """Print a summary of translation status by language."""
    rows = conn.execute("""
        SELECT language,
               COUNT(*) AS total,
               SUM(CASE WHEN translated_at IS NOT NULL THEN 1 ELSE 0 END) AS translated
        FROM news_articles
        WHERE language NOT IN ('en', 'unknown')
        GROUP BY language
        ORDER BY total DESC
    """).fetchall()

    if not rows:
        print("No non-English articles found in the database.")
        return

    print(f"\n{'─'*50}")
    print(f"{'LANGUAGE':<12} {'TOTAL':>8} {'TRANSLATED':>12} {'REMAINING':>10}")
    print(f"{'─'*50}")
    for r in rows:
        remaining = r["total"] - r["translated"]
        lang = language_name(r["language"])
        print(f"{lang:<12} {r['total']:>8} {r['translated']:>12} {remaining:>10}")
    print(f"{'─'*50}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    ticker_filter: str | None = None,
    limit: int | None = None,
    show_stats: bool = False,
) -> None:
    conn = open_db(settings.SQLITE_DB_PATH)

    if show_stats:
        print_stats(conn)
        conn.close()
        return

    articles = get_untranslated_articles(conn, ticker=ticker_filter, limit=limit)
    log.info("Found %d non-English articles to translate using %s",
             len(articles), OLLAMA_MODEL)

    if not articles:
        log.info("Nothing to translate — all articles are English or already translated.")
        print_stats(conn)
        conn.close()
        return

    translated_count = 0
    failed_count     = 0

    for i, row in enumerate(articles):
        text = " ".join(filter(None, [row["headline"], row["content_snippet"]]))
        lang = row["language"]

        log.info("[%d/%d] Translating %s (%s) — %s",
                 i + 1, len(articles),
                 row["company_name"], row["ticker"],
                 language_name(lang))

        try:
            result = translate_article(text, row["company_name"], lang)
            save_translation(conn, row["article_id"], result)
            translated_count += 1
            log.info("  → tone: %s | confidence: %s | sentiment preserved: %s",
                     result["tone"],
                     result["confidence"],
                     result["sentiment_preserved"])

        except Exception as e:
            log.error("  Translation failed for %s: %s", row["article_id"], e)
            failed_count += 1

        time.sleep(0.1)   # Small pause between Ollama calls

    conn.close()
    log.info("Done. Translated: %d | Failed: %d", translated_count, failed_count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Translate non-English news articles via local Ollama model"
    )
    parser.add_argument("--ticker", type=str, default=None,
                        help="Only translate articles for this ticker")
    parser.add_argument("--limit",  type=int, default=None,
                        help="Max articles to translate (e.g. --limit 10)")
    parser.add_argument("--stats",  action="store_true",
                        help="Show translation status by language and exit")
    args = parser.parse_args()

    main(ticker_filter=args.ticker, limit=args.limit, show_stats=args.stats)