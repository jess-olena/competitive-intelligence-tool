# -*- coding: utf-8 -*-
"""
AI Project
Created: 14APR2026
smoke_test.py
"""

from config import settings
from context_loader import build_static_context
from financials_parser import FinancialsParser

# Test 1: context loader
ctx = build_static_context(["AAPL", "XOM"])
assert ctx["skills"],   "Skills files not loading"
assert ctx["profiles"], "Company profiles not found"
print(f"Skills:   {len(ctx['skills'])} chars")
print(f"Profiles: {len(ctx['profiles'])} chars")

# Test 2: financial database
with FinancialsParser(settings.SQLITE_DB_PATH) as p:
    tickers = p.list_tickers()
    print(f"DB tickers: {tickers}")
    quarters = p.get_last_8_quarters("AAPL")
    print(f"AAPL quarters in DB: {len(quarters)}")

print("\nAll checks passed. Phases 1 and 2 complete.")