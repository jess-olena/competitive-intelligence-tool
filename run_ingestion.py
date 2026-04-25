# -*- coding: utf-8 -*-
"""
AI Project
Created: 14APR2026
run_ingestion.py
"""
from config import settings
from edgar_fetcher import fetch_all
from financials_parser import FinancialsParser
from document_chunker import chunk_filings_from_fetch_results
import json

TICKERS = ["AAPL", "MSFT", "NVDA", "XOM", "NEE", "CVX", "GOOGL"]

CIK_MAP = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "XOM":  "0000034088",
    "NEE":  "0000753308",
    "CVX":  "0000093410",
    "GOOGL":"0001652044",   # ← add this
}

COMPANY_META = {
    "AAPL": {"company_name": "Apple Inc.",           "sector": "Technology"},
    "MSFT": {"company_name": "Microsoft Corp.",      "sector": "Technology"},
    "NVDA": {"company_name": "NVIDIA Corp.",         "sector": "Technology"},
    "XOM":  {"company_name": "ExxonMobil Corp.",     "sector": "Technology"},
    "NEE":  {"company_name": "NextEra Energy",       "sector": "Energy"},
    "CVX":  {"company_name": "Chevron Corp.",        "sector": "Energy"},
    "GOOGL":{"company_name": "Alphabet Inc.",        "sector": "Technology"},  # ← add
}

# Step 1: Fetch raw EDGAR data (skip if already done)
print("Step 1: Fetching EDGAR data...")
fetch_results = fetch_all(TICKERS, CIK_MAP,
                          output_dir=settings.FILINGS_DIR / "raw")

# Step 2: Parse XBRL facts into SQLite (upserts safely)
print("\nStep 2: Parsing financials into SQLite...")
with FinancialsParser(settings.SQLITE_DB_PATH) as parser:
    summary = parser.ingest_from_fetch_results(fetch_results)
    print("\n── Financial snapshot summary ──")
    for ticker, rows in sorted(summary.items()):
        print(f"  {ticker:<6} {rows:>4} rows")

# Step 3: Chunk filing narrative text
print("\nStep 3: Chunking filing documents...")
chunks = chunk_filings_from_fetch_results(fetch_results, COMPANY_META)

# Save chunks to disk for Phase 4 embedding
chunks_path = settings.FILINGS_DIR / "chunks.json"
with open(chunks_path, "w", encoding="utf-8") as f:
    json.dump(chunks, f, indent=2, ensure_ascii=False)

print(f"\n── Chunk summary ──")
print(f"  Total chunks: {len(chunks)}")
by_ticker = {}
for c in chunks:
    by_ticker.setdefault(c["ticker"], 0)
    by_ticker[c["ticker"]] += 1
for ticker, count in sorted(by_ticker.items()):
    print(f"  {ticker:<6} {count:>4} chunks")
print(f"\n  Saved to: {chunks_path}")