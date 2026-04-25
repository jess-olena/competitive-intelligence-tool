# 🕵️ CIA Agent: Competitive Intelligence, Automated

> AI-powered competitive intelligence briefing tool using RAG, ChromaDB, Ollama, and SEC EDGAR data. Generates cited analyst briefings from 10-K/10-Q filings, global news, and sentiment analysis across public companies in multiple sectors.

**MGS 636 AI for Managers — End-of-Term Project**

---

## What It Does

CIA Agent automates the competitive intelligence analyst workflow. Instead of spending hours reading SEC filings and scanning headlines, you ask a question and receive a structured, cited briefing document in minutes — grounded in real data from SEC EDGAR, global news sources, and curated company profiles.

The system demonstrates three core AI for Managers course pillars:

| Pillar | Implementation |
|---|---|
| **Prompt Engineering** | Analyst system prompt with chain-of-thought, 4 sector skills files, sentiment and translation prompts |
| **Knowledge Generation** | Company markdown profiles, XBRL financial extraction, sector analyst skill files |
| **RAG** | ChromaDB vector store, 5 query types, token-budgeted context assembly, hierarchical retrieval |

---

## Live Demo

The Streamlit app provides a full web interface for querying the pipeline:

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`

![CIA Agent Demo Interface](https://img.shields.io/badge/Streamlit-Demo-FF4B4B?logo=streamlit&logoColor=white)

**Demo features:**
- Company dropdown (7 companies across Technology and Energy sectors)
- Sentiment snapshot and financial data in the sidebar
- Free-form query input with sector-specific suggested queries
- Full briefing output with cited SEC filings and news sources
- Recent news feed with sentiment scores per article

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  LAYER 1 — DATA INGESTION               │
│  edgar_fetcher → financials_parser → document_chunker   │
│  news_fetcher → translator_ollama → sentiment_scorer    │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│            LAYER 2 — INTELLIGENCE ENGINE (RAG)          │
│    vector_store (ChromaDB) → rag_orchestrator           │
│    context_loader (skills + profiles) → Ollama LLM      │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  LAYER 3 — OUTPUT                       │
│     briefing_generator → Markdown files + Streamlit     │
└─────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Component | Technology |
|---|---|
| LLM (generation + classification) | Ollama `llama3.1:8b` — fully local |
| Embeddings | Ollama `nomic-embed-text` — fully local |
| Vector store | ChromaDB (persistent, `db/chroma/`) |
| Financial database | SQLite (`db/financial.db`) |
| Web interface | Streamlit |
| SEC data | EDGAR REST API (free, no key required beyond User-Agent) |
| News data | NewsAPI |
| Settings | `pydantic-settings` + `.env` file |
| HTML parsing | `beautifulsoup4`, `lxml` |
| Token counting | `tiktoken` |

**Why fully local?**
- News articles get taken down — a local corpus preserves the information landscape at ingestion time
- No query logging by third-party APIs — competitive intelligence stays inside your perimeter
- Pinned model version = reproducible, auditable results
- Zero marginal cost per query at scale

---

## Company Coverage

7 companies across 2 sectors with full data ingestion (profiles, SEC filings, news, sentiment):

| Ticker | Company | Sector | Avg Sentiment | Articles |
|---|---|---|---|---|
| NVDA | NVIDIA Corporation | Technology | +0.164 | 329 |
| AAPL | Apple Inc. | Technology | +0.056 | 408 |
| GOOGL | Alphabet Inc. | Technology | +0.037 | 166 |
| NEE | NextEra Energy | Energy | -0.022 | 143 |
| MSFT | Microsoft Corporation | Technology | -0.041 | 192 |
| CVX | Chevron Corporation | Energy | -0.148 | 247 |
| XOM | ExxonMobil Corporation | Energy | -0.227 | 195 |

---

## Corpus Statistics

| Asset | Count |
|---|---|
| SEC filing chunks (ChromaDB) | 1,732 |
| News articles scored | 1,880 |
| Financial snapshot rows (SQLite) | 437 |
| Company profile chunks (ChromaDB) | 164 |
| **Total embedded documents** | **3,450** |

---

## Prerequisites

- Python 3.12
- [Ollama](https://ollama.com) installed with models pulled:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

- A [NewsAPI](https://newsapi.org) key (free tier)
- An [Anthropic](https://console.anthropic.com) API key (optional — only needed for Claude-based generation)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/competitive-intelligence-tool.git
cd competitive-intelligence-tool
```

### 2. Create and activate virtual environment

**Mac / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate.bat
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
ANTHROPIC_API_KEY=your-key-here        # optional
EDGAR_USER_AGENT=YourName your@email.com   # required by SEC policy
NEWS_API_KEY=your-newsapi-key-here     # required for news ingestion
VOYAGE_API_KEY=                        # leave empty
```

### 5. Create project directories

```bash
python -c "from config import settings; settings.ensure_directories(); print('Done.')"
```

---

## Running the Data Pipeline

Run these steps in order to build the corpus from scratch. Steps 1–3 take approximately 30 minutes total.

### Step 1 — Ingest SEC EDGAR data

```bash
python run_ingestion.py
```

Fetches EDGAR submissions, XBRL financial facts, and 10-K/10-Q filing HTML for all companies. Writes to SQLite and `data/sec_filings/chunks.json`.

### Step 2 — Fetch news articles

```bash
python news_fetcher.py
```

### Step 3 — Score sentiment (runs locally via Ollama)

```bash
python sentiment_scorer_ollama.py --limit 20   # test batch first
python sentiment_scorer_ollama.py              # full run
```

### Step 4 — Build the vector store

```bash
python vector_store.py build
```

Takes 15–25 minutes. Check results with `python vector_store.py stats`.

### Step 5 — Run the app

```bash
streamlit run app.py
```

---

## Usage

### Streamlit web interface

```bash
streamlit run app.py
# Opens at http://localhost:8501
```

### Interactive RAG query (command line)

```bash
python rag_orchestrator.py
```

Format: `<question> | TICKER`

```
Query > What are Apple's biggest competitive risks? | AAPL
Query > Compare ExxonMobil and Chevron's risk exposure | XOM CVX
Query > quit
```

### Generate briefings

```bash
# Single company
python briefing_generator.py --ticker AAPL

# Comparative
python briefing_generator.py --ticker XOM --ticker CVX --compare

# Full sector
python briefing_generator.py --sector tech
```

Briefings are saved to `outputs/briefings/` as dated markdown files.

---

## RAG Query Types

The orchestrator automatically classifies every query into one of five types:

| Query Type | Example | Retrieval Strategy |
|---|---|---|
| `single_company_narrative` | "What are Apple's biggest risks?" | Filing chunks + news |
| `single_company_financial` | "Show Apple's revenue trend" | SQLite financials + filing chunks |
| `comparative_narrative` | "Compare NVIDIA and Microsoft's AI strategy" | Per-company filing chunks + news |
| `comparative_financial` | "How do Apple and Microsoft margins compare?" | SQLite financials per company |
| `sector_overview` | "What's happening in the energy sector?" | Filing chunks across all sector companies |

---

## Adding a New Company

1. Generate a company profile using the template in `skills/company_profile_template.md` and save to `data/profiles/TICKER.md`
2. Add the ticker, CIK, and metadata to `run_ingestion.py`
3. Run `python run_ingestion.py`
4. Add the company to the `COMPANIES` list in `news_fetcher.py`
5. Run `python news_fetcher.py --ticker TICKER`
6. Run `python sentiment_scorer_ollama.py --ticker TICKER`
7. Rebuild the vector store: `python vector_store.py reset --confirm && python vector_store.py build`
8. Add the ticker to `COMPANIES` and `SECTOR_MAP` in `app.py`

---

## Project Structure

```
cia-agent/
├── app.py                       # Streamlit web interface
├── config.py                    # Settings via pydantic-settings
├── context_loader.py            # Loads skills + profiles into prompt context
├── ollama_client.py             # Ollama generation + embedding wrapper
├── rag_orchestrator.py          # Query classifier + context assembly + generation
├── briefing_generator.py        # Structured markdown briefing output
├── vector_store.py              # ChromaDB: embed, store, retrieve
├── edgar_fetcher.py             # SEC EDGAR API client
├── financials_parser.py         # XBRL → SQLite financial snapshots
├── document_chunker.py          # 10-K/10-Q HTML → paragraph chunks
├── run_ingestion.py             # Full ingestion pipeline orchestrator
├── news_fetcher.py              # NewsAPI integration
├── translator_ollama.py         # Non-English article translation via Ollama
├── sentiment_scorer_ollama.py   # Article sentiment scoring via Ollama
├── smoke_test.py                # Validates DB + context loader connection
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment variable template
└── skills/
    ├── tech_sector.md           # Technology analyst skill file
    ├── energy_sector.md         # Energy analyst skill file
    ├── healthcare_sector.md     # Healthcare analyst skill file
    ├── consumer_sector.md       # Consumer & Retail analyst skill file
    └── company_profile_template.md
```

---

## Course Pillars Demonstrated

### Prompt Engineering
- Master analyst system prompt with chain-of-thought reasoning structure
- Four sector-specific skills files that teach Claude/Ollama domain reasoning
- Sentiment analysis prompt returning structured JSON with topic-level scores
- Translation prompt that preserves financial sentiment markers across languages
- Briefing structure template enforcing consistent output format

### Knowledge Generation
- Seven curated company markdown profiles as RAG knowledge artifacts
- Four sector analyst skill files covering Technology, Energy, Healthcare, and Consumer
- XBRL financial extraction pipeline producing 437 structured financial snapshot rows
- Demonstrates knowledge pipeline from raw SEC data to queryable structured knowledge

### Retrieval Augmented Generation
- ChromaDB vector store with three collections (filings, news, profiles)
- Five-way query classification routing to different retrieval strategies
- Token-budgeted context assembly (6,000 token window for llama3.1:8b)
- Hierarchical retrieval: sector → company → document level
- Ablation test demonstrates quality difference: no RAG → prompts only → full RAG

---

## License
MIT License

---

*Built for MGS 636 AI for Managers · Spring Term 2026*
