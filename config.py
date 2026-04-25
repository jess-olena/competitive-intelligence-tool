# -*- coding: utf-8 -*-
"""
AI Term Project
Created: 13 April 2026
"""

"""
config.py — Centralized configuration for the Competitive Intelligence Briefing Tool.

All settings are loaded from environment variables (or a .env file in local dev).
Never hardcode secrets. Use .env.example as the template for required variables.

Usage:
    from config import settings
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
"""

from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Project root (the directory containing this file) ──────────────────────
ROOT_DIR = Path(__file__).parent.resolve()


class Settings(BaseSettings):
    """
    All environment variables consumed by the application.
    Pydantic-settings reads from environment + .env file automatically.
    """

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",          # Silently ignore unknown env vars
    )

    # ── Anthropic ─────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = Field(
        ...,
        description="Anthropic API key. Obtain from https://console.anthropic.com",
    )
    CLAUDE_MODEL: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude model ID to use for briefing generation.",
    )
    CLAUDE_MAX_TOKENS: int = Field(
        default=8192,
        description="Maximum tokens in Claude's response per call.",
    )
    CLAUDE_TEMPERATURE: float = Field(
        default=0.2,
        description="Sampling temperature. Lower = more deterministic analysis.",
        ge=0.0,
        le=1.0,
    )

    # ── Embedding Model ───────────────────────────────────────────────────
    VOYAGE_API_KEY: str = Field(
        default="",
        description="Voyage AI API key for financial-domain embeddings. "
                    "Leave empty to use a local sentence-transformers fallback.",
    )
    EMBEDDING_MODEL: str = Field(
        default="voyage-finance-2",
        description="Voyage AI embedding model name. "
                    "Fallback: 'sentence-transformers/all-MiniLM-L6-v2'.",
    )
    EMBEDDING_BATCH_SIZE: int = Field(
        default=32,
        description="Number of text chunks to embed per API call.",
    )
    EMBEDDING_DIMENSION: int = Field(
        default=1024,
        description="Output dimension of the embedding model. "
                    "Must match the ChromaDB collection's configured dimension.",
    )

    # ── SEC EDGAR ─────────────────────────────────────────────────────────
    EDGAR_USER_AGENT: str = Field(
        ...,
        description="Required by SEC EDGAR. Format: 'Company Name email@example.com'. "
                    "See https://www.sec.gov/os/accessing-edgar-data",
    )
    EDGAR_BASE_URL: str = Field(
        default="https://data.sec.gov",
        description="SEC EDGAR HTTPS data endpoint base URL.",
    )
    EDGAR_RATE_LIMIT_RPS: float = Field(
        default=8.0,
        description="Max requests per second to EDGAR (SEC limit is 10/s).",
    )
    EDGAR_FILING_TYPES: list[str] = Field(
        default=["10-K", "10-Q", "8-K", "DEF 14A"],
        description="SEC filing form types to ingest.",
    )
    EDGAR_LOOKBACK_YEARS: int = Field(
        default=3,
        description="Number of years of historical filings to ingest on first run.",
    )

    # ── News API ──────────────────────────────────────────────────────────
    NEWS_API_KEY: str = Field(
        default="",
        description="NewsAPI.org API key. Leave empty to use only RSS sources.",
    )
    NEWS_API_BASE_URL: str = Field(
        default="https://newsapi.org/v2",
        description="NewsAPI base URL.",
    )
    NEWS_LOOKBACK_DAYS: int = Field(
        default=30,
        description="How many days back to fetch news articles on ingestion.",
    )
    NEWS_MAX_ARTICLES_PER_TICKER: int = Field(
        default=200,
        description="Cap on articles ingested per ticker per run.",
    )
    RSS_FEED_URLS: list[str] = Field(
        default=[
            "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",    # WSJ Markets
            "https://feeds.reuters.com/reuters/businessNews",    # Reuters Business
            "https://www.ft.com/rss/home",                       # Financial Times
        ],
        description="RSS feeds to poll for financial news.",
    )

    # ── SQLite ────────────────────────────────────────────────────────────
    SQLITE_DB_PATH: Path = Field(
        default=ROOT_DIR / "db" / "financial.db",
        description="Absolute path to the SQLite database file.",
    )
    SQLITE_ECHO_SQL: bool = Field(
        default=False,
        description="Set True in development to log all SQL statements.",
    )

    # ── ChromaDB ──────────────────────────────────────────────────────────
    CHROMA_DB_PATH: Path = Field(
        default=ROOT_DIR / "db" / "chroma",
        description="Directory where ChromaDB persists its vector collections.",
    )
    CHROMA_COLLECTION_FILINGS: str = Field(
        default="sec_filings",
        description="ChromaDB collection name for SEC filing chunks.",
    )
    CHROMA_COLLECTION_NEWS: str = Field(
        default="news_articles",
        description="ChromaDB collection name for news article chunks.",
    )
    CHROMA_COLLECTION_PROFILES: str = Field(
        default="company_profiles",
        description="ChromaDB collection name for company markdown profile chunks.",
    )
    CHROMA_N_RESULTS: int = Field(
        default=20,
        description="Number of top-K chunks to retrieve per vector query.",
    )

    # ── RAG / Chunking ────────────────────────────────────────────────────
    CHUNK_SIZE_TOKENS: int = Field(
        default=400,
        description="Target chunk size in tokens for text splitting.",
    )
    CHUNK_OVERLAP_TOKENS: int = Field(
        default=50,
        description="Overlap between consecutive chunks to preserve context.",
    )
    RAG_CONTEXT_MAX_TOKENS: int = Field(
        default=6_000,
        description="Maximum tokens of retrieved context to inject into the prompt. "
                    "Must fit within Claude's context window alongside the prompt.",
    )
    HYBRID_SEARCH_ALPHA: float = Field(
        default=0.6,
        description="Weight of vector score vs keyword score in hybrid retrieval. "
                    "1.0 = pure vector, 0.0 = pure BM25 keyword.",
        ge=0.0,
        le=1.0,
    )

    # ── File Paths ────────────────────────────────────────────────────────
    DATA_DIR: Path = Field(
        default=ROOT_DIR / "data",
        description="Root directory for all raw ingested files.",
    )
    FILINGS_DIR: Path = Field(
        default=ROOT_DIR / "data" / "sec_filings",
        description="Subdirectory for raw EDGAR filing documents.",
    )
    NEWS_DIR: Path = Field(
        default=ROOT_DIR / "data" / "news",
        description="Subdirectory for raw news article JSON files.",
    )
    PROFILES_DIR: Path = Field(
        default=ROOT_DIR / "data" / "profiles",
        description="Subdirectory for company markdown profile files.",
    )
    OUTPUTS_DIR: Path = Field(
        default=ROOT_DIR / "outputs",
        description="Root directory for all generated briefing outputs.",
    )
    BRIEFINGS_DIR: Path = Field(
        default=ROOT_DIR / "outputs" / "briefings",
        description="Subdirectory for final competitive intelligence reports.",
    )
    SUMMARIES_DIR: Path = Field(
        default=ROOT_DIR / "outputs" / "summaries",
        description="Subdirectory for intermediate cached chunk summaries.",
    )

    # ── API Server ────────────────────────────────────────────────────────
    API_HOST: str = Field(default="0.0.0.0", description="FastAPI bind host.")
    API_PORT: int = Field(default=8000, description="FastAPI bind port.")
    API_RELOAD: bool = Field(
        default=False,
        description="Enable uvicorn auto-reload (development only).",
    )
    API_WORKERS: int = Field(
        default=1,
        description="Number of uvicorn worker processes.",
    )

    # ── Logging ───────────────────────────────────────────────────────────
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging verbosity: DEBUG | INFO | WARNING | ERROR | CRITICAL.",
    )
    LOG_FILE: Path = Field(
        default=ROOT_DIR / "logs" / "app.log",
        description="Path to the rotating log file. Directory is created on startup.",
    )
    LOG_ROTATION: str = Field(
        default="10 MB",
        description="Loguru log rotation trigger (size or time, e.g. '1 day').",
    )
    LOG_RETENTION: str = Field(
        default="30 days",
        description="How long to keep archived log files.",
    )

    # ── Rate Limiting ─────────────────────────────────────────────────────
    ANTHROPIC_RATE_LIMIT_RPM: int = Field(
        default=50,
        description="Self-imposed Claude API requests per minute ceiling.",
    )
    ANTHROPIC_MAX_RETRIES: int = Field(
        default=4,
        description="Number of retries on transient Anthropic API errors.",
    )

    # ── Validators ────────────────────────────────────────────────────────
    @field_validator("CLAUDE_MODEL")
    @classmethod
    def validate_model(cls, v: str) -> str:
        allowed_prefixes = ("claude-opus", "claude-sonnet", "claude-haiku")
        if not any(v.startswith(p) for p in allowed_prefixes):
            raise ValueError(
                f"CLAUDE_MODEL '{v}' does not look like a valid Claude model ID. "
                f"Expected a model starting with one of: {allowed_prefixes}"
            )
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}, got '{v}'")
        return v.upper()

    def ensure_directories(self) -> None:
        """Create all required data/output directories on startup."""
        dirs = [
            self.DATA_DIR, self.FILINGS_DIR, self.NEWS_DIR, self.PROFILES_DIR,
            self.OUTPUTS_DIR, self.BRIEFINGS_DIR, self.SUMMARIES_DIR,
            self.CHROMA_DB_PATH, self.SQLITE_DB_PATH.parent,
            self.LOG_FILE.parent,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)


# ── Singleton ──────────────────────────────────────────────────────────────
settings = Settings()  # type: ignore[call-arg]

