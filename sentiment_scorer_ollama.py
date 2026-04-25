# -*- coding: utf-8 -*-
"""
sentiment_scorer_ollama.py — Phase 3.3: Sentiment Scoring via Ollama
AI for Managers — Competitive Intelligence Briefing Tool

Scores each news article using a local Ollama model instead of Claude.
Drop-in replacement for sentiment_scorer.py — no API key required.

Usage:
    python sentiment_scorer_ollama.py              # Score all unscored articles
    python sentiment_scorer_ollama.py --ticker LLY # Score one company only
    python sentiment_scorer_ollama.py --limit 20   # Score a batch for testing
    python sentiment_scorer_ollama.py --summary    # Print sentiment table
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


# ── Sentiment Prompt ──────────────────────────────────────────────────────────
# Structured JSON-only output prompt — mirrors the Claude version exactly
# so downstream code (Phase 5 briefing generator) works with either scorer.

SENTIMENT_SYSTEM_PROMPT = """You are a financial sentiment analyst specializing in equity research.
Your task is to analyze news articles about publicly traded companies and score their sentiment.

Return ONLY a valid JSON object — no preamble, no explanation, no markdown code fences.
The JSON must strictly follow this schema:

{
  "overall_sentiment": <float from -1.0 (very negative) to 1.0 (very positive)>,
  "overall_label": "very_negative" | "negative" | "neutral" | "positive" | "very_positive",
  "confidence": <float from 0.0 to 1.0>,
  "topic_scores": {
    "earnings_outlook":       <null or float -1.0 to 1.0>,
    "competitive_position":   <null or float -1.0 to 1.0>,
    "regulatory_environment": <null or float -1.0 to 1.0>,
    "leadership_management":  <null or float -1.0 to 1.0>,
    "product_innovation":     <null or float -1.0 to 1.0>
  },
  "key_signals": ["<2-3 specific phrases or facts that most influenced the score>"],
  "forward_looking": <true if article discusses future expectations, false if retrospective>,
  "materiality": "high" | "medium" | "low"
}

SCORING GUIDELINES:
- overall_sentiment: Use the full -1.0 to 1.0 range. Reserve +/-0.8 to +/-1.0 for major
  events (earnings miss, leadership scandal, drug approval, antitrust ruling).
- confidence: Lower confidence when article is vague, opinion-heavy, or very short.
- topic_scores: Set to null if the article does not address that topic at all.
- materiality: "high" = could move stock price; "medium" = relevant background; "low" = noise.
- forward_looking: true if the article makes predictions or discusses guidance."""


def build_user_prompt(article_text: str, company_name: str, ticker: str) -> str:
    """Build the user message for sentiment scoring."""
    return (
        f"Company: {company_name} (${ticker})\n\n"
        f"Article:\n{article_text}"
    )


# ── JSON Extraction ───────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """
    Extract a JSON object from model output that may contain
    surrounding text, markdown fences, or explanation.
    Tries three strategies in order:
      1. Direct parse (model returned clean JSON)
      2. Strip markdown code fences
      3. Regex extraction of first {...} block
    """
    text = text.strip()

    # Strategy 1: direct parse
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown fences
    if "```" in text:
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fenced:
            candidate = fenced.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

    # Strategy 3: extract first {...} block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        candidate = brace_match.group(0)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # All strategies failed
    return ""


def _neutral_sentiment() -> dict:
    """Fallback neutral sentiment when parsing fails."""
    return {
        "overall_sentiment": 0.0,
        "overall_label": "neutral",
        "confidence": 0.0,
        "topic_scores": {
            "earnings_outlook":       None,
            "competitive_position":   None,
            "regulatory_environment": None,
            "leadership_management":  None,
            "product_innovation":     None,
        },
        "key_signals": [],
        "forward_looking": False,
        "materiality": "low",
    }


# ── Ollama Sentiment Call ─────────────────────────────────────────────────────

def score_article_sentiment(
    article_text: str,
    company_name: str,
    ticker: str,
) -> dict:
    """
    Call Ollama to score the sentiment of one article.
    Returns the parsed sentiment dict, or neutral defaults on error.
    """
    user_prompt = build_user_prompt(article_text, company_name, ticker)

    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        options={
            "temperature": 0.1,    # Low temperature for consistent scoring
            "num_predict": 512,
        },
    )

    raw_text = response["message"]["content"].strip()
    json_str = _extract_json(raw_text)

    if not json_str:
        log.warning("Could not extract JSON from Ollama response — using neutral defaults")
        log.debug("Raw response: %s", raw_text[:300])
        return _neutral_sentiment()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log.warning("JSON parse error: %s — using neutral defaults", e)
        return _neutral_sentiment()


# ── Database Helpers ──────────────────────────────────────────────────────────

def open_db(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite database and add sentiment columns if missing."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}.\n"
            "Run news_fetcher.py first to populate it."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Safe migration — add columns only if they don't exist yet
    existing = {row[1] for row in conn.execute("PRAGMA table_info(news_articles)")}
    new_cols = {
        "sentiment_score":           "REAL",
        "sentiment_label":           "TEXT",
        "sentiment_confidence":      "REAL",
        "sentiment_materiality":     "TEXT",
        "sentiment_forward_looking": "INTEGER",
        "sentiment_key_signals":     "TEXT",
        "topic_earnings":            "REAL",
        "topic_competitive":         "REAL",
        "topic_regulatory":          "REAL",
        "topic_leadership":          "REAL",
        "topic_innovation":          "REAL",
        "scored_at":                 "TEXT",
    }
    for col, col_type in new_cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE news_articles ADD COLUMN {col} {col_type}")

    # Company-level sentiment index
    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_sentiment_index (
            ticker                 TEXT PRIMARY KEY,
            company_name           TEXT NOT NULL,
            sector                 TEXT NOT NULL,
            avg_sentiment          REAL,
            article_count          INTEGER,
            high_materiality_count INTEGER,
            avg_earnings_outlook   REAL,
            avg_competitive        REAL,
            avg_regulatory         REAL,
            avg_leadership         REAL,
            avg_innovation         REAL,
            dominant_tone          TEXT,
            last_updated           TEXT
        )
    """)
    conn.commit()
    return conn


def get_unscored_articles(
    conn: sqlite3.Connection,
    ticker: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Fetch articles that have not been sentiment-scored yet."""
    sql = """
        SELECT article_id, ticker, company_name, headline,
               content_snippet, translated_snippet, language
        FROM news_articles
        WHERE scored_at IS NULL
    """
    params: list = []
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    sql += " ORDER BY published_at DESC"
    if limit:
        sql += f" LIMIT {limit}"
    return conn.execute(sql, params).fetchall()


def save_article_sentiment(
    conn: sqlite3.Connection,
    article_id: str,
    scores: dict,
) -> None:
    """Write per-article sentiment scores back to the database."""
    topics = scores.get("topic_scores") or {}
    conn.execute("""
        UPDATE news_articles
        SET sentiment_score           = :score,
            sentiment_label           = :label,
            sentiment_confidence      = :confidence,
            sentiment_materiality     = :materiality,
            sentiment_forward_looking = :forward,
            sentiment_key_signals     = :signals,
            topic_earnings            = :earnings,
            topic_competitive         = :competitive,
            topic_regulatory          = :regulatory,
            topic_leadership          = :leadership,
            topic_innovation          = :innovation,
            scored_at                 = :scored_at
        WHERE article_id = :article_id
    """, {
        "article_id":  article_id,
        "score":       scores.get("overall_sentiment"),
        "label":       scores.get("overall_label"),
        "confidence":  scores.get("confidence"),
        "materiality": scores.get("materiality"),
        "forward":     1 if scores.get("forward_looking") else 0,
        "signals":     json.dumps(scores.get("key_signals", [])),
        "earnings":    topics.get("earnings_outlook"),
        "competitive": topics.get("competitive_position"),
        "regulatory":  topics.get("regulatory_environment"),
        "leadership":  topics.get("leadership_management"),
        "innovation":  topics.get("product_innovation"),
        "scored_at":   datetime.now(timezone.utc).isoformat(),
    })
    conn.commit()


def rebuild_company_index(conn: sqlite3.Connection) -> None:
    """Aggregate per-article scores into the company-level sentiment index."""
    rows = conn.execute("""
        SELECT ticker, company_name, sector,
               AVG(sentiment_score)                                      AS avg_sentiment,
               COUNT(*)                                                  AS article_count,
               SUM(CASE WHEN sentiment_materiality='high' THEN 1 ELSE 0 END) AS high_mat,
               AVG(topic_earnings)                                       AS avg_earnings,
               AVG(topic_competitive)                                    AS avg_competitive,
               AVG(topic_regulatory)                                     AS avg_regulatory,
               AVG(topic_leadership)                                     AS avg_leadership,
               AVG(topic_innovation)                                     AS avg_innovation
        FROM news_articles
        WHERE scored_at IS NOT NULL
        GROUP BY ticker
    """).fetchall()

    for row in rows:
        avg = row["avg_sentiment"] or 0.0
        if avg >= 0.2:
            tone = "positive"
        elif avg <= -0.2:
            tone = "negative"
        else:
            tone = "neutral"

        conn.execute("""
            INSERT INTO company_sentiment_index
                (ticker, company_name, sector, avg_sentiment, article_count,
                 high_materiality_count, avg_earnings_outlook, avg_competitive,
                 avg_regulatory, avg_leadership, avg_innovation,
                 dominant_tone, last_updated)
            VALUES
                (:ticker, :company_name, :sector, :avg_sentiment, :article_count,
                 :high_mat, :avg_earnings, :avg_competitive, :avg_regulatory,
                 :avg_leadership, :avg_innovation, :tone, :updated)
            ON CONFLICT(ticker) DO UPDATE SET
                avg_sentiment          = excluded.avg_sentiment,
                article_count          = excluded.article_count,
                high_materiality_count = excluded.high_materiality_count,
                avg_earnings_outlook   = excluded.avg_earnings_outlook,
                avg_competitive        = excluded.avg_competitive,
                avg_regulatory         = excluded.avg_regulatory,
                avg_leadership         = excluded.avg_leadership,
                avg_innovation         = excluded.avg_innovation,
                dominant_tone          = excluded.dominant_tone,
                last_updated           = excluded.last_updated
        """, {
            "ticker":          row["ticker"],
            "company_name":    row["company_name"],
            "sector":          row["sector"],
            "avg_sentiment":   round(avg, 4),
            "article_count":   row["article_count"],
            "high_mat":        row["high_mat"],
            "avg_earnings":    row["avg_earnings"],
            "avg_competitive": row["avg_competitive"],
            "avg_regulatory":  row["avg_regulatory"],
            "avg_leadership":  row["avg_leadership"],
            "avg_innovation":  row["avg_innovation"],
            "tone":            tone,
            "updated":         datetime.now(timezone.utc).isoformat(),
        })
    conn.commit()
    log.info("Company sentiment index rebuilt for %d tickers", len(rows))


def print_sentiment_summary(conn: sqlite3.Connection) -> None:
    """Print the company-level sentiment leaderboard to stdout."""
    rows = conn.execute("""
        SELECT ticker, company_name, sector, avg_sentiment, article_count,
               high_materiality_count, dominant_tone, last_updated
        FROM company_sentiment_index
        ORDER BY avg_sentiment DESC
    """).fetchall()

    if not rows:
        print("No sentiment data yet. Run scoring first.")
        return

    print(f"\n{'─'*85}")
    print(f"{'TICKER':<8} {'COMPANY':<25} {'SECTOR':<12} {'AVG SCORE':>10} "
          f"{'ARTICLES':>9} {'HIGH-MAT':>9} {'TONE':<10}")
    print(f"{'─'*85}")
    for r in rows:
        score = r["avg_sentiment"]
        score_str = f"{score:+.3f}" if score is not None else "  N/A "
        print(f"{r['ticker']:<8} {r['company_name']:<25} {r['sector']:<12} "
              f"{score_str:>10} {r['article_count']:>9} "
              f"{r['high_materiality_count']:>9} {r['dominant_tone']:<10}")
    print(f"{'─'*85}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    ticker_filter: str | None = None,
    limit: int | None = None,
    show_summary: bool = False,
) -> None:
    conn = open_db(settings.SQLITE_DB_PATH)

    if show_summary:
        print_sentiment_summary(conn)
        conn.close()
        return

    articles = get_unscored_articles(conn, ticker=ticker_filter, limit=limit)
    log.info("Found %d articles to score using %s", len(articles), OLLAMA_MODEL)

    if not articles:
        log.info("All articles already scored. Rebuilding index...")
        rebuild_company_index(conn)
        print_sentiment_summary(conn)
        conn.close()
        return

    scored_count = 0
    failed_count = 0

    for i, row in enumerate(articles):
        # Use translated text if available, otherwise original
        text = (
            row["translated_snippet"]
            or row["content_snippet"]
            or row["headline"]
            or ""
        ).strip()

        if not text:
            log.debug("Skipping %s — no text content", row["article_id"])
            continue

        log.info("[%d/%d] Scoring %s (%s)",
                 i + 1, len(articles), row["company_name"], row["ticker"])

        try:
            scores = score_article_sentiment(text, row["company_name"], row["ticker"])
            save_article_sentiment(conn, row["article_id"], scores)
            scored_count += 1
            log.info("  → %s (%.2f) | materiality: %s | confidence: %.2f",
                     scores.get("overall_label", "?"),
                     scores.get("overall_sentiment", 0.0),
                     scores.get("materiality", "?"),
                     scores.get("confidence", 0.0))

        except Exception as e:
            log.error("  Scoring failed for %s: %s", row["article_id"], e)
            failed_count += 1

        # Small pause between Ollama calls — local model, but still polite
        time.sleep(0.1)

    log.info("Scoring complete. Scored: %d | Failed: %d", scored_count, failed_count)

    rebuild_company_index(conn)
    print_sentiment_summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Score news article sentiment via local Ollama model"
    )
    parser.add_argument("--ticker",  type=str,  default=None,
                        help="Only score articles for this ticker (e.g. --ticker LLY)")
    parser.add_argument("--limit",   type=int,  default=None,
                        help="Max articles to score in this run (e.g. --limit 20)")
    parser.add_argument("--summary", action="store_true",
                        help="Print the company sentiment index and exit")
    args = parser.parse_args()

    main(ticker_filter=args.ticker, limit=args.limit, show_summary=args.summary)