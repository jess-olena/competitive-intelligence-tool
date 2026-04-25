# -*- coding: utf-8 -*-
"""
app.py — CIA Agent: Competitive Intelligence, Automated
Streamlit live demo interface

Run with:
    streamlit run app.py
"""

import sqlite3
import time
from pathlib import Path

import streamlit as st

from config import settings
from rag_orchestrator import query as rag_query
from vector_store import (
    get_client,
    get_or_create_collection,
    COLLECTION_FILINGS,
    COLLECTION_NEWS,
    COLLECTION_PROFILES,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CIA Agent",
    page_icon="🕵️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .main-title { font-size: 2.2rem; font-weight: 700; color: #0B1C3D; margin-bottom: 0; }
  .sub-title  { font-size: 1.0rem; color: #64748B; margin-top: 0; margin-bottom: 1.5rem; }
  .metric-card {
    background: #F8FAFC; border: 1px solid #E2E8F0;
    border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 0.5rem;
  }
  .metric-big   { font-size: 1.8rem; font-weight: 700; color: #0B1C3D; }
  .metric-label { font-size: 0.8rem; color: #64748B; margin-top: 0.1rem; }
  .section-header {
    font-size: 0.75rem; font-weight: 600; color: #0D9488;
    letter-spacing: 0.08em; text-transform: uppercase;
    margin-bottom: 0.5rem; margin-top: 1rem;
  }
  .sentiment-positive { color: #10B981; font-weight: 600; }
  .sentiment-negative { color: #EF4444; font-weight: 600; }
  .sentiment-neutral  { color: #64748B; font-weight: 600; }
  .citation-row {
    font-size: 0.78rem; color: #475569;
    padding: 0.25rem 0; border-bottom: 1px solid #F1F5F9;
  }
  .stButton > button {
    background-color: #0B1C3D; color: white;
    border-radius: 8px; border: none;
    padding: 0.5rem 2rem; font-weight: 600;
    width: 100%;
  }
  .stButton > button:hover { background-color: #0D9488; }
</style>
""", unsafe_allow_html=True)

# ── Company roster (only companies with profiles + data) ──────────────────────
COMPANIES = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "GOOGL": "Alphabet Google",
    "XOM":  "ExxonMobil Corporation",
    "NEE":  "NextEra Energy Inc.",
    "CVX":  "Chevron Corporation",
}

SECTOR_MAP = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "GOOGL": "Technology",
    "XOM":  "Energy",     "NEE":  "Energy",      "CVX":  "Energy",
}

# ── Suggested queries per sector ──────────────────────────────────────────────
SUGGESTED_QUERIES = {
    "Technology": [
        "What are the biggest competitive risks this company faces?",
        "How has revenue and margin trended over the last year?",
        "What is the company's AI strategy and competitive position?",
        "What regulatory risks are disclosed in the most recent 10-K?",
    ],
    "Energy": [
        "What are the company's biggest operational and regulatory risks?",
        "How has revenue trended and what drives margin pressure?",
        "What is the company's capital allocation strategy?",
        "How exposed is this company to commodity price volatility?",
    ],
}

# ── ChromaDB collections (cached so they load once) ──────────────────────────
@st.cache_resource(show_spinner=False)
def load_collections():
    client       = get_client()
    filings_col  = get_or_create_collection(client, COLLECTION_FILINGS)
    news_col     = get_or_create_collection(client, COLLECTION_NEWS)
    profiles_col = get_or_create_collection(client, COLLECTION_PROFILES)
    return filings_col, news_col, profiles_col

# ── Financial data loader ─────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_financials(ticker: str) -> dict:
    if not settings.SQLITE_DB_PATH.exists():
        return {}
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    annual = conn.execute("""
        SELECT period_end, revenue_m, net_income_m,
               gross_margin, operating_margin, eps_diluted
        FROM financial_snapshots
        WHERE ticker = ? AND form_type = '10-K'
        ORDER BY period_end DESC LIMIT 1
    """, (ticker,)).fetchone()
    quarters = conn.execute("""
        SELECT period_end, fiscal_period, revenue_m, gross_margin
        FROM financial_snapshots
        WHERE ticker = ? AND form_type = '10-Q'
        ORDER BY period_end DESC LIMIT 4
    """, (ticker,)).fetchall()
    conn.close()
    return {
        "annual":   dict(annual) if annual else {},
        "quarters": [dict(q) for q in quarters],
    }

# ── Sentiment data loader ─────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_sentiment(ticker: str) -> dict:
    if not settings.SQLITE_DB_PATH.exists():
        return {}
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT avg_sentiment, article_count, high_materiality_count,
               dominant_tone, avg_earnings_outlook, avg_competitive,
               avg_regulatory, avg_leadership, avg_innovation
        FROM company_sentiment_index WHERE ticker = ?
    """, (ticker,)).fetchone()
    conn.close()
    return dict(row) if row else {}

# ── Recent news loader ────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_recent_news(ticker: str, limit: int = 8) -> list:
    if not settings.SQLITE_DB_PATH.exists():
        return []
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT headline, source_name, published_at,
               sentiment_label, sentiment_score, url
        FROM news_articles
        WHERE ticker = ? AND headline IS NOT NULL
        ORDER BY published_at DESC LIMIT ?
    """, (ticker, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🕵️ CIA Agent")
    st.markdown("*Competitive Intelligence, Automated*")
    st.divider()

    # Company selector
    st.markdown('<div class="section-header">Select Company</div>',
                unsafe_allow_html=True)
    ticker = st.selectbox(
        "Company",
        options=list(COMPANIES.keys()),
        format_func=lambda t: f"{t} — {COMPANIES[t]}",
        label_visibility="collapsed",
    )
    company_name = COMPANIES[ticker]
    sector       = SECTOR_MAP[ticker]

    st.divider()

    # Sentiment snapshot
    sentiment = load_sentiment(ticker)
    if sentiment:
        st.markdown('<div class="section-header">Sentiment Snapshot</div>',
                    unsafe_allow_html=True)
        tone  = sentiment.get("dominant_tone", "neutral")
        score = sentiment.get("avg_sentiment", 0)
        tone_class = f"sentiment-{tone}"
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-big">{score:+.3f}</div>'
            f'<div class="metric-label">avg sentiment score</div>'
            f'<div class="{tone_class}" style="margin-top:0.3rem">'
            f'{tone.upper()}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        col1, col2 = st.columns(2)
        col1.metric("Articles", sentiment.get("article_count", 0))
        col2.metric("High-Mat", sentiment.get("high_materiality_count", 0))

    st.divider()

    # Financial snapshot
    financials = load_financials(ticker)
    annual = financials.get("annual", {})
    if annual:
        st.markdown('<div class="section-header">Latest Annual Financials</div>',
                    unsafe_allow_html=True)
        period = annual.get("period_end", "")[:7]
        rev    = annual.get("revenue_m")
        ni     = annual.get("net_income_m")
        gm     = annual.get("gross_margin")
        om     = annual.get("operating_margin")
        eps    = annual.get("eps_diluted")

        if rev:
            st.metric("Revenue", f"${rev:,.0f}M",
                      help=f"Fiscal period ending {period}")
        if ni:
            st.metric("Net Income", f"${ni:,.0f}M")
        if gm:
            st.metric("Gross Margin", f"{gm*100:.1f}%")
        if om:
            st.metric("Operating Margin", f"{om*100:.1f}%")
        if eps:
            st.metric("EPS (Diluted)", f"${eps:.2f}")

    st.divider()
    st.markdown(
        '<div style="font-size:0.7rem;color:#94A3B8">'
        'MGS 636 AI for Managers<br>End-of-Term Project · 25% of Grade'
        '</div>',
        unsafe_allow_html=True,
    )

# ── Main panel ────────────────────────────────────────────────────────────────
st.markdown(
    f'<div class="main-title">🕵️ CIA Agent</div>'
    f'<div class="sub-title">Competitive Intelligence, Automated — '
    f'{company_name} ({ticker}) · {sector}</div>',
    unsafe_allow_html=True,
)

# Tabs
tab_query, tab_news, tab_about = st.tabs([
    "💬 Ask a Question", "📰 Recent News", "ℹ️ About"
])

# ── TAB 1: Query ──────────────────────────────────────────────────────────────
with tab_query:
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.markdown('<div class="section-header">Your question</div>',
                    unsafe_allow_html=True)
        user_query = st.text_area(
            "Question",
            placeholder=f"Ask anything about {company_name}...",
            height=100,
            label_visibility="collapsed",
        )

    with col_right:
        st.markdown('<div class="section-header">Suggested queries</div>',
                    unsafe_allow_html=True)
        suggestions = SUGGESTED_QUERIES.get(sector, SUGGESTED_QUERIES["Technology"])
        for s in suggestions:
            if st.button(s, key=f"sug_{s[:20]}"):
                user_query = s
                st.session_state["last_query"] = s

    # Run button
    run_col, _ = st.columns([1, 3])
    with run_col:
        run_clicked = st.button("Generate Briefing ▶", type="primary")

    # Generate and display
    if run_clicked and user_query.strip():
        with st.spinner(f"Analyzing {company_name}... this takes ~200 seconds (dependent on your processor)"):
            start = time.time()
            try:
                result = rag_query(user_query.strip(), tickers=[ticker])
                elapsed = time.time() - start

                st.success(f"Generated in {elapsed:.0f}s · "
                           f"{result['token_count']:,} tokens · "
                           f"{len(result['citations'])} citations · "
                           f"Query type: `{result['query_type']}`")

                # Answer
                st.markdown("---")
                st.markdown(result["answer"].replace("$", r"\$"))

                # Citations
                if result["citations"]:
                    st.markdown("---")
                    st.markdown('<div class="section-header">Sources cited</div>',
                                unsafe_allow_html=True)
                    seen = set()
                    for c in result["citations"]:
                        if c["type"] == "sec_filing":
                            line = (f"📄 **[{c['ticker']}]** "
                                    f"{c.get('form_type','')} · "
                                    f"{c.get('section','')} · "
                                    f"{c.get('date','')}")
                        elif c["type"] == "news_article":
                            sent = c.get("sentiment", "")
                            sent_icon = ("🟢" if sent == "positive"
                                         else "🔴" if sent == "negative"
                                         else "⚪")
                            line = (f"{sent_icon} **[{c['ticker']}]** "
                                    f"{c.get('source','')} · "
                                    f"{c.get('date','')}")
                        else:
                            line = (f"💾 **[{c['ticker']}]** "
                                    f"{c.get('source','')}")
                        if line not in seen:
                            st.markdown(
                                f'<div class="citation-row">{line}</div>',
                                unsafe_allow_html=True,
                            )
                            seen.add(line)

            except Exception as e:
                st.error(f"Query failed: {e}")
                st.exception(e)

    elif run_clicked and not user_query.strip():
        st.warning("Please enter a question or click a suggested query.")

# ── TAB 2: Recent News ────────────────────────────────────────────────────────
with tab_news:
    st.markdown(
        f'<div class="section-header">Recent news — {company_name}</div>',
        unsafe_allow_html=True,
    )
    news = load_recent_news(ticker)
    if not news:
        st.info("No news articles found. Run news_fetcher.py first.")
    else:
        for article in news:
            sentiment  = article.get("sentiment_label") or "unscored"
            score      = article.get("sentiment_score")
            tone_icon  = ("🟢" if sentiment == "positive"
                          else "🔴" if sentiment == "negative"
                          else "⚪")
            date_str   = (article.get("published_at") or "")[:10]
            source     = article.get("source_name") or ""
            headline   = article.get("headline") or "(no headline)"
            score_str  = f" ({score:+.2f})" if score is not None else ""

            with st.container():
                st.markdown(
                    f"{tone_icon} **{headline}**  \n"
                    f"<span style='font-size:0.8rem;color:#94A3B8'>"
                    f"{source} · {date_str} · {sentiment}{score_str}"
                    f"</span>",
                    unsafe_allow_html=True,
                )
                st.divider()

# ── TAB 3: About ──────────────────────────────────────────────────────────────
with tab_about:
    st.markdown("## CIA Agent — System Overview")
    st.markdown("""
CIA Agent is a six-phase AI pipeline that automates competitive intelligence
briefing generation from SEC filings, global news, and financial data.

### Pipeline
| Phase | Module | Output |
|---|---|---|
| 1 — Corpus Design | `context_loader.py` | Skills files, company profiles |
| 2 — EDGAR Ingestion | `edgar_fetcher.py`, `financials_parser.py`, `document_chunker.py` | 1,732 chunks, 437 financial rows |
| 3 — News & Sentiment | `news_fetcher.py`, `sentiment_scorer_ollama.py` | 1,554 articles scored |
| 4 — Vector Store | `vector_store.py` | ChromaDB: 3,450 embedded documents |
| 5 — RAG Orchestrator | `rag_orchestrator.py` | Query classification + context assembly |
| 5 — Briefing Generator | `briefing_generator.py` | Structured markdown briefings |

### Technology
- **LLM:** Ollama `llama3.1:8b` (local, no API cost)
- **Embeddings:** Ollama `nomic-embed-text`
- **Vector store:** ChromaDB (persistent, local)
- **Financial DB:** SQLite (`db/financial.db`)
- **Data sources:** SEC EDGAR REST API · NewsAPI · Company markdown profiles

### Course pillars demonstrated
- **Prompt Engineering** — Analyst system prompt, sector skills files, chain-of-thought
- **Knowledge Generation** — 7 company profiles, 4 sector skill files, XBRL extraction
- **RAG** — Hierarchical retrieval, token-budgeted context assembly, 5 query types
    """)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Filing Chunks", "1,732")
    col2.metric("News Articles", "1,554")
    col3.metric("Financial Rows", "437")
    col4.metric("Profile Chunks", "164")