# -*- coding: utf-8 -*-
"""
Title: AI Project Directory Tree
Created: 13 April 2026

"""

competitive_intel/
│
├── README.md                          # Project overview, setup, and usage guide
├── requirements.txt                   # All Python dependencies
├── config.py                          # Environment variables and app-wide settings
├── .env.example                       # Template for secrets (never commit .env)
├── .gitignore                         # Excludes .env, data/, outputs/, __pycache__/
│
├── main.py                            # CLI entrypoint — run briefings, ingest, query
│
├── data/                              # Raw ingested files (git-ignored)
│   ├── sec_filings/                   # Downloaded EDGAR XBRL/HTML filing documents
│   │   └── {ticker}/{form_type}/      # e.g. AAPL/10-K/0001193125-24-...txt
│   ├── news/                          # Raw news article JSON dumps
│   │   └── {ticker}/                  # e.g. MSFT/2024-01-15_article_xyz.json
│   └── profiles/                      # Company markdown profiles (hand-authored or AI-gen)
│       └── {ticker}.md                # e.g. NVDA.md — business model, products, SWOT
│
├── outputs/                           # Generated briefings (git-ignored)
│   ├── briefings/                     # Final competitive intel reports
│   │   └── {date}_{ticker}_brief.md   # e.g. 2024-01-15_AAPL_brief.md
│   └── summaries/                     # Intermediate chunk summaries cached for reuse
│       └── {source_hash}.txt
│
├── db/                                # Persistent storage layer
│   ├── financial.db                   # SQLite: structured financial data from filings
│   └── chroma/                        # ChromaDB vector store directory (auto-created)
│
├── ingestion/                         # Data ingestion pipeline modules
│   ├── __init__.py
│   ├── edgar_client.py                # Fetches SEC EDGAR filings via EDGAR REST API
│   ├── edgar_parser.py                # Parses 10-K/10-Q XBRL/HTML → structured dicts
│   ├── news_fetcher.py                # Pulls news from NewsAPI / RSS / web scraping
│   ├── news_parser.py                 # Cleans and normalizes raw news articles
│   ├── profile_loader.py              # Reads and validates company .md profiles
│   └── ingest_pipeline.py             # Orchestrates full ingestion for a given ticker
│
├── storage/                           # Storage abstraction layer
│   ├── __init__.py
│   ├── sqlite_store.py                # SQLite schema, CRUD for financials/metadata
│   ├── chroma_store.py                # ChromaDB collection management and upsert logic
│   └── models.py                      # Pydantic data models (Filing, Article, Profile)
│
├── rag/                               # Retrieval-Augmented Generation pipeline
│   ├── __init__.py
│   ├── chunker.py                     # Text splitting strategies (fixed, semantic, hybrid)
│   ├── embedder.py                    # Wraps embedding model (Voyage AI or local)
│   ├── retriever.py                   # Hybrid search: ChromaDB vector + SQLite keyword
│   └── context_builder.py             # Assembles retrieved chunks into prompt context
│
├── llm/                               # Claude API interaction layer
│   ├── __init__.py
│   ├── claude_client.py               # Anthropic SDK wrapper with retry/rate-limit logic
│   ├── prompt_templates.py            # All system and user prompt templates
│   └── briefing_generator.py          # Orchestrates RAG → Claude → structured output
│
├── analysis/                          # Financial and competitive analysis helpers
│   ├── __init__.py
│   ├── financial_metrics.py           # Computes ratios (P/E, margins, YoY growth, etc.)
│   ├── sentiment_analyzer.py          # News sentiment scoring (rule-based + Claude)
│   └── competitor_mapper.py           # Identifies peer companies from filings/profiles
│
├── api/                               # Optional FastAPI server for programmatic access
│   ├── __init__.py
│   ├── server.py                      # FastAPI app factory and lifespan hooks
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── ingest.py                  # POST /ingest/{ticker} — trigger ingestion
│   │   ├── briefing.py                # POST /briefing — generate briefing with options
│   │   └── query.py                   # GET /query — freeform RAG query endpoint
│   └── schemas.py                     # Pydantic request/response schemas for the API
│
├── utils/                             # Shared utilities
│   ├── __init__.py
│   ├── logger.py                      # Structured logging setup (loguru)
│   ├── rate_limiter.py                # Token-bucket rate limiter for external API calls
│   ├── file_utils.py                  # Safe file I/O, hash helpers, path builders
│   └── date_utils.py                  # Fiscal year mapping, quarter normalization
│
└── tests/                             # Test suite
    ├── __init__.py
    ├── conftest.py                    # Shared fixtures: test DB, mock clients, sample data
    ├── test_edgar_parser.py           # Unit tests for EDGAR parsing logic
    ├── test_chunker.py                # Chunking consistency and boundary tests
    ├── test_retriever.py              # RAG retrieval precision/recall tests
    ├── test_sqlite_store.py           # DB schema and CRUD correctness tests
    └── test_briefing_generator.py     # Integration test with mocked Claude responses
