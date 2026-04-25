# -*- coding: utf-8 -*-
"""
sentiment_scorer.py — Phase 3.3: Sentiment Scoring Pipeline
AI for Managers — Competitive Intelligence Briefing Tool

Scores each news article on two levels:
  1. Per-article sentiment  — overall score + 5 financial topic scores
  2. Company-level index    — aggregated sentiment per ticker (stored in DB)

The scores feed directly into Phase 5 briefing generation as the
"Global signals & sentiment" section.

Usage:
    python sentiment_scorer.py              # Score all unscored articles
    python sentiment_scorer.py --ticker LLY # Score one company only
    python sentiment_scorer.py --limit 20   # Score a batch for testing
    python sentiment_scorer.py --summary    # Print company-level sentiment table
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


# ── Sentiment Prompt ──────────────────────────────────────────────────────────
# System prompt instructs Claude to act as a financial sentiment analyst.
# The JSON-only output format is intentional — makes parsing reliable.

SENTIMENT_SYSTEM_PROMPT = """You are a financial sentiment analyst specializing in equity research.
Your task is to analyze news articles about publicly traded companies and score their sentiment.

Return ONLY a valid JSON object — no preamble, no explanation, no markdown code fences.
The JSON must strictly follow this schema:

{
  "overall_sentiment": <float from -1.0 (very negative) to 1.0 (very positive)>,
  "overall_label": "very_negative" | "negative" | "neutral" | "positive" | "very_positive",
  "confidence": <float from 0.0 (uncertain) to 1.0 (very confident)>,
  "topic_scores": {
    "earnings_outlook":       <null or float -1.0 to 1.0>,
    "competitive_position":   <null or float -1.0 to 1.0>,
    "regulatory_environment": <null or float -1.0 to 1.0>,
    "leadership_management":  <null or float -1.0 to 1.0>,
    "product_innovation":     <null or float -1.0 to 1.0>
  },
  "key_signals": ["<2-3 specific phrases or facts that most influenced the score>"],
  "forward_looking": <true if article discusses future expectations, false if purely retrospective>,
  "materiality": "high" | "medium" | "low"
}

SCORING GUIDELINES:
- overall_sentiment: Use the full -1.0 to 1.0 range. Reserve ±0.8 to ±1.0 for major events
  (earnings miss, leadership scandal, blockbuster drug approval, antitrust ruling).
- confidence: Lower confidence when article is vague, opinion-heavy, or very short.
- topic_scores: Set to null if the article does not address that topic at all.
- materiality: "high" = could move stock price; "medium" = relevant background;
  "low" = routine/noise.
- forward_looking: true if the article makes predictions or discusses guidance."""


def build_sentiment_user_prompt(article_text: str, company_name: str, ticker: str) -> str:
    """Build the per-article sentiment analysis prompt."""
    return (
        f"Company: {company_name} (${ticker})\n\n"
        f"Article:\n{article_text}"
    )


# ── Claude Sentiment Call ─────────────────────────────────────────────────────

def score_article_sentiment(
    client: anthropic.Anthropic,
    article_text: str,
    company_name: str,
    ticker: str,
) -> dict:
    """
    Call Claude to score the sentiment of one article.
    Returns the parsed sentiment dict, or a default neutral dict on error.
    """
    user_prompt = build_sentiment_user_prompt(article_text, company_name, ticker)

    response = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=512,
        temperature=0.1,    # Deterministic scoring
        system=SENTIMENT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = response.content[0].text.strip()

    # Strip accidental markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        log.warning("Could not parse sentiment JSON — returning neutral defaults")
        return _neutral_sentiment()


def _neutral_sentiment() -> dict:
    """Fallback neutral sentiment when parsing fails."""
    return {
        "overall_sentiment": 0.0,
        "overall_label": "neutral",
        "confidence": 0.0,
        "topic_scores": {
            "earnings_outlook": None,
            "competitive_position": None,
            "regulatory_environment": None,
            "leadership_management": None,
            "product_innovation": None,
        },
        "key_signals": [],
        "forward_looking": False,
        "materiality": "low",
    }


# ── Database Helpers ──────────────────────────────────────────────────────────

def open_db(db_path: Path) -> sqlite3.Connection:
    """Open the SQLite database and add sentiment columns if missing."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}.\n"
            "Run news_fetcher.py first to populate the database."
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Add per-article sentiment columns (safe migration)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(news_articles)")}
    new_cols = {
        "sentiment_score":           "REAL",
        "sentiment_label":           "TEXT",
        "sentiment_confidence":      "REAL",
        "sentiment_materiality":     "TEXT",
        "sentiment_forward_looking": "INTEGER",   # SQLite boolean = 0/1
        "sentiment_key_signals":     "TEXT",       # JSON array stored as text
        "topic_earnings":            "REAL",
        "topic_competitive":         "REAL",
        "topic_regulatory":          "REAL",
        "topic_leadership":          "REAL",
        "topic_innovation":          "REAL",
        "scored_at":                 "TEXT",
    }
    for col, col_type in new_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE news_articles ADD COLUMN {col} {col_type}")

    # Company-level sentiment index table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS company_sentiment_index (
            ticker               TEXT PRIMARY KEY,
            company_name         TEXT NOT NULL,
            sector               TEXT NOT NULL,
            avg_sentiment        REAL,
            article_count        INTEGER,
            high_materiality_count INTEGER,
            avg_earnings_outlook REAL,
            avg_competitive      REAL,
            avg_regulatory       REAL,
            avg_leadership       REAL,
            avg_innovation       REAL,
            dominant_tone        TEXT,
            last_updated         TEXT
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


def save_article_sentiment(conn: sqlite3.Connection, article_id: str, scores: dict) -> None:
    """Write per-article sentiment scores back to the DB."""
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
    """
    Aggregate per-article scores into a company-level sentiment index.
    Run after all articles are scored to produce the summary used in Phase 5.
    """
    rows = conn.execute("""
        SELECT ticker, company_name, sector,
               AVG(sentiment_score)                                AS avg_sentiment,
               COUNT(*)                                            AS article_count,
               SUM(CASE WHEN sentiment_materiality='high' THEN 1 ELSE 0 END) AS high_mat,
               AVG(topic_earnings)                                 AS avg_earnings,
               AVG(topic_competitive)                              AS avg_competitive,
               AVG(topic_regulatory)                               AS avg_regulatory,
               AVG(topic_leadership)                               AS avg_leadership,
               AVG(topic_innovation)                               AS avg_innovation
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
            "ticker":        row["ticker"],
            "company_name":  row["company_name"],
            "sector":        row["sector"],
            "avg_sentiment": round(avg, 4),
            "article_count": row["article_count"],
            "high_mat":      row["high_mat"],
            "avg_earnings":  row["avg_earnings"],
            "avg_competitive": row["avg_competitive"],
            "avg_regulatory":  row["avg_regulatory"],
            "avg_leadership":  row["avg_leadership"],
            "avg_innovation":  row["avg_innovation"],
            "tone":          tone,
            "updated":       datetime.now(timezone.utc).isoformat(),
        })
    conn.commit()
    log.info("Company sentiment index rebuilt for %d tickers", len(rows))


def print_sentiment_summary(conn: sqlite3.Connection) -> None:
    """Print a formatted table of company-level sentiment scores to stdout."""
    rows = conn.execute("""
        SELECT ticker, company_name, sector, avg_sentiment, article_count,
               high_materiality_count, dominant_tone, last_updated
        FROM company_sentiment_index
        ORDER BY avg_sentiment DESC
    """).fetchall()

    if not rows:
        print("No sentiment data yet. Run sentiment_scorer.py first.")
        return

    print(f"\n{'─'*85}")
    print(f"{'TICKER':<8} {'COMPANY':<25} {'SECTOR':<12} {'AVG SCORE':>10} {'ARTICLES':>9} {'HIGH-MAT':>9} {'TONE':<10}")
    print(f"{'─'*85}")
    for r in rows:
        score = r["avg_sentiment"]
        score_str = f"{score:+.3f}" if score is not None else "  N/A "
        print(f"{r['ticker']:<8} {r['company_name']:<25} {r['sector']:<12} "
              f"{score_str:>10} {r['article_count']:>9} {r['high_materiality_count']:>9} "
              f"{r['dominant_tone']:<10}")
    print(f"{'─'*85}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(
    ticker_filter: str | None = None,
    limit: int | None = None,
    show_summary: bool = False,
) -> None:
    """
    Score all unscored articles, then rebuild the company sentiment index.

    Args:
        ticker_filter: Only score articles for this ticker (e.g. 'LLY')
        limit:         Max articles to score in this run
        show_summary:  Print company-level sentiment table and exit
    """
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    conn   = open_db(settings.SQLITE_DB_PATH)

    if show_summary:
        print_sentiment_summary(conn)
        conn.close()
        return

    articles = get_unscored_articles(conn, ticker=ticker_filter, limit=limit)
    log.info("Found %d articles to score", len(articles))

    if not articles:
        log.info("All articles already scored. Use --summary to view results.")
        rebuild_company_index(conn)
        print_sentiment_summary(conn)
        conn.close()
        return

    scored_count  = 0
    failed_count  = 0

    for i, row in enumerate(articles):
        # Use translated text if available (non-English articles), else original
        text = (
            row["translated_snippet"]
            or row["content_snippet"]
            or row["headline"]
            or ""
        )
        if not text.strip():
            log.debug("Skipping article %s — no text content", row["article_id"])
            continue

        log.info("[%d/%d] Scoring %s (%s)", i + 1, len(articles), row["company_name"], row["ticker"])

        try:
            scores = score_article_sentiment(client, text, row["company_name"], row["ticker"])
            save_article_sentiment(conn, row["article_id"], scores)
            scored_count += 1
            log.info("  → %s (%.2f) | materiality: %s",
                     scores.get("overall_label", "?"),
                     scores.get("overall_sentiment", 0.0),
                     scores.get("materiality", "?"))

        except anthropic.RateLimitError:
            log.warning("  Rate limited — sleeping 30 s")
            time.sleep(30)
            failed_count += 1

        except Exception as e:
            log.error("  Scoring failed for %s: %s", row["article_id"], e)
            failed_count += 1

        time.sleep(0.3)   # Pause between Claude calls

    log.info("Scoring complete. Scored: %d | Failed: %d", scored_count, failed_count)

    # Rebuild the company-level index with the latest scores
    rebuild_company_index(conn)
    print_sentiment_summary(conn)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Score news article sentiment via Claude")
    parser.add_argument("--ticker",  type=str,  default=None,
                        help="Only score articles for this ticker (e.g. --ticker LLY)")
    parser.add_argument("--limit",   type=int,  default=None,
                        help="Max articles to score in this run (e.g. --limit 20)")
    parser.add_argument("--summary", action="store_true",
                        help="Print the company sentiment index and exit")
    args = parser.parse_args()

    main(ticker_filter=args.ticker, limit=args.limit, show_summary=args.summary)
