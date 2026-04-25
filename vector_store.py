# -*- coding: utf-8 -*-
"""
vector_store.py — Phase 4: Vector Store & RAG Retrieval
AI for Managers — Competitive Intelligence Briefing Tool

Embeds SEC filing chunks and news articles into ChromaDB and provides
semantic retrieval for the RAG orchestrator in Phase 5.

Embedding engine: Ollama (nomic-embed-text) — runs locally, no API cost.
Vector store:     ChromaDB — persistent, stored in db/chroma/.

Pipeline position:
    document_chunker  →  vector_store  →  rag_orchestrator
    news_fetcher      →  vector_store  →  rag_orchestrator

Collections:
    filings_corpus   — SEC 10-K / 10-Q narrative chunks
    news_corpus      — News article headlines + snippets
    profiles_corpus  — Company markdown profile chunks

Usage:
    python vector_store.py build        # Index all chunks and news articles
    python vector_store.py stats        # Print collection statistics
    python vector_store.py query        # Interactive query test
    python vector_store.py reset        # Delete and rebuild all collections

Programmatic:
    from vector_store import embed_and_store, retrieve, retrieve_multi_company
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from config import settings
from ollama_client import embed as ollama_embed

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
COLLECTION_FILINGS  = settings.CHROMA_COLLECTION_FILINGS   # "sec_filings"
COLLECTION_NEWS     = settings.CHROMA_COLLECTION_NEWS       # "news_articles"
COLLECTION_PROFILES = settings.CHROMA_COLLECTION_PROFILES  # "company_profiles"

# Batch size for embedding calls — Ollama processes one at a time internally
# but we batch DB upserts for efficiency.
EMBED_BATCH_SIZE = 32

# Metadata fields ChromaDB accepts (must be str, int, float, or bool only)
# We select a safe subset from our full chunk schema.
FILING_META_KEYS = [
    "ticker", "company_name", "sector", "source_type",
    "filing_date", "fiscal_period", "section_name", "token_count",
]
NEWS_META_KEYS = [
    "ticker", "company_name", "sector", "language",
    "published_at", "source_name", "sentiment_label",
    "sentiment_score", "materiality",
]
PROFILE_META_KEYS = [
    "ticker", "company_name", "sector", "chunk_index",
]


# ── ChromaDB Client ───────────────────────────────────────────────────────────

def get_client() -> chromadb.PersistentClient:
    """
    Return a persistent ChromaDB client pointing at db/chroma/.
    Creates the directory if it does not exist.
    """
    chroma_path = settings.CHROMA_DB_PATH
    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(chroma_path),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    log.info("ChromaDB client ready: %s", chroma_path)
    return client


def get_or_create_collection(
    client: chromadb.PersistentClient,
    name: str,
) -> chromadb.Collection:
    """Get or create a ChromaDB collection by name."""
    collection = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},   # cosine similarity for text
    )
    log.info("Collection '%s': %d existing documents", name, collection.count())
    return collection


# ── Embedding Helper ──────────────────────────────────────────────────────────

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using Ollama (nomic-embed-text).
    Processes in batches and adds a short pause to avoid overwhelming
    the local Ollama server.
    """
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        vectors = ollama_embed(batch)
        all_vectors.extend(vectors)
        if i + EMBED_BATCH_SIZE < len(texts):
            time.sleep(0.1)   # small pause between batches
    return all_vectors


def _safe_meta(raw: dict, keys: list[str]) -> dict:
    """
    Extract only the allowed metadata keys and ensure all values are
    ChromaDB-compatible types (str, int, float, bool).
    None values are converted to empty strings.
    """
    meta: dict[str, Any] = {}
    for key in keys:
        val = raw.get(key)
        if val is None:
            meta[key] = ""
        elif isinstance(val, (str, int, float, bool)):
            meta[key] = val
        else:
            meta[key] = str(val)
    return meta


# ── Core Storage Function ─────────────────────────────────────────────────────

def embed_and_store(
    chunks: list[dict],
    collection: chromadb.Collection,
    meta_keys: list[str],
    id_field: str = "chunk_id",
    content_field: str = "content",
) -> int:
    """
    Embed a list of chunk dicts and upsert them into a ChromaDB collection.

    Args:
        chunks:        List of chunk dicts (from document_chunker or news_fetcher)
        collection:    Target ChromaDB collection
        meta_keys:     Which metadata fields to preserve
        id_field:      Name of the unique ID field in each chunk dict
        content_field: Name of the text content field to embed

    Returns:
        Number of chunks upserted.
    """
    if not chunks:
        log.warning("embed_and_store called with empty chunk list — nothing to do.")
        return 0

    log.info("Embedding %d chunks into '%s'...", len(chunks), collection.name)

    # Extract texts and IDs
    texts = [str(c.get(content_field, "")) for c in chunks]
    ids   = [str(c.get(id_field, f"chunk_{i}")) for i, c in enumerate(chunks)]
    metas = [_safe_meta(c, meta_keys) for c in chunks]

    # Embed in batches
    vectors = _embed_texts(texts)

    # Upsert into ChromaDB (upsert = insert or update if ID exists)
    collection.upsert(
        ids=ids,
        embeddings=vectors,
        documents=texts,
        metadatas=metas,
    )

    log.info("Upserted %d documents into '%s'", len(chunks), collection.name)
    return len(chunks)


# ── Retrieval Functions ───────────────────────────────────────────────────────

def retrieve(
    query: str,
    collection: chromadb.Collection,
    ticker: str | None = None,
    section: str | None = None,
    source_type: str | None = None,
    language: str | None = None,
    n_results: int = 8,
) -> list[dict]:
    """
    Embed a query and retrieve the top-n most semantically similar chunks.

    Optionally filters by metadata fields before ranking. All filters are
    applied as AND conditions.

    Args:
        query:       Natural language query string
        collection:  ChromaDB collection to search
        ticker:      Filter to a specific company (e.g. 'AAPL')
        section:     Filter to a specific filing section (e.g. 'Item 7 – MD&A')
        source_type: Filter by form type (e.g. '10-K', '10-Q')
        language:    Filter by language code (e.g. 'en') — news corpus only
        n_results:   Number of results to return

    Returns:
        List of result dicts, each containing:
            id, content, metadata, distance
        Sorted by relevance (closest distance first).
    """
    if not query.strip():
        return []

    # Build metadata filter
    where_clauses: list[dict] = []
    if ticker:
        where_clauses.append({"ticker": {"$eq": ticker.upper()}})
    if section:
        where_clauses.append({"section_name": {"$eq": section}})
    if source_type:
        where_clauses.append({"source_type": {"$eq": source_type}})
    if language:
        where_clauses.append({"language": {"$eq": language}})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    # Embed the query
    query_vector = _embed_texts([query])[0]

    # Cap n_results at collection size to avoid ChromaDB errors
    count = collection.count()
    if count == 0:
        log.warning("Collection '%s' is empty — run build_index() first.", collection.name)
        return []
    n = min(n_results, count)

    # Query ChromaDB
    query_kwargs: dict[str, Any] = {
        "query_embeddings": [query_vector],
        "n_results": n,
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    results = collection.query(**query_kwargs)

    # Flatten ChromaDB's nested response into a clean list of dicts
    output: list[dict] = []
    for i, doc_id in enumerate(results["ids"][0]):
        output.append({
            "id":       doc_id,
            "content":  results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })

    return output


def retrieve_multi_company(
    query: str,
    tickers: list[str],
    collection: chromadb.Collection,
    n_results_per_company: int = 3,
) -> dict[str, list[dict]]:
    """
    Retrieve top chunks per company for cross-company comparison queries.

    Runs one filtered retrieval per ticker and returns results grouped
    by company. This prevents a single company from dominating results
    when asking comparative questions.

    Args:
        query:                 Natural language query
        tickers:               List of ticker symbols to compare
        collection:            ChromaDB collection to search
        n_results_per_company: How many chunks to retrieve per company

    Returns:
        Dict mapping ticker → list of result dicts, e.g.:
        {
            "AAPL": [...3 chunks...],
            "MSFT": [...3 chunks...],
        }
    """
    results: dict[str, list[dict]] = {}
    for ticker in tickers:
        ticker_results = retrieve(
            query=query,
            collection=collection,
            ticker=ticker.upper(),
            n_results=n_results_per_company,
        )
        results[ticker.upper()] = ticker_results
        log.info("  %s: %d chunks retrieved", ticker.upper(), len(ticker_results))
    return results


def retrieve_combined(
    query: str,
    filings_col: chromadb.Collection,
    news_col: chromadb.Collection,
    ticker: str | None = None,
    n_filings: int = 6,
    n_news: int = 4,
) -> dict[str, list[dict]]:
    """
    Retrieve from both filings and news collections in one call.
    Used by the RAG orchestrator to build a combined context window.

    Returns:
        {"filings": [...], "news": [...]}
    """
    return {
        "filings": retrieve(query, filings_col, ticker=ticker, n_results=n_filings),
        "news":    retrieve(query, news_col,    ticker=ticker, n_results=n_news),
    }


# ── Profile Chunker ───────────────────────────────────────────────────────────

def chunk_profile(ticker: str, profile_text: str, chunk_size: int = 800) -> list[dict]:
    """
    Split a company markdown profile into chunks for embedding.
    Splits at double newlines (section boundaries) and respects chunk_size
    in characters (not tokens — profiles are short enough this is fine).
    """
    ticker = ticker.upper()
    sections = [s.strip() for s in profile_text.split("\n\n") if s.strip()]

    chunks: list[dict] = []
    current_parts: list[str] = []
    current_len = 0

    for section in sections:
        if current_len + len(section) > chunk_size and current_parts:
            content = "\n\n".join(current_parts)
            chunks.append({
                "chunk_id":    f"{ticker}_profile_{len(chunks):04d}",
                "ticker":      ticker,
                "company_name": ticker,
                "sector":      "",
                "chunk_index": len(chunks),
                "content":     content,
            })
            current_parts = [section]
            current_len = len(section)
        else:
            current_parts.append(section)
            current_len += len(section)

    if current_parts:
        content = "\n\n".join(current_parts)
        chunks.append({
            "chunk_id":    f"{ticker}_profile_{len(chunks):04d}",
            "ticker":      ticker,
            "company_name": ticker,
            "sector":      "",
            "chunk_index": len(chunks),
            "content":     content,
        })

    return chunks


# ── News Article Loader ───────────────────────────────────────────────────────

def load_news_from_db(
    db_path: Path,
    ticker: str | None = None,
) -> list[dict]:
    """
    Load news articles from SQLite and format them as embeddable chunks.
    Uses translated text when available, falls back to original snippet.
    """
    if not db_path.exists():
        log.warning("News DB not found at %s — skipping news corpus.", db_path)
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT article_id, ticker, company_name, sector,
               headline, source_name, published_at, language,
               content_snippet, translated_snippet,
               sentiment_label, sentiment_score, sentiment_materiality
        FROM news_articles
    """
    params: list = []
    if ticker:
        sql += " WHERE ticker = ?"
        params.append(ticker.upper())
    sql += " ORDER BY published_at DESC"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    chunks: list[dict] = []
    for row in rows:
        # Use translated text if available, otherwise original
        text = (
            row["translated_snippet"]
            or row["content_snippet"]
            or row["headline"]
            or ""
        ).strip()

        if not text:
            continue

        # Prepend headline so it's always in the embedding context
        headline = (row["headline"] or "").strip()
        if headline and not text.startswith(headline):
            text = f"{headline}\n\n{text}"

        chunks.append({
            "chunk_id":          row["article_id"],
            "ticker":            row["ticker"],
            "company_name":      row["company_name"],
            "sector":            row["sector"] or "",
            "language":          row["language"] or "en",
            "published_at":      row["published_at"] or "",
            "source_name":       row["source_name"] or "",
            "sentiment_label":   row["sentiment_label"] or "",
            "sentiment_score":   row["sentiment_score"] or 0.0,
            "materiality":       row["sentiment_materiality"] or "",
            "content":           text,
        })

    log.info("Loaded %d news articles from DB%s",
             len(chunks), f" for {ticker}" if ticker else "")
    return chunks


# ── Build Index ───────────────────────────────────────────────────────────────

def build_index(reset: bool = False) -> None:
    """
    Full index build: embeds filing chunks, news articles, and company profiles
    into their respective ChromaDB collections.

    Args:
        reset: If True, delete existing collections before rebuilding.
               Use this when chunk content has changed significantly.
    """
    client = get_client()

    # Optionally wipe existing collections
    if reset:
        for name in [COLLECTION_FILINGS, COLLECTION_NEWS, COLLECTION_PROFILES]:
            try:
                client.delete_collection(name)
                log.info("Deleted collection '%s'", name)
            except Exception:
                pass

    filings_col  = get_or_create_collection(client, COLLECTION_FILINGS)
    news_col     = get_or_create_collection(client, COLLECTION_NEWS)
    profiles_col = get_or_create_collection(client, COLLECTION_PROFILES)

    # ── 1. Filing chunks ──────────────────────────────────────────────────
    chunks_path = settings.FILINGS_DIR / "chunks.json"
    if chunks_path.exists():
        log.info("Loading filing chunks from %s", chunks_path)
        with open(chunks_path, encoding="utf-8") as f:
            filing_chunks = json.load(f)
        log.info("Loaded %d filing chunks", len(filing_chunks))
        embed_and_store(filing_chunks, filings_col, FILING_META_KEYS)
    else:
        log.warning("chunks.json not found at %s — skipping filings.", chunks_path)
        log.warning("Run run_ingestion.py first to generate chunks.json")

    # ── 2. News articles ──────────────────────────────────────────────────
    news_chunks = load_news_from_db(settings.SQLITE_DB_PATH)
    if news_chunks:
        embed_and_store(
            news_chunks, news_col, NEWS_META_KEYS,
            id_field="chunk_id", content_field="content",
        )
    else:
        log.warning("No news articles found — run news_fetcher.py first.")

    # ── 3. Company profiles ───────────────────────────────────────────────
    profiles_dir = settings.PROFILES_DIR
    profile_files = list(profiles_dir.glob("*.md"))
    if profile_files:
        all_profile_chunks: list[dict] = []
        for profile_path in sorted(profile_files):
            ticker = profile_path.stem.upper()
            text   = profile_path.read_text(encoding="utf-8")
            chunks = chunk_profile(ticker, text)
            all_profile_chunks.extend(chunks)
            log.info("  %s profile: %d chunks", ticker, len(chunks))
        embed_and_store(all_profile_chunks, profiles_col, PROFILE_META_KEYS)
    else:
        log.warning("No profile .md files found in %s", profiles_dir)

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n── Vector store build complete ──────────────────────────────────")
    print(f"  {COLLECTION_FILINGS:<20} {filings_col.count():>6} documents")
    print(f"  {COLLECTION_NEWS:<20} {news_col.count():>6} documents")
    print(f"  {COLLECTION_PROFILES:<20} {profiles_col.count():>6} documents")
    print(f"  Stored at: {settings.CHROMA_DB_PATH}")


# ── Stats ─────────────────────────────────────────────────────────────────────

def print_stats() -> None:
    """Print collection sizes and a sample of metadata from each."""
    client = get_client()
    for name in [COLLECTION_FILINGS, COLLECTION_NEWS, COLLECTION_PROFILES]:
        try:
            col = client.get_collection(name)
            count = col.count()
            print(f"\n── {name} ({count} documents) ──────────────────")
            if count > 0:
                sample = col.get(limit=3, include=["metadatas"])
                for meta in sample["metadatas"]:
                    ticker  = meta.get("ticker", "?")
                    section = meta.get("section_name") or meta.get("source_name") or ""
                    date    = meta.get("filing_date") or meta.get("published_at") or ""
                    print(f"  {ticker:<6} | {section:<35} | {date}")
        except Exception:
            print(f"  '{name}' not found — run build_index() first.")


# ── Interactive Query Test ────────────────────────────────────────────────────

def interactive_query() -> None:
    """
    Simple REPL for testing retrieval quality.
    Type a query, get back the top chunks with their metadata.
    """
    client      = get_client()
    filings_col = get_or_create_collection(client, COLLECTION_FILINGS)
    news_col    = get_or_create_collection(client, COLLECTION_NEWS)

    print("\nVector store query test — type 'quit' to exit")
    print("Commands: [f] filings  [n] news  [c] compare companies")

    while True:
        try:
            raw = input("\nQuery > ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if raw.lower() in ("quit", "exit", "q"):
            break
        if not raw:
            continue

        parts  = raw.split("|")
        query  = parts[0].strip()
        ticker = parts[1].strip().upper() if len(parts) > 1 else None

        print(f"\nSearching filings{f' [{ticker}]' if ticker else ''}...")
        results = retrieve(query, filings_col, ticker=ticker, n_results=4)
        for i, r in enumerate(results, 1):
            meta = r["metadata"]
            print(f"  {i}. [{meta.get('ticker')}] {meta.get('section_name','')} "
                  f"({meta.get('filing_date','')}) — distance: {r['distance']:.3f}")
            print(f"     {r['content'][:120]}...")

        print(f"\nSearching news{f' [{ticker}]' if ticker else ''}...")
        news_results = retrieve(query, news_col, ticker=ticker, n_results=3)
        for i, r in enumerate(news_results, 1):
            meta = r["metadata"]
            print(f"  {i}. [{meta.get('ticker')}] {meta.get('source_name','')} "
                  f"({meta.get('published_at','')[:10]}) — {meta.get('sentiment_label','')}")
            print(f"     {r['content'][:120]}...")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vector_store",
        description="ChromaDB vector store for the CI Briefing Tool",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build",  help="Embed and index all chunks and news articles")
    sub.add_parser("stats",  help="Print collection document counts and samples")
    sub.add_parser("query",  help="Interactive retrieval test (type queries at prompt)")

    reset_p = sub.add_parser("reset", help="Delete all collections and rebuild from scratch")
    reset_p.add_argument("--confirm", action="store_true",
                         help="Required to prevent accidental deletion")

    args = parser.parse_args()

    if args.cmd == "build":
        build_index(reset=False)
    elif args.cmd == "stats":
        print_stats()
    elif args.cmd == "query":
        interactive_query()
    elif args.cmd == "reset":
        if not args.confirm:
            print("Add --confirm to delete and rebuild all collections.")
            print("  python vector_store.py reset --confirm")
        else:
            build_index(reset=True)


if __name__ == "__main__":
    main()