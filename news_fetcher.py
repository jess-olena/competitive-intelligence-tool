# -*- coding: utf-8 -*-
"""
news_fetcher.py — Phase 3.1: News Ingestion Pipeline
AI for Managers — Competitive Intelligence Briefing Tool

Fetches recent news articles for all 12 tracked companies from NewsAPI,
detects article language, deduplicates by URL, and stores results in SQLite.

Usage:
    python news_fetcher.py               # Fetch all companies
    python news_fetcher.py --ticker AAPL # Fetch one company only
    python news_fetcher.py --dry-run     # Print counts without saving
"""

import argparse
import hashlib
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from langdetect import detect, LangDetectException

from config import settings

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Company Roster ────────────────────────────────────────────────────────────
# 12 companies across 4 sectors. overseas_exposure=True adds regional
# sub-queries (" Asia", " Europe") to catch non-US coverage.
COMPANIES: list[dict] = [
    # Technology
    {"ticker": "AAPL",  "name": "Apple",              "sector": "Technology", "overseas_exposure": True},
    {"ticker": "MSFT",  "name": "Microsoft",           "sector": "Technology", "overseas_exposure": False},
    {"ticker": "NVDA",  "name": "NVIDIA",              "sector": "Technology", "overseas_exposure": True},
	{"ticker": "GOOGL", "name": "Alphabet", "sector": "Technology", "overseas_exposure": True},
    # Energy
    {"ticker": "XOM",   "name": "ExxonMobil",          "sector": "Energy",     "overseas_exposure": True},
    {"ticker": "NEE",   "name": "NextEra Energy",       "sector": "Energy",     "overseas_exposure": False},
    {"ticker": "CVX",   "name": "Chevron",              "sector": "Energy",     "overseas_exposure": True},
]

# Regional suffixes added for companies with significant overseas presence.
REGIONAL_SUFFIXES: list[str] = [" Asia", " Europe"]


# ── Database Setup ────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    """
    Create (or open) the SQLite database and ensure the news_articles table exists.

    Schema:
        article_id       — SHA-256 of the URL (stable, deduplication key)
        ticker           — e.g. "AAPL"
        company_name     — e.g. "Apple"
        sector           — e.g. "Technology"
        headline         — article title
        source_name      — publisher name (e.g. "Reuters")
        source_country   — ISO country code if provided by API, else NULL
        published_at     — ISO 8601 timestamp string
        language         — ISO 639-1 language code detected by langdetect
        url              — canonical article URL
        content_snippet  — first 500 chars of article content/description
        ingested_at      — UTC timestamp of when this row was inserted
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # safer concurrent writes

    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            article_id      TEXT PRIMARY KEY,
            ticker          TEXT NOT NULL,
            company_name    TEXT NOT NULL,
            sector          TEXT NOT NULL,
            headline        TEXT,
            source_name     TEXT,
            source_country  TEXT,
            published_at    TEXT,
            language        TEXT,
            url             TEXT UNIQUE NOT NULL,
            content_snippet TEXT,
            ingested_at     TEXT NOT NULL
        )
    """)
    # Index for fast lookups by ticker and language (used heavily in Phase 3.2)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_ticker   ON news_articles(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_language ON news_articles(language)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_date     ON news_articles(published_at)")
    conn.commit()
    log.info("Database ready: %s", db_path)
    return conn


# ── Language Detection ────────────────────────────────────────────────────────

def detect_language(text: str) -> str:
    """
    Return the ISO 639-1 language code for text (e.g. 'en', 'zh', 'de').
    Falls back to 'unknown' if detection fails or text is too short.
    """
    if not text or len(text.strip()) < 20:
        return "unknown"
    try:
        return detect(text)
    except LangDetectException:
        return "unknown"


# ── Article ID ────────────────────────────────────────────────────────────────

def make_article_id(url: str) -> str:
    """SHA-256 hash of the URL — stable, unique, URL-safe identifier."""
    return hashlib.sha256(url.encode()).hexdigest()


# ── NewsAPI Fetching ──────────────────────────────────────────────────────────

def fetch_articles_for_query(
    query: str,
    from_date: str,
    api_key: str,
    page_size: int = 100,
) -> list[dict]:  # type: ignore[type-arg]
    """
    Call NewsAPI /v2/everything for a single query string.
    Returns a list of raw article dicts from the API.

    Args:
        query:     Search query (e.g. 'Apple OR $AAPL')
        from_date: ISO date string, e.g. '2026-03-14'
        api_key:   Your NewsAPI key
        page_size: Articles per page (max 100 on free tier)
    """
    url = f"{settings.NEWS_API_BASE_URL}/everything"
    params = {
        "q":          query,
        "from":       from_date,
        "language":   "en",          # start with English; we broaden below
        "sortBy":     "publishedAt",
        "pageSize":   page_size,
        "apiKey":     api_key,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            log.warning("NewsAPI returned status '%s' for query '%s': %s",
                        data.get("status"), query, data.get("message", ""))
            return []

        articles = data.get("articles", [])
        log.info("  Query '%s' → %d articles", query, len(articles))
        return articles

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            log.warning("Rate limited by NewsAPI — sleeping 60 s")
            time.sleep(60)
        else:
            log.error("HTTP error for query '%s': %s", query, e)
        return []

    except requests.exceptions.RequestException as e:
        log.error("Request failed for query '%s': %s", query, e)
        return []


def parse_article(raw: dict, ticker: str, company: dict) -> dict | None:
    """
    Convert a raw NewsAPI article dict into our canonical article dict.
    Returns None if the article has no URL (unusable).
    """
    url = (raw.get("url") or "").strip()
    if not url or url == "https://removed.com":
        return None

    # Combine title + description for language detection (more text = more accurate)
    text_for_lang = " ".join(filter(None, [raw.get("title"), raw.get("description")]))
    language = detect_language(text_for_lang)

    # Build a content snippet from description or content (first 500 chars)
    raw_snippet = raw.get("description") or raw.get("content") or ""
    snippet = raw_snippet[:500].strip()

    source = raw.get("source") or {}

    return {
        "article_id":      make_article_id(url),
        "ticker":          ticker,
        "company_name":    company["name"],
        "sector":          company["sector"],
        "headline":        (raw.get("title") or "").strip(),
        "source_name":     source.get("name", ""),
        "source_country":  None,          # NewsAPI free tier doesn't provide this
        "published_at":    raw.get("publishedAt", ""),
        "language":        language,
        "url":             url,
        "content_snippet": snippet,
        "ingested_at":     datetime.now(timezone.utc).isoformat(),
    }


# ── Upsert ────────────────────────────────────────────────────────────────────

def upsert_articles(conn: sqlite3.Connection, articles: list[dict]) -> int:
    """
    Insert articles into news_articles, skipping duplicates by URL.
    Returns the count of newly inserted rows.
    """
    inserted = 0
    for art in articles:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO news_articles
                    (article_id, ticker, company_name, sector, headline,
                     source_name, source_country, published_at, language,
                     url, content_snippet, ingested_at)
                VALUES
                    (:article_id, :ticker, :company_name, :sector, :headline,
                     :source_name, :source_country, :published_at, :language,
                     :url, :content_snippet, :ingested_at)
            """, art)
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except sqlite3.Error as e:
            log.error("DB error inserting article %s: %s", art.get("url"), e)
    conn.commit()
    return inserted


# ── Per-Company Fetch ─────────────────────────────────────────────────────────

def fetch_company_news(
    company: dict,
    api_key: str,
    conn: sqlite3.Connection | None,
    dry_run: bool = False,
) -> int:
    """
    Run all news queries for one company, parse results, and save to DB.

    Queries built:
      1. Primary:  '{company_name} OR ${ticker}'
      2. Regional: '{company_name} Asia', '{company_name} Europe'  (if overseas_exposure=True)

    Returns the total number of new articles inserted.
    """
    ticker = company["ticker"]
    name   = company["name"]
    from_date = (datetime.now(timezone.utc) - timedelta(days=settings.NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    log.info("Fetching news for %s (%s)", name, ticker)

    # Build query list
    queries: list[str] = [f'"{name}" OR ${ticker}']
    if company.get("overseas_exposure"):
        for suffix in REGIONAL_SUFFIXES:
            queries.append(f'"{name}"{suffix}')

    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    for query in queries:
        raw_articles = fetch_articles_for_query(query, from_date, api_key)
        time.sleep(0.5)  # Polite pause between queries

        for raw in raw_articles:
            parsed = parse_article(raw, ticker, company)
            if parsed and parsed["url"] not in seen_urls:
                seen_urls.add(parsed["url"])
                all_articles.append(parsed)

            # Respect per-ticker cap from config
            if len(all_articles) >= settings.NEWS_MAX_ARTICLES_PER_TICKER:
                log.info("  Hit cap of %d articles for %s", settings.NEWS_MAX_ARTICLES_PER_TICKER, ticker)
                break

    log.info("  %s: %d unique articles collected", ticker, len(all_articles))

    if dry_run:
        return len(all_articles)

    inserted = upsert_articles(conn, all_articles)
    log.info("  %s: %d new rows saved to DB (%d already existed)",
             ticker, inserted, len(all_articles) - inserted)
    return inserted


# ── Main ──────────────────────────────────────────────────────────────────────

def main(ticker_filter: str | None = None, dry_run: bool = False) -> None:
    """
    Entry point. Fetches news for all companies (or one if ticker_filter is set).

    Args:
        ticker_filter: If provided, only fetch news for this ticker (e.g. 'AAPL')
        dry_run:       If True, fetch and count but do not write to DB
    """
    api_key = settings.NEWS_API_KEY
    if not api_key:
        raise ValueError(
            "NEWS_API_KEY is not set. Add it to your .env file.\n"
            "  1. Go to https://newsapi.org and sign up (free)\n"
            "  2. Copy your API key\n"
            "  3. Add NEWS_API_KEY=your_key_here to the .env file"
        )

    conn = None if dry_run else init_db(settings.SQLITE_DB_PATH)

    companies = COMPANIES
    if ticker_filter:
        companies = [c for c in COMPANIES if c["ticker"].upper() == ticker_filter.upper()]
        if not companies:
            raise ValueError(f"Ticker '{ticker_filter}' not found in company roster.")

    total_inserted = 0
    for i, company in enumerate(companies):
        inserted = fetch_company_news(company, api_key, conn, dry_run=dry_run)
        total_inserted += inserted
        # Pause between companies to stay well under NewsAPI rate limits
        if i < len(companies) - 1:
            time.sleep(1.0)

    if conn:
        conn.close()

    mode = "DRY RUN —" if dry_run else ""
    log.info("%s Done. Total new articles saved: %d", mode, total_inserted)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch news articles for CI Briefing Tool")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Fetch a single company only (e.g. --ticker AAPL)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count articles without writing to the database")
    args = parser.parse_args()

    main(ticker_filter=args.ticker, dry_run=args.dry_run)
