# -*- coding: utf-8 -*-
"""
briefing_generator.py — Phase 5: Structured Briefing Generator
AI for Managers — Competitive Intelligence Briefing Tool

Generates full markdown competitive intelligence briefings by combining
all context layers (skills, profiles, financials, filing chunks, news,
sentiment) and passing them through the RAG orchestrator.

Briefings are saved to outputs/briefings/ as dated markdown files.

Usage:
    python briefing_generator.py --ticker AAPL
    python briefing_generator.py --ticker AAPL --ticker MSFT --compare
    python briefing_generator.py --sector tech
    python briefing_generator.py --all
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ollama

from config import settings
from context_loader import build_static_context, COMPANY_SECTORS
from rag_orchestrator import (
    classify_query,
    assemble_context,
    get_financial_snapshot,
    format_financials_for_prompt,
    _count_tokens,
    ANALYST_SYSTEM_PROMPT,
    GENERATION_MODEL,
)
from vector_store import (
    get_client,
    get_or_create_collection,
    COLLECTION_FILINGS,
    COLLECTION_NEWS,
    COLLECTION_PROFILES,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Company metadata ──────────────────────────────────────────────────────────
COMPANY_NAMES: dict[str, str] = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "GOOGL": "Alphabet Google",
    "XOM":  "ExxonMobil Corporation",
    "NEE":  "NextEra Energy Inc.",
    "CVX":  "Chevron Corporation",
    "JNJ":  "Johnson & Johnson",
    "LLY":  "Eli Lilly and Company",
    "UNH":  "UnitedHealth Group Inc.",
    "AMZN": "Amazon.com Inc.",
    "WMT":  "Walmart Inc.",
    "MCD":  "McDonald's Corporation",
}

SECTOR_TICKERS: dict[str, list[str]] = {
    "tech":       ["AAPL", "MSFT", "NVDA","GOOGL"],
    "energy":     ["XOM", "NEE", "CVX"],
    "healthcare": ["JNJ", "LLY", "UNH"],
    "consumer":   ["AMZN", "WMT", "MCD"],
}

# ── Briefing system prompt ────────────────────────────────────────────────────
BRIEFING_SYSTEM_PROMPT = """You are a senior equity research analyst producing a formal
competitive intelligence briefing document.

Your briefing must follow this exact structure with these exact markdown headers:

## Executive summary
3 sentences maximum. The most important finding first.

## Company overview
2-3 sentences on the company's business model, market position, and primary revenue drivers.
Reference the company profile context provided.

## Financial performance
Lead with specific numbers. Include revenue, margins, and YoY trends.
Reference the structured financial data provided. Flag any missing data gaps.

## Strategic position and competitive dynamics
Qualitative assessment of competitive moat, key threats, and strategic priorities.
Ground every claim in the filing excerpts provided.

## Key risks
List the 3-5 most material risks. Distinguish between near-term and structural risks.
Cite specific Risk Factors section language where available.

## Global signals and sentiment
Summarize the news sentiment picture. Reference specific articles and their tone.
Note any geographic or regional patterns in coverage.

## Analyst assessment
Confidence level (high / medium / low) and why.
Key data gaps that limit the analysis.
One forward-looking statement grounded in the evidence.

## Sources cited
List every source used: SEC filing date and section, news outlet and date.

RULES:
- Every specific claim must have a source reference in brackets: [AAPL 10-K 2025-10-31]
- Never fabricate numbers. Write N/A if data is missing.
- Keep the total briefing under 800 words.
- Use plain markdown — no HTML, no tables."""


# ── Sentiment summary loader ──────────────────────────────────────────────────

def get_sentiment_summary(ticker: str) -> dict[str, Any]:
    """Pull the company-level sentiment index from SQLite."""
    if not settings.SQLITE_DB_PATH.exists():
        return {}
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT ticker, company_name, avg_sentiment, article_count,
               high_materiality_count, dominant_tone,
               avg_earnings_outlook, avg_competitive,
               avg_regulatory, avg_leadership, avg_innovation
        FROM company_sentiment_index
        WHERE ticker = ?
    """, (ticker.upper(),)).fetchone()
    conn.close()
    return dict(row) if row else {}


def format_sentiment_for_prompt(sentiment: dict[str, Any]) -> str:
    """Format sentiment data into a readable block for prompt injection."""
    if not sentiment:
        return ""
    lines = [f"## Sentiment summary: {sentiment.get('ticker', '')}"]
    avg   = sentiment.get('avg_sentiment')
    lines.append(
        f"Overall tone: {sentiment.get('dominant_tone', 'N/A')} "
        f"(score: {avg:+.3f})" if avg is not None else
        f"Overall tone: {sentiment.get('dominant_tone', 'N/A')}"
    )
    lines.append(f"Articles analyzed: {sentiment.get('article_count', 0)}")
    lines.append(
        f"High-materiality articles: {sentiment.get('high_materiality_count', 0)}"
    )
    topic_map = {
        "avg_earnings_outlook": "Earnings outlook",
        "avg_competitive":      "Competitive position",
        "avg_regulatory":       "Regulatory environment",
        "avg_leadership":       "Leadership/management",
        "avg_innovation":       "Product innovation",
    }
    topic_lines = []
    for key, label in topic_map.items():
        val = sentiment.get(key)
        if val is not None:
            topic_lines.append(f"  {label}: {val:+.3f}")
    if topic_lines:
        lines.append("Topic scores:")
        lines.extend(topic_lines)
    return "\n".join(lines)


# ── Briefing generation ───────────────────────────────────────────────────────

def generate_briefing(
    ticker: str,
    briefing_date: str,
    filings_col: Any,
    news_col: Any,
    profiles_col: Any,
) -> dict[str, Any]:
    """
    Generate a full competitive intelligence briefing for one company.

    Returns a dict with:
        ticker, company_name, briefing_date,
        briefing_text, citations, token_count
    """
    company_name = COMPANY_NAMES.get(ticker, ticker)
    log.info("Generating briefing for %s (%s)...", company_name, ticker)

    # Build the briefing query
    query_text = (
        f"Generate a comprehensive competitive intelligence briefing for "
        f"{company_name} ({ticker}) covering financial performance, "
        f"competitive position, key risks, and recent news signals."
    )

    # Assemble RAG context
    context_string, citations = assemble_context(
        query_text=query_text,
        query_type="single_company_narrative",
        tickers=[ticker],
        filings_col=filings_col,
        news_col=news_col,
        profiles_col=profiles_col,
    )

    # Get structured financials
    snap = get_financial_snapshot(ticker, n_quarters=4)
    fin_text = format_financials_for_prompt(snap)
    if fin_text:
        context_string = fin_text + "\n\n" + context_string
        citations.append({
            "type":   "financial_db",
            "ticker": ticker,
            "source": "SEC EDGAR XBRL (SQLite financial_snapshots)",
        })

    # Get sentiment summary
    sentiment = get_sentiment_summary(ticker)
    sent_text = format_sentiment_for_prompt(sentiment)
    if sent_text:
        context_string = context_string + "\n\n" + sent_text

    # Build user message
    user_message = (
        f"Briefing date: {briefing_date}\n"
        f"Company: {company_name} ({ticker})\n\n"
        f"Context:\n{context_string}\n\n"
        f"{'─' * 60}\n\n"
        f"Generate the full competitive intelligence briefing now. "
        f"Follow the required structure exactly. "
        f"Ground every claim in the context provided above."
    )

    token_count = _count_tokens(BRIEFING_SYSTEM_PROMPT + user_message)
    log.info("Briefing context: %d tokens | %d citations", token_count, len(citations))

    # Generate
    response = ollama.chat(
        model=GENERATION_MODEL,
        messages=[
            {"role": "system", "content": BRIEFING_SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        options={"temperature": 0.2, "num_predict": 2000},
    )
    briefing_text = response["message"]["content"].strip()

    return {
        "ticker":        ticker,
        "company_name":  company_name,
        "briefing_date": briefing_date,
        "briefing_text": briefing_text,
        "citations":     citations,
        "token_count":   token_count,
    }


def generate_comparison_briefing(
    tickers: list[str],
    briefing_date: str,
    filings_col: Any,
    news_col: Any,
    profiles_col: Any,
) -> dict[str, Any]:
    """
    Generate a comparative briefing across two or more companies.
    """
    names = [COMPANY_NAMES.get(t, t) for t in tickers]
    log.info("Generating comparative briefing for %s...", " vs ".join(tickers))

    query_text = (
        f"Compare {' and '.join(names)} across financial performance, "
        f"competitive strategy, key risks, and market positioning."
    )

    context_string, citations = assemble_context(
        query_text=query_text,
        query_type="comparative_narrative",
        tickers=tickers,
        filings_col=filings_col,
        news_col=news_col,
        profiles_col=profiles_col,
    )

    # Add financials and sentiment for each company
    extra_blocks: list[str] = []
    for ticker in tickers:
        snap = get_financial_snapshot(ticker, n_quarters=2)
        fin_text = format_financials_for_prompt(snap)
        if fin_text:
            extra_blocks.append(fin_text)
            citations.append({
                "type":   "financial_db",
                "ticker": ticker,
                "source": "SEC EDGAR XBRL (SQLite financial_snapshots)",
            })
        sentiment = get_sentiment_summary(ticker)
        sent_text = format_sentiment_for_prompt(sentiment)
        if sent_text:
            extra_blocks.append(sent_text)

    if extra_blocks:
        context_string = "\n\n".join(extra_blocks) + "\n\n" + context_string

    comparison_system = BRIEFING_SYSTEM_PROMPT.replace(
        "## Company overview\n2-3 sentences on the company's business model",
        "## Company overviews\nOne paragraph per company covering business model",
    )

    user_message = (
        f"Briefing date: {briefing_date}\n"
        f"Comparison: {' vs '.join(names)}\n\n"
        f"Context:\n{context_string}\n\n"
        f"{'─' * 60}\n\n"
        f"Generate a comparative competitive intelligence briefing. "
        f"Structure each section to directly compare the companies side by side. "
        f"Ground every claim in the context above."
    )

    token_count = _count_tokens(comparison_system + user_message)
    log.info("Comparison context: %d tokens | %d citations", token_count, len(citations))

    response = ollama.chat(
        model=GENERATION_MODEL,
        messages=[
            {"role": "system", "content": comparison_system},
            {"role": "user",   "content": user_message},
        ],
        options={"temperature": 0.2, "num_predict": 2500},
    )
    briefing_text = response["message"]["content"].strip()

    return {
        "ticker":        "_vs_".join(tickers),
        "company_name":  " vs ".join(names),
        "briefing_date": briefing_date,
        "briefing_text": briefing_text,
        "citations":     citations,
        "token_count":   token_count,
    }


# ── Save and display ──────────────────────────────────────────────────────────

def save_briefing(result: dict[str, Any]) -> Path:
    """
    Save a briefing result to outputs/briefings/ as a markdown file.
    Returns the path of the saved file.
    """
    briefings_dir = settings.BRIEFINGS_DIR
    briefings_dir.mkdir(parents=True, exist_ok=True)

    date_str = result["briefing_date"].replace("-", "")
    filename = f"{date_str}_{result['ticker']}_briefing.md"
    output_path = briefings_dir / filename

    # Build full markdown document
    lines = [
        f"# Competitive Intelligence Briefing: {result['company_name']}",
        f"**Date:** {result['briefing_date']}  ",
        f"**Generated by:** CI Briefing Tool (Ollama/{GENERATION_MODEL})  ",
        f"**Context tokens:** {result['token_count']:,}",
        "",
        "---",
        "",
        result["briefing_text"],
        "",
        "---",
        "",
        "## Sources cited",
        "",
    ]

    # Deduplicated citations
    seen: set[str] = set()
    for c in result["citations"]:
        if c["type"] == "sec_filing":
            line = f"- [{c['ticker']}] {c.get('form_type','')} — {c.get('section','')} ({c.get('date','')})"
        elif c["type"] == "news_article":
            sentiment = f" — {c['sentiment']}" if c.get("sentiment") else ""
            line = f"- [{c['ticker']}] {c.get('source','')} ({c.get('date','')}){sentiment}"
        else:
            line = f"- [{c['ticker']}] {c.get('source','')}"
        if line not in seen:
            lines.append(line)
            seen.add(line)

    output_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Briefing saved: %s", output_path)
    return output_path


def print_briefing(result: dict[str, Any]) -> None:
    """Print a briefing result to the terminal."""
    print(f"\n{'═' * 70}")
    print(f"  {result['company_name']} — {result['briefing_date']}")
    print(f"  Tokens: {result['token_count']:,} | Citations: {len(result['citations'])}")
    print(f"{'═' * 70}\n")
    print(result["briefing_text"])
    print(f"\n{'─' * 70}")
    print("Sources:")
    seen: set[str] = set()
    for c in result["citations"]:
        if c["type"] == "sec_filing":
            line = f"  [{c['ticker']}] {c.get('form_type','')} {c.get('section','')} ({c.get('date','')})"
        elif c["type"] == "news_article":
            line = f"  [{c['ticker']}] {c.get('source','')} ({c.get('date','')})"
        else:
            line = f"  [{c['ticker']}] {c.get('source','')}"
        if line not in seen:
            print(line)
            seen.add(line)
    print(f"{'═' * 70}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate competitive intelligence briefings"
    )
    parser.add_argument("--ticker", type=str, action="append", default=[],
                        help="Ticker to brief (repeat for multiple: --ticker AAPL --ticker MSFT)")
    parser.add_argument("--compare", action="store_true",
                        help="Generate a comparative briefing across tickers")
    parser.add_argument("--sector", type=str, default=None,
                        choices=["tech", "energy", "healthcare", "consumer"],
                        help="Generate briefings for all companies in a sector")
    parser.add_argument("--all", action="store_true",
                        help="Generate briefings for all available companies")
    parser.add_argument("--no-save", action="store_true",
                        help="Print to terminal only, do not save to file")
    args = parser.parse_args()

    # Determine which tickers to process
    tickers: list[str] = [t.upper() for t in args.ticker]

    if args.sector:
        tickers = SECTOR_TICKERS.get(args.sector, [])
        log.info("Sector mode: %s → %s", args.sector, tickers)

    if args.all:
        tickers = [t for t in COMPANY_NAMES if
                   (settings.PROFILES_DIR / f"{t}.md").exists()]
        log.info("All mode: %d companies with profiles", len(tickers))

    if not tickers:
        parser.print_help()
        return

    # Connect to vector store once
    client       = get_client()
    filings_col  = get_or_create_collection(client, COLLECTION_FILINGS)
    news_col     = get_or_create_collection(client, COLLECTION_NEWS)
    profiles_col = get_or_create_collection(client, COLLECTION_PROFILES)

    briefing_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.compare and len(tickers) >= 2:
        # Single comparative briefing
        result = generate_comparison_briefing(
            tickers, briefing_date, filings_col, news_col, profiles_col
        )
        print_briefing(result)
        if not args.no_save:
            save_briefing(result)
    else:
        # Individual briefing per ticker
        for ticker in tickers:
            if ticker not in COMPANY_NAMES:
                log.warning("Unknown ticker %s — skipping", ticker)
                continue
            result = generate_briefing(
                ticker, briefing_date, filings_col, news_col, profiles_col
            )
            print_briefing(result)
            if not args.no_save:
                path = save_briefing(result)
                print(f"Saved: {path}\n")


if __name__ == "__main__":
    main()