# -*- coding: utf-8 -*-
"""
rag_orchestrator.py — Phase 4/5: Core RAG Query Engine
AI for Managers — Competitive Intelligence Briefing Tool

Classifies incoming queries, retrieves the right context from ChromaDB
and SQLite, assembles a token-budgeted prompt, and generates analyst-grade
answers using a local Ollama model.

Query types handled:
    single_company_narrative   — "What are Apple's biggest risks?"
    single_company_financial   — "Show me Apple's revenue trend"
    comparative_narrative      — "Compare NVIDIA and AMD's AI strategy"
    comparative_financial      — "How do Apple and Microsoft margins compare?"
    sector_overview            — "What's happening in the energy sector?"

Usage:
    python rag_orchestrator.py
    (launches an interactive query REPL)

Programmatic:
    from rag_orchestrator import query
    result = query("What are Apple's biggest risks?", tickers=["AAPL"])
    print(result["answer"])
    print(result["citations"])
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import chromadb
import ollama
import tiktoken

from config import settings
from context_loader import build_static_context, COMPANY_SECTORS
from vector_store import (
    get_client,
    get_or_create_collection,
    retrieve,
    retrieve_multi_company,
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

# ── Models ────────────────────────────────────────────────────────────────────
CLASSIFIER_MODEL  = "llama3.1:8b"   # fast, used only for classification
GENERATION_MODEL  = "llama3.1:8b"   # used for full answer generation

# ── Token budget (cl100k_base approximation) ──────────────────────────────────
ENCODER              = tiktoken.get_encoding("cl100k_base")
MAX_CONTEXT_TOKENS   = 6_000    # safe limit for llama3.1:8b (8k context window)
SYSTEM_PROMPT_BUDGET = 600      # reserved for analyst system prompt
SKILLS_BUDGET        = 800      # reserved for sector skills file(s)
PROFILES_BUDGET      = 800    # reserved for company profile(s)
FINANCIALS_BUDGET    = 400      # reserved for structured financial snapshot
CHUNKS_BUDGET        = MAX_CONTEXT_TOKENS - (
    SYSTEM_PROMPT_BUDGET + SKILLS_BUDGET + PROFILES_BUDGET + FINANCIALS_BUDGET
)                               # remaining tokens go to retrieved chunks

# ── Query types ───────────────────────────────────────────────────────────────
QUERY_TYPES = [
    "single_company_narrative",
    "single_company_financial",
    "comparative_narrative",
    "comparative_financial",
    "sector_overview",
]

# ── Analyst system prompt ─────────────────────────────────────────────────────
ANALYST_SYSTEM_PROMPT = """You are a senior equity research analyst with 15 years of experience
covering multiple sectors. You produce concise, data-grounded competitive intelligence briefings.

REASONING APPROACH (follow this order for every response):
1. Identify the core question being asked.
2. Identify which data in the provided context is most relevant.
3. Note any data gaps or limitations explicitly.
4. Synthesize a direct, evidence-based answer.

OUTPUT FORMAT:
## Executive summary
3 sentences maximum. Lead with the most important finding.

## Key findings
Bullet points. Every claim must reference a specific source (filing date, article headline,
or financial figure). Use numbers wherever available.

## Risks and opportunities
Two clearly labeled sub-sections. Be specific — avoid generic statements.

## Analyst note
One paragraph. State your confidence level and flag any data gaps or caveats.

GUARDRAILS:
- Distinguish clearly between what the filing STATES and what it IMPLIES.
- Never fabricate specific numbers. If a figure is not in the provided context, say so.
- When comparing companies, always normalize for sector and size differences.
- Flag when you are extrapolating beyond the data provided."""


# ── Step 1: Query classifier ──────────────────────────────────────────────────

def classify_query(query_text: str, tickers: list[str]) -> str:
    """
    Use Ollama to classify the query into one of five types.
    Falls back to 'single_company_narrative' if classification fails.

    Args:
        query_text: The user's natural language question.
        tickers:    List of ticker symbols mentioned or inferred.

    Returns:
        One of the five QUERY_TYPES strings.
    """
    ticker_context = (
        f"Companies mentioned: {', '.join(tickers)}" if tickers
        else "No specific companies mentioned."
    )
    n_companies = len(set(tickers)) if tickers else 0

    classification_prompt = f"""Classify this financial analysis query into exactly one category.

Query: "{query_text}"
{ticker_context}
Number of distinct companies: {n_companies}

Categories:
- single_company_narrative: Questions about one company's strategy, risks, competitive position,
  business model, or qualitative aspects (e.g. "What are Apple's biggest risks?")
- single_company_financial: Questions about one company's financial metrics, revenue, margins,
  earnings, or numerical performance (e.g. "Show Apple's revenue trend")
- comparative_narrative: Qualitative comparison of two or more companies
  (e.g. "Compare NVIDIA and Microsoft's AI strategy")
- comparative_financial: Numerical/financial comparison of two or more companies
  (e.g. "How do Apple and Microsoft margins compare?")
- sector_overview: Broad sector or industry questions without focus on specific companies
  (e.g. "What's happening in the energy sector?")

Rules:
- If 2+ companies AND financial metrics → comparative_financial
- If 2+ companies AND strategy/risk/qualitative → comparative_narrative
- If 1 company AND financial metrics → single_company_financial
- If 1 company AND strategy/risk/qualitative → single_company_narrative
- If no specific company → sector_overview

Respond with ONLY the category name, nothing else."""

    try:
        response = ollama.chat(
            model=CLASSIFIER_MODEL,
            messages=[{"role": "user", "content": classification_prompt}],
            options={"temperature": 0.0, "num_predict": 20},
        )
        raw = response["message"]["content"].strip().lower()

        # Extract the category from the response
        for qt in QUERY_TYPES:
            if qt in raw:
                log.info("Query classified as: %s", qt)
                return qt

        # If no exact match, infer from keywords
        if n_companies >= 2:
            result = "comparative_narrative"
        elif n_companies == 1:
            result = "single_company_narrative"
        else:
            result = "sector_overview"

        log.warning("Could not parse classification '%s' — inferred: %s", raw, result)
        return result

    except Exception as e:
        log.error("Classification failed: %s — defaulting to single_company_narrative", e)
        return "single_company_narrative"


# ── Step 2: Financial data retrieval ─────────────────────────────────────────

def get_financial_snapshot(ticker: str, n_quarters: int = 4) -> dict[str, Any]:
    """
    Pull the most recent quarterly financial snapshots from SQLite.
    Returns a dict ready for prompt injection.
    """
    if not settings.SQLITE_DB_PATH.exists():
        return {}

    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT ticker, period_end, fiscal_period, form_type,
               revenue_m, net_income_m, gross_profit_m, operating_income_m,
               eps_diluted, gross_margin, operating_margin
        FROM financial_snapshots
        WHERE ticker = ?
          AND form_type = '10-Q'
        ORDER BY period_end DESC
        LIMIT ?
    """, (ticker.upper(), n_quarters)).fetchall()

    # Also grab most recent annual
    annual = conn.execute("""
        SELECT ticker, period_end, fiscal_period, form_type,
               revenue_m, net_income_m, gross_profit_m, operating_income_m,
               eps_diluted, gross_margin, operating_margin
        FROM financial_snapshots
        WHERE ticker = ?
          AND form_type = '10-K'
        ORDER BY period_end DESC
        LIMIT 1
    """, (ticker.upper(),)).fetchone()

    conn.close()

    quarters = [dict(r) for r in rows]
    return {
        "ticker":         ticker.upper(),
        "annual":         dict(annual) if annual else {},
        "recent_quarters": quarters,
    }


def format_financials_for_prompt(snapshots: dict[str, Any]) -> str:
    """
    Format financial snapshot data into a compact, readable string
    for injection into the prompt context.
    """
    if not snapshots:
        return ""

    ticker = snapshots.get("ticker", "")
    lines  = [f"## Structured financials: {ticker}"]

    annual = snapshots.get("annual", {})
    if annual:
        rev = annual.get('revenue_m')
        ni  = annual.get('net_income_m')
        gm  = annual.get('gross_margin')
        om  = annual.get('operating_margin')
        eps = annual.get('eps_diluted')

        rev_str = f"${rev:,.0f}M"   if rev is not None else "N/A"
        ni_str  = f"${ni:,.0f}M"    if ni  is not None else "N/A"
        gm_str  = f"{gm*100:.1f}%"  if gm  is not None else "N/A"
        om_str  = f"{om*100:.1f}%"  if om  is not None else "N/A"
        eps_str = f"${eps:.2f}"     if eps is not None else "N/A"

        lines.append(
            f"\nMost recent annual ({annual.get('period_end', '')}):\n"
            f"  Revenue:          {rev_str}\n"
            f"  Net income:       {ni_str}\n"
            f"  Gross margin:     {gm_str}\n"
            f"  Operating margin: {om_str}\n"
            f"  EPS (diluted):    {eps_str}"
        )

    quarters = snapshots.get("recent_quarters", [])
    if quarters:
        lines.append("\nRecent quarters (most recent first):")
        for q in quarters:
            rev = q.get("revenue_m")
            gm  = q.get("gross_margin")
            rev_str = f"${rev:,.0f}M"   if rev is not None else "N/A"
            gm_str  = f"{gm*100:.1f}%"  if gm  is not None else "N/A"
            lines.append(
                f"  {q.get('period_end', '')} "
                f"({q.get('fiscal_period', '')}): "
                f"Rev {rev_str} | GM {gm_str}"
            )

    return "\n".join(lines)

# ── Step 3: Context assembly ──────────────────────────────────────────────────

def _count_tokens(text: str) -> int:
    """Count tokens using the cl100k_base encoder."""
    return len(ENCODER.encode(text))


def _truncate_to_budget(text: str, budget: int) -> str:
    """
    Truncate text to fit within a token budget.
    Cuts at paragraph boundaries where possible.
    """
    if _count_tokens(text) <= budget:
        return text

    # Try paragraph-boundary truncation first
    paragraphs = text.split("\n\n")
    kept: list[str] = []
    used = 0
    for para in paragraphs:
        para_tokens = _count_tokens(para)
        if used + para_tokens > budget:
            break
        kept.append(para)
        used += para_tokens

    if kept:
        return "\n\n".join(kept) + "\n\n[... truncated to fit context window ...]"

    # Last resort: character-level truncation
    approx_chars = budget * 3
    return text[:approx_chars] + "\n\n[... truncated ...]"


def assemble_context(
    query_text: str,
    query_type: str,
    tickers: list[str],
    filings_col: chromadb.Collection,
    news_col: chromadb.Collection,
    profiles_col: chromadb.Collection,
) -> tuple[str, list[dict]]:
    """
    Assemble the full context string and citations list for a query.

    Strategy by query type:
        narrative      → filing chunks + news + profiles + skills
        financial      → structured DB data + filing chunks + profiles + skills
        comparative    → per-company chunks from both filings and news
        sector_overview → sector skills + broad retrieval across all tickers

    Returns:
        (context_string, citations_list)
    """
    citations:      list[dict] = []
    context_parts:  list[str]  = []

    # ── Static knowledge: skills + profiles ───────────────────────────────
    static = build_static_context(tickers if tickers else [])

    skills_text   = _truncate_to_budget(static.get("skills",   ""), SKILLS_BUDGET)
    profiles_text = _truncate_to_budget(static.get("profiles", ""), PROFILES_BUDGET)

    if skills_text:
        context_parts.append(skills_text)
    if profiles_text:
        context_parts.append(profiles_text)

    # ── Structured financials (financial queries) ─────────────────────────
    financials_text = ""
    if query_type in ("single_company_financial", "comparative_financial"):
        fin_blocks: list[str] = []
        for ticker in tickers:
            snap = get_financial_snapshot(ticker)
            if snap:
                fin_blocks.append(format_financials_for_prompt(snap))
                citations.append({
                    "type":   "financial_db",
                    "ticker": ticker,
                    "source": "SEC EDGAR XBRL (SQLite financial_snapshots)",
                })
        financials_text = "\n\n".join(fin_blocks)
        financials_text = _truncate_to_budget(financials_text, FINANCIALS_BUDGET)
        if financials_text:
            context_parts.append(financials_text)

    # ── Retrieved chunks budget ───────────────────────────────────────────
    used_tokens = sum(_count_tokens(p) for p in context_parts)
    remaining   = max(0, CHUNKS_BUDGET - used_tokens)
    filing_budget = int(remaining * 0.65)   # 65% to filings
    news_budget   = int(remaining * 0.35)   # 35% to news

    # ── Filing chunks ─────────────────────────────────────────────────────
    filing_chunks: list[dict] = []

    if query_type in ("single_company_narrative", "single_company_financial"):
        # Single company: retrieve with optional section filter
        section = "Item 7 – MD&A" if "financial" in query_type else None
        for ticker in tickers[:1]:
            filing_chunks = retrieve(
                query_text, filings_col,
                ticker=ticker, section=section,
                n_results=settings.CHROMA_N_RESULTS,
            )

    elif query_type in ("comparative_narrative", "comparative_financial"):
        # Comparative: retrieve per company, interleave results
        multi = retrieve_multi_company(
            query_text, tickers, filings_col,
            n_results_per_company=4,
        )
        for ticker_chunks in multi.values():
            filing_chunks.extend(ticker_chunks)

    elif query_type == "sector_overview":
        # Sector: retrieve across all tickers in the relevant sector
        sector_tickers = [
            t for t, s in COMPANY_SECTORS.items()
            if tickers and COMPANY_SECTORS.get(tickers[0]) == s
        ] if tickers else list(COMPANY_SECTORS.keys())
        multi = retrieve_multi_company(
            query_text, sector_tickers[:6], filings_col,
            n_results_per_company=2,
        )
        for ticker_chunks in multi.values():
            filing_chunks.extend(ticker_chunks)

    # Build filing context block within budget
    filing_block_parts: list[str] = []
    filing_tokens_used = 0
    for chunk in filing_chunks:
        chunk_text = (
            f"[{chunk['metadata'].get('ticker','')} | "
            f"{chunk['metadata'].get('section_name','')} | "
            f"{chunk['metadata'].get('filing_date','')}]\n"
            f"{chunk['content']}"
        )
        chunk_tokens = _count_tokens(chunk_text)
        if filing_tokens_used + chunk_tokens > filing_budget:
            break
        filing_block_parts.append(chunk_text)
        filing_tokens_used += chunk_tokens
        citations.append({
            "type":       "sec_filing",
            "ticker":     chunk["metadata"].get("ticker", ""),
            "section":    chunk["metadata"].get("section_name", ""),
            "date":       chunk["metadata"].get("filing_date", ""),
            "form_type":  chunk["metadata"].get("source_type", ""),
            "distance":   round(chunk.get("distance", 0), 3),
        })

    if filing_block_parts:
        context_parts.append(
            "## SEC filing excerpts\n\n" + "\n\n---\n\n".join(filing_block_parts)
        )

    # ── News chunks ───────────────────────────────────────────────────────
    news_chunks: list[dict] = []

    if query_type == "sector_overview":
        news_tickers = sector_tickers[:6] if "sector_tickers" in dir() else []
        for ticker in news_tickers[:3]:
            news_chunks.extend(retrieve(
                query_text, news_col, ticker=ticker, n_results=2,
            ))
    elif tickers:
        if query_type in ("comparative_narrative", "comparative_financial"):
            multi_news = retrieve_multi_company(
                query_text, tickers, news_col, n_results_per_company=2,
            )
            for ticker_news in multi_news.values():
                news_chunks.extend(ticker_news)
        else:
            news_chunks = retrieve(
                query_text, news_col,
                ticker=tickers[0] if tickers else None,
                n_results=6,
            )

    # Build news context block within budget
    news_block_parts: list[str] = []
    news_tokens_used = 0
    for chunk in news_chunks:
        meta = chunk["metadata"]
        sentiment = meta.get("sentiment_label", "")
        sentiment_str = f" | sentiment: {sentiment}" if sentiment else ""
        chunk_text = (
            f"[{meta.get('ticker','')} | "
            f"{meta.get('source_name','')} | "
            f"{meta.get('published_at','')[:10]}"
            f"{sentiment_str}]\n"
            f"{chunk['content']}"
        )
        chunk_tokens = _count_tokens(chunk_text)
        if news_tokens_used + chunk_tokens > news_budget:
            break
        news_block_parts.append(chunk_text)
        news_tokens_used += chunk_tokens
        citations.append({
            "type":      "news_article",
            "ticker":    meta.get("ticker", ""),
            "source":    meta.get("source_name", ""),
            "date":      meta.get("published_at", "")[:10],
            "sentiment": meta.get("sentiment_label", ""),
            "distance":  round(chunk.get("distance", 0), 3),
        })

    if news_block_parts:
        context_parts.append(
            "## Recent news\n\n" + "\n\n---\n\n".join(news_block_parts)
        )

    # ── Final assembly ────────────────────────────────────────────────────
    context_string = "\n\n" + ("=" * 60) + "\n\n".join(context_parts)

    total_tokens = _count_tokens(ANALYST_SYSTEM_PROMPT + context_string + query_text)
    log.info(
        "Context assembled: %d tokens | %d filing chunks | %d news chunks | %d citations",
        total_tokens, len(filing_block_parts), len(news_block_parts), len(citations),
    )

    return context_string, citations


# ── Step 4: Generation ────────────────────────────────────────────────────────

def generate_answer(
    query_text: str,
    context_string: str,
    query_type: str,
) -> str:
    """
    Call Ollama to generate the analyst briefing response.
    Combines the system prompt, assembled context, and user query.
    """
    # Add query-type-specific instruction to the user prompt
    type_instructions = {
        "single_company_narrative":  "Focus on qualitative factors: strategy, risks, competitive position.",
        "single_company_financial":  "Lead with specific numbers and trends. Include YoY comparisons where data allows.",
        "comparative_narrative":     "Structure your response to directly compare the companies. Use a consistent framework for each.",
        "comparative_financial":     "Use a tabular or parallel structure. Normalize metrics before comparing.",
        "sector_overview":           "Identify sector-wide trends, then note how individual companies differ.",
    }
    instruction = type_instructions.get(query_type, "")

    user_message = (
        f"Context:\n{context_string}\n\n"
        f"{'─' * 60}\n\n"
        f"Question: {query_text}\n\n"
        f"{instruction}"
    )

    log.info("Calling %s for answer generation...", GENERATION_MODEL)
    start = time.time()

    response = ollama.chat(
        model=GENERATION_MODEL,
        messages=[
            {"role": "system", "content": ANALYST_SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        options={
            "temperature": 0.2,
            "num_predict": 1500,
        },
    )

    elapsed = time.time() - start
    answer  = response["message"]["content"].strip()
    log.info("Answer generated in %.1f seconds (%d chars)", elapsed, len(answer))
    return answer


# ── Public API ────────────────────────────────────────────────────────────────

def query(
    query_text: str,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """
    Main entry point for the RAG query engine.

    Args:
        query_text: Natural language question from the user.
        tickers:    Optional list of ticker symbols. If not provided,
                    the orchestrator will attempt to infer them from
                    the query text using COMPANY_SECTORS.

    Returns:
        {
            "query":       original query text,
            "query_type":  classified query type,
            "tickers":     tickers used,
            "answer":      generated analyst response,
            "citations":   list of source dicts,
            "token_count": approximate context token count,
        }
    """
    tickers = [t.upper() for t in (tickers or [])]

    # Auto-detect tickers from query text if none provided
    if not tickers:
        query_upper = query_text.upper()
        detected = [t for t in COMPANY_SECTORS if t in query_upper]
        if detected:
            tickers = detected
            log.info("Auto-detected tickers from query: %s", tickers)

    # Step 1: classify
    query_type = classify_query(query_text, tickers)

    # Step 2 & 3: retrieve and assemble context
    client      = get_client()
    filings_col = get_or_create_collection(client, COLLECTION_FILINGS)
    news_col    = get_or_create_collection(client, COLLECTION_NEWS)
    profiles_col = get_or_create_collection(client, COLLECTION_PROFILES)

    context_string, citations = assemble_context(
        query_text, query_type, tickers,
        filings_col, news_col, profiles_col,
    )

    # Step 4 & 5: generate
    answer = generate_answer(query_text, context_string, query_type)

    return {
        "query":       query_text,
        "query_type":  query_type,
        "tickers":     tickers,
        "answer":      answer,
        "citations":   citations,
        "token_count": _count_tokens(ANALYST_SYSTEM_PROMPT + context_string + query_text),
    }


# ── Interactive REPL ──────────────────────────────────────────────────────────

def _print_result(result: dict) -> None:
    """Pretty-print a query result to the terminal."""
    print(f"\n{'═' * 70}")
    print(f"Query type : {result['query_type']}")
    print(f"Tickers    : {', '.join(result['tickers']) or 'none detected'}")
    print(f"Tokens     : ~{result['token_count']:,}")
    print(f"{'─' * 70}")
    print(result["answer"])
    print(f"\n{'─' * 70}")
    print(f"Sources used ({len(result['citations'])}):")
    seen: set[str] = set()
    for c in result["citations"]:
        if c["type"] == "sec_filing":
            key = f"  [{c['ticker']}] {c['form_type']} {c['section']} ({c['date']})"
        elif c["type"] == "news_article":
            key = f"  [{c['ticker']}] {c['source']} ({c['date']}) — {c.get('sentiment','')}"
        else:
            key = f"  [{c['ticker']}] {c['source']}"
        if key not in seen:
            print(key)
            seen.add(key)
    print(f"{'═' * 70}\n")


def interactive_repl() -> None:
    """
    Simple interactive query loop for testing the full RAG pipeline.

    Format:  <query text> [| TICKER1 TICKER2 ...]
    Example: What are Apple's biggest risks? | AAPL
    Example: Compare Apple and Microsoft margins | AAPL MSFT
    Example: quit
    """
    print("\nRAG Orchestrator — Interactive Query Mode")
    print("Format: <question> [| TICKER1 TICKER2]")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            raw = input("Query > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not raw:
            continue
        if raw.lower() in ("quit", "exit", "q"):
            break

        # Parse optional ticker override after pipe
        parts  = raw.split("|")
        q_text = parts[0].strip()
        tickers = []
        if len(parts) > 1:
            tickers = [t.strip().upper() for t in parts[1].split() if t.strip()]

        try:
            result = query(q_text, tickers=tickers)
            _print_result(result)
        except Exception as e:
            log.error("Query failed: %s", e)
            import traceback
            traceback.print_exc()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    interactive_repl()