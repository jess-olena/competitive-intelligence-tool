"""
financials_parser.py
~~~~~~~~~~~~~~~~~~~~
Reads XBRL company-facts JSON files produced by ``edgar_fetcher.py``,
extracts structured financial time-series data, and persists the results to
a local SQLite database.

The module is designed to slot directly into the ``edgar_fetcher`` pipeline::

    # 1. Fetch raw data (edgar_fetcher.py)
    from edgar_fetcher import fetch_all
    fetch_results = fetch_all(TICKERS, CIK_MAP)

    # 2. Parse + store (this module)
    from financials_parser import FinancialsParser
    with FinancialsParser("financials.db") as parser:
        parser.ingest_from_fetch_results(fetch_results)
        data = parser.get_last_8_quarters("AAPL")

It can also be used standalone against any directory of ``*_facts.json``
files, or by passing raw facts dicts programmatically.

Extracted metrics  (us-gaap namespace, with fallback concept aliases)
---------------------------------------------------------------------
    revenue_m            – Revenues / RevenueFromContractWithCustomer…
    net_income_m         – NetIncomeLoss / ProfitLoss
    gross_profit_m       – GrossProfit
    operating_income_m   – OperatingIncomeLoss
    eps_diluted          – EarningsPerShareDiluted
    shares_outstanding_m – CommonStockSharesOutstanding

Derived metrics
---------------
    gross_margin        – gross_profit_m / revenue_m   (0–1 fraction)
    operating_margin    – operating_income_m / revenue_m

All monetary values are normalised to **millions USD**.

CLI
---
    python financials_parser.py ingest-dir  data/edgar/raw  --db financials.db
    python financials_parser.py ingest      AAPL  data/edgar/raw/AAPL_facts.json
    python financials_parser.py query       AAPL  [--quarters 8]  [--annual]
    python financials_parser.py tickers
    python financials_parser.py counts
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging – identical format to edgar_fetcher so output blends in one stream
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Mirrors edgar_fetcher.OUTPUT_DIR so ingest_directory() works out-of-the-box
DEFAULT_RAW_DIR: Path = Path("data/edgar/raw")
DEFAULT_DB:      Path = Path("financials.db")

# SEC fair-use header, re-used when this module fetches directly
_USER_AGENT   = "MyResearchBot/1.0 contact@example.com"
_SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC form types we accept
_ACCEPTED_FORMS: frozenset[str] = frozenset({"10-K", "10-Q", "20-F", "40-F"})
_ANNUAL_FORMS:   frozenset[str] = frozenset({"10-K", "20-F", "40-F"})

# Candidate concept names per metric, tried in priority order (first match wins).
# Multiple aliases cover both old and new US-GAAP taxonomy versions without
# requiring any per-company configuration.
_CONCEPT_CANDIDATES: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenuesNetOfInterestExpense",
    ],
    "net_income": [
        "NetIncomeLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "ProfitLoss",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
        "EarningsPerShareBasicAndDiluted",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssuedAndOutstanding",
    ],
}

# ---------------------------------------------------------------------------
# Database DDL
# ---------------------------------------------------------------------------

_DDL = """
-- ── financial_snapshots ────────────────────────────────────────────────────
-- One row per (ticker, period_end, form_type).  The ON CONFLICT upsert means
-- re-runs refresh figures in place without ever accumulating duplicates.
CREATE TABLE IF NOT EXISTS financial_snapshots (
    ticker               TEXT  NOT NULL,
    period_end           TEXT  NOT NULL,  -- ISO-8601 date:     YYYY-MM-DD
    fiscal_period        TEXT,            -- FY | Q1 | Q2 | Q3 | Q4
    form_type            TEXT,            -- 10-K | 10-Q | 20-F | 40-F
    revenue_m            REAL,            -- millions USD  (NULL if unavailable)
    net_income_m         REAL,            -- millions USD
    gross_profit_m       REAL,            -- millions USD
    operating_income_m   REAL,            -- millions USD
    eps_diluted          REAL,            -- USD per diluted share
    shares_outstanding_m REAL,            -- millions shares
    gross_margin         REAL,            -- 0–1 fraction; NULL when revenue = 0
    operating_margin     REAL,            -- 0–1 fraction; NULL when revenue = 0
    ingested_at          TEXT  NOT NULL,  -- ISO-8601 datetime UTC
    PRIMARY KEY (ticker, period_end, form_type)
);

CREATE INDEX IF NOT EXISTS idx_fs_ticker_period
    ON financial_snapshots (ticker, period_end DESC);
"""

# ON CONFLICT clause targets the composite PK and overwrites every non-key
# column.  A re-run after an updated facts file silently refreshes figures.
_UPSERT_SQL = """
INSERT INTO financial_snapshots (
    ticker, period_end, fiscal_period, form_type,
    revenue_m, net_income_m, gross_profit_m, operating_income_m,
    eps_diluted, shares_outstanding_m,
    gross_margin, operating_margin,
    ingested_at
) VALUES (
    :ticker, :period_end, :fiscal_period, :form_type,
    :revenue_m, :net_income_m, :gross_profit_m, :operating_income_m,
    :eps_diluted, :shares_outstanding_m,
    :gross_margin, :operating_margin,
    :ingested_at
)
ON CONFLICT(ticker, period_end, form_type) DO UPDATE SET
    fiscal_period        = excluded.fiscal_period,
    revenue_m            = excluded.revenue_m,
    net_income_m         = excluded.net_income_m,
    gross_profit_m       = excluded.gross_profit_m,
    operating_income_m   = excluded.operating_income_m,
    eps_diluted          = excluded.eps_diluted,
    shares_outstanding_m = excluded.shares_outstanding_m,
    gross_margin         = excluded.gross_margin,
    operating_margin     = excluded.operating_margin,
    ingested_at          = excluded.ingested_at;
"""

# Column order must match every SELECT that feeds _rows_to_dict()
_COLUMNS: list[str] = [
    "ticker", "period_end", "fiscal_period", "form_type",
    "revenue_m", "net_income_m", "gross_profit_m", "operating_income_m",
    "eps_diluted", "shares_outstanding_m",
    "gross_margin", "operating_margin",
    "ingested_at",
]

# ---------------------------------------------------------------------------
# Low-level XBRL helpers (module-private)
# ---------------------------------------------------------------------------


def _to_millions(value: float | None, unit: str) -> float | None:
    """
    Normalise a raw XBRL numeric value to millions USD.

    Parameters
    ----------
    value:
        The raw numeric value from the XBRL entry, or ``None``.
    unit:
        The XBRL unit label as it appears in the ``units`` dict key,
        e.g. ``"USD"``, ``"USD/shares"``, ``"shares"``.

    Returns
    -------
    float | None
        The value expressed in millions, or ``None`` if *value* is ``None``.

    Notes
    -----
    EPS values (``USD/shares``) are returned unchanged because they are
    already expressed as dollars per share.  Share counts (``shares``) are
    divided by 1 000 000 so they are expressed in millions of shares, which
    keeps the ``shares_outstanding_m`` column dimensionally consistent with
    the monetary columns.
    """
    if value is None:
        return None
    upper = (unit or "").upper()
    if "USD/SHARE" in upper:   # EPS – already per-share, no scaling needed
        return value
    if upper in ("SHARES", "SHARE"):
        return value / 1_000_000
    if "MILLION" in upper:     # rare, but some foreign filers use this
        return value
    if "THOUSAND" in upper:
        return value / 1_000
    return value / 1_000_000   # default: raw USD → millions


def _safe_margin(
    numerator: float | None,
    denominator: float | None,
) -> float | None:
    """Return ``numerator / denominator``, guarding against None and zero."""
    if numerator is None or denominator is None or denominator == 0.0:
        return None
    return numerator / denominator


def _utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_concept(
    facts: dict[str, Any],
    candidates: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Search the ``us-gaap`` (then ``dei``) taxonomy for the first concept in
    *candidates* that has data, and return a deduplicated per-period mapping.

    When multiple filings cover the same ``period_end`` date the function
    retains the most recently *filed* one.  If both an annual and a quarterly
    filing share the same ``period_end``, the annual takes priority.

    Parameters
    ----------
    facts:
        The ``facts`` sub-dict from a company-facts JSON payload.
    candidates:
        Ordered list of XBRL concept names to try.

    Returns
    -------
    dict[str, dict[str, Any]]
        ``{ "YYYY-MM-DD": {"val", "unit", "form", "filed", "is_annual"}, … }``
        Keyed by ``period_end``.  Empty dict if no candidate concept exists.
    """
    gaap = facts.get("us-gaap", {})
    dei  = facts.get("dei",     {})

    for concept in candidates:
        data = gaap.get(concept) or dei.get(concept)
        if not data:
            continue

        # Flatten all unit/entry combinations into a single list of candidates
        raw: list[dict[str, Any]] = []
        for unit_label, entries in data.get("units", {}).items():
            for entry in entries:
                form = entry.get("form", "")
                if form not in _ACCEPTED_FORMS:
                    continue
                end = entry.get("end")
                if not end:
                    continue
                raw.append({
                    "end":       end,
                    "val":       entry.get("val"),
                    "unit":      unit_label,
                    "form":      form,
                    "filed":     entry.get("filed", ""),
                    "is_annual": form in _ANNUAL_FORMS,
                })

        if not raw:
            continue

        # ── Pass 1: deduplicate within (period_end, form_type) ──────────
        # Keep only the most recently filed entry for each exact combo.
        by_end_form: dict[tuple[str, str], dict] = {}
        for row in raw:
            key  = (row["end"], row["form"])
            prev = by_end_form.get(key)
            if prev is None or row["filed"] > prev["filed"]:
                by_end_form[key] = row

        # ── Pass 2: deduplicate across form types for the same period_end ──
        # Annual forms win over quarterly; among equal form priority, prefer
        # the most recently filed entry.
        period_best: dict[str, dict] = {}
        for row in by_end_form.values():
            end  = row["end"]
            prev = period_best.get(end)
            if prev is None:
                period_best[end] = row
            elif row["is_annual"] and not prev["is_annual"]:
                period_best[end] = row                     # annual beats quarterly
            elif row["is_annual"] == prev["is_annual"] and row["filed"] > prev["filed"]:
                period_best[end] = row                     # newer filing wins

        return period_best   # { period_end_str: best_row_dict }

    return {}   # no concept found under any alias


def _fiscal_period_label(form: str, period_end: str) -> str:
    """
    Derive a human-readable fiscal period label.

    Annual forms map to ``"FY"``.  Quarterly forms receive a ``Q1``–``Q4``
    label based on the calendar quarter of *period_end*, which is a reliable
    approximation for most US fiscal calendars.

    Parameters
    ----------
    form:
        SEC form type string, e.g. ``"10-K"`` or ``"10-Q"``.
    period_end:
        Period end date in ``YYYY-MM-DD`` format.

    Returns
    -------
    str
        One of ``"FY"``, ``"Q1"``, ``"Q2"``, ``"Q3"``, ``"Q4"``, or ``"Q?"``.
    """
    if form in _ANNUAL_FORMS:
        return "FY"
    try:
        month = int(period_end[5:7])
    except (ValueError, IndexError):
        return "Q?"
    return {
        1: "Q1", 2: "Q1", 3: "Q1",
        4: "Q2", 5: "Q2", 6: "Q2",
        7: "Q3", 8: "Q3", 9: "Q3",
        10: "Q4", 11: "Q4", 12: "Q4",
    }.get(month, "Q?")


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------


def extract_financial_snapshots(
    ticker: str,
    facts_json: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Parse a raw SEC XBRL company-facts payload and return a list of
    financial snapshot dicts ready for database insertion.

    Each dict contains every column of the ``financial_snapshots`` table.
    Monetary values are expressed in millions USD; EPS is left as dollars per
    diluted share; shares outstanding is in millions of shares.

    Parameters
    ----------
    ticker:
        Exchange ticker symbol, e.g. ``"AAPL"``.  Stored uppercased.
    facts_json:
        Parsed JSON from ``/api/xbrl/companyfacts/CIK{cik}.json``.
        ``edgar_fetcher`` saves this as ``{TICKER}_facts.json``.

    Returns
    -------
    list[dict[str, Any]]
        One dict per (ticker, period_end, form_type) combination found in the
        facts file.  Sorted chronologically by ``period_end``.

    Notes
    -----
    The function handles concept-name variation across GAAP taxonomy versions
    via an alias fallback chain (``_CONCEPT_CANDIDATES``).  When a period
    appears in both 10-K and 10-Q filings the annual filing takes precedence.
    A snapshot row is emitted even when only a subset of metrics is available;
    unavailable columns are stored as ``NULL``.
    """
    ticker       = ticker.upper()
    entity_facts = facts_json.get("facts", {})
    ingested_at  = _utcnow_iso()

    # Build per-period data for every metric in one pass
    concept_data: dict[str, dict[str, dict]] = {
        metric: _extract_concept(entity_facts, candidates)
        for metric, candidates in _CONCEPT_CANDIDATES.items()
    }

    # Union of every period_end seen across all metrics
    all_periods: set[str] = set()
    for metric_map in concept_data.values():
        all_periods.update(metric_map.keys())

    snapshots: list[dict[str, Any]] = []

    for period_end in sorted(all_periods):
        rev_row = concept_data["revenue"].get(period_end)
        ni_row  = concept_data["net_income"].get(period_end)
        gp_row  = concept_data["gross_profit"].get(period_end)
        oi_row  = concept_data["operating_income"].get(period_end)
        eps_row = concept_data["eps_diluted"].get(period_end)
        so_row  = concept_data["shares_outstanding"].get(period_end)

        # Skip periods with no data at all (degenerate union edge-case)
        representative = next(
            (r for r in (rev_row, ni_row, gp_row, oi_row, eps_row, so_row) if r),
            None,
        )
        if representative is None:
            continue

        form_type = representative["form"]

        # ── Monetary values → millions USD ────────────────────────────────
        def _m(row: dict | None, default_unit: str = "USD") -> float | None:
            """Shorthand: extract and scale a single metric row."""
            if row is None:
                return None
            return _to_millions(row["val"], row.get("unit", default_unit))

        revenue_m            = _m(rev_row)
        net_income_m         = _m(ni_row)
        gross_profit_m       = _m(gp_row)
        operating_income_m   = _m(oi_row)
        eps_diluted          = _m(eps_row, "USD/shares")   # returns unchanged
        shares_outstanding_m = _m(so_row,  "shares")       # → millions shares

        # ── Derived ratios ────────────────────────────────────────────────
        gross_margin     = _safe_margin(gross_profit_m,     revenue_m)
        operating_margin = _safe_margin(operating_income_m, revenue_m)

        snapshots.append({
            "ticker":               ticker,
            "period_end":           period_end,
            "fiscal_period":        _fiscal_period_label(form_type, period_end),
            "form_type":            form_type,
            "revenue_m":            revenue_m,
            "net_income_m":         net_income_m,
            "gross_profit_m":       gross_profit_m,
            "operating_income_m":   operating_income_m,
            "eps_diluted":          eps_diluted,
            "shares_outstanding_m": shares_outstanding_m,
            "gross_margin":         gross_margin,
            "operating_margin":     operating_margin,
            "ingested_at":          ingested_at,
        })

    log.info(
        "%s: extracted %d snapshots  (annual=%d  quarterly=%d)",
        ticker,
        len(snapshots),
        sum(1 for s in snapshots if s["form_type"] in _ANNUAL_FORMS),
        sum(1 for s in snapshots if s["form_type"] == "10-Q"),
    )
    return snapshots


# ---------------------------------------------------------------------------
# FinancialsParser – database façade
# ---------------------------------------------------------------------------


class FinancialsParser:
    """
    High-level interface for ingesting XBRL company-facts data into SQLite
    and querying the ``financial_snapshots`` table.

    The class implements the context-manager protocol; using it via ``with``
    guarantees the database connection is closed on exit::

        with FinancialsParser("financials.db") as parser:
            parser.ingest_from_fetch_results(fetch_results)
            rows = parser.get_last_8_quarters("AAPL")

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created (with schema applied) if
        the file does not already exist.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB) -> None:
        self.db_path = Path(db_path)
        self._conn   = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL journal: concurrent readers never block the writer
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._apply_schema()

    # ── Context-manager protocol ──────────────────────────────────────────

    def __enter__(self) -> "FinancialsParser":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    # ── Schema management ─────────────────────────────────────────────────

    def _apply_schema(self) -> None:
        """Create tables / indexes idempotently.  Safe to call on every open."""
        self._conn.executescript(_DDL)
        self._conn.commit()
        log.info("DB ready: %s", self.db_path)

    # ── Internal write helper ─────────────────────────────────────────────

    def _upsert_snapshots(self, snapshots: list[dict[str, Any]]) -> int:
        """
        Bulk-upsert *snapshots* into ``financial_snapshots``.

        Rows whose ``(ticker, period_end, form_type)`` PK already exists are
        updated in place; no duplicate rows are ever created.

        Parameters
        ----------
        snapshots:
            List of dicts as returned by :func:`extract_financial_snapshots`.

        Returns
        -------
        int
            Number of rows processed (inserts + updates combined).
        """
        if not snapshots:
            return 0
        with self._conn:                              # auto-commit / rollback
            self._conn.executemany(_UPSERT_SQL, snapshots)
        return len(snapshots)

    # ── Ingestion – primary entry points ─────────────────────────────────

    def ingest_from_fetch_results(
        self,
        fetch_results: list[dict[str, Any]],
    ) -> dict[str, int]:
        """
        Ingest every company returned by ``edgar_fetcher.fetch_all()``.

        This is the primary integration point between the two modules.  Each
        element of *fetch_results* must contain ``"ticker"`` and
        ``"facts_path"`` keys, exactly as produced by
        :func:`edgar_fetcher.fetch_company`.

        Parameters
        ----------
        fetch_results:
            The list returned by ``edgar_fetcher.fetch_all()``.

        Returns
        -------
        dict[str, int]
            ``{ ticker: rows_upserted }`` for every successfully processed
            company.  Companies that raise an exception are logged and
            omitted from the result.

        Examples
        --------
        ::

            from edgar_fetcher    import fetch_all
            from financials_parser import FinancialsParser

            results = fetch_all(TICKERS, CIK_MAP)          # edgar_fetcher
            with FinancialsParser("financials.db") as p:
                summary = p.ingest_from_fetch_results(results)
                # → {"AAPL": 42, "MSFT": 40, "GOOGL": 38, …}
        """
        summary: dict[str, int] = {}
        for item in fetch_results:
            ticker     = item.get("ticker", "UNKNOWN").upper()
            facts_path = item.get("facts_path")
            if not facts_path:
                log.error(
                    "%s: 'facts_path' key missing in fetch result – skipped.", ticker
                )
                continue
            try:
                summary[ticker] = self.ingest_file(ticker, facts_path)
            except Exception as exc:          # noqa: BLE001
                log.error("%s: ingestion failed – %s", ticker, exc)
        return summary

    def ingest_file(self, ticker: str, path: str | Path) -> int:
        """
        Parse a local ``{TICKER}_facts.json`` file and upsert into the DB.

        This is the expected path when consuming ``edgar_fetcher`` output.

        Parameters
        ----------
        ticker:
            Exchange ticker symbol.
        path:
            Path to the XBRL company-facts JSON file on disk.

        Returns
        -------
        int
            Number of rows upserted.
        """
        path = Path(path)
        log.info("%s: loading facts from %s", ticker.upper(), path)
        with path.open(encoding="utf-8") as fh:
            facts_json = json.load(fh)
        return self.ingest_payload(ticker, facts_json)

    def ingest_payload(self, ticker: str, facts_json: dict[str, Any]) -> int:
        """
        Parse an already-loaded company-facts dict and upsert into the DB.

        Useful for testing, or when the JSON has been fetched and transformed
        in memory before being stored.

        Parameters
        ----------
        ticker:
            Exchange ticker symbol.
        facts_json:
            Parsed JSON payload (``dict`` equivalent of a ``*_facts.json`` file).

        Returns
        -------
        int
            Number of rows upserted.
        """
        snapshots = extract_financial_snapshots(ticker, facts_json)
        n = self._upsert_snapshots(snapshots)
        log.info("%s: upserted %d rows", ticker.upper(), n)
        return n

    def ingest_directory(
        self,
        directory: str | Path = DEFAULT_RAW_DIR,
        pattern: str = "*_facts.json",
    ) -> dict[str, int]:
        """
        Batch-ingest all ``*_facts.json`` files found in *directory*.

        The ticker symbol is inferred from the filename by stripping the
        ``_facts`` suffix, matching the naming convention used by
        ``edgar_fetcher.fetch_company()`` (e.g. ``AAPL_facts.json`` → ``AAPL``).

        Parameters
        ----------
        directory:
            Directory to scan.  Defaults to ``data/edgar/raw``.
        pattern:
            Glob pattern for facts files.

        Returns
        -------
        dict[str, int]
            ``{ ticker: rows_upserted }`` per file.  Files that fail are
            logged; their value is set to ``-1`` in the result dict.
        """
        directory = Path(directory)
        files     = sorted(directory.glob(pattern))
        if not files:
            log.warning(
                "ingest_directory: no files matched '%s' in %s", pattern, directory
            )
        results: dict[str, int] = {}
        for jfile in files:
            # "AAPL_facts.json" → stem "AAPL_facts" → ticker "AAPL"
            ticker = jfile.stem.replace("_facts", "").upper()
            try:
                results[ticker] = self.ingest_file(ticker, jfile)
            except Exception as exc:          # noqa: BLE001
                log.error("Failed to ingest %s (%s): %s", jfile.name, ticker, exc)
                results[ticker] = -1
        log.info(
            "ingest_directory: processed %d file(s) from %s", len(results), directory
        )
        return results

    def ingest_cik(self, ticker: str, cik: str | int) -> int:
        """
        Fetch a company-facts payload live from SEC EDGAR and upsert into DB.

        Bypasses ``edgar_fetcher`` entirely; useful for one-off look-ups.
        The response is **not** saved to disk.

        Parameters
        ----------
        ticker:
            Exchange ticker symbol.
        cik:
            SEC Central Index Key (leading zeros added automatically).

        Returns
        -------
        int
            Number of rows upserted.

        Raises
        ------
        urllib.error.URLError
            On network failure or a non-200 HTTP response from SEC EDGAR.
        """
        cik_padded = str(cik).strip().lstrip("0").zfill(10)
        url = _SEC_FACTS_URL.format(cik=cik_padded)
        log.info("%s: fetching live facts from %s", ticker.upper(), url)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            facts_json = json.loads(resp.read())
        return self.ingest_payload(ticker, facts_json)

    # ── Query API ─────────────────────────────────────────────────────────

    def get_last_8_quarters(
        self,
        ticker: str,
        n: int = 8,
    ) -> dict[str, dict[str, Any]]:
        """
        Return the *n* most-recent quarterly snapshots for *ticker*.

        Only ``10-Q`` filings are included; annual rows are excluded.  Use
        :meth:`get_annual_snapshots` for fiscal year-end figures.

        Parameters
        ----------
        ticker:
            Exchange ticker symbol (case-insensitive).
        n:
            Number of quarters to return.  Defaults to ``8`` (two full fiscal
            years of quarterly data).

        Returns
        -------
        dict[str, dict[str, Any]]
            An ordered mapping of ``period_end → snapshot_dict``, sorted
            **most-recent-first**.  Each snapshot dict contains all columns
            from ``financial_snapshots``::

                {
                  "2024-09-28": {
                    "ticker":               "AAPL",
                    "period_end":           "2024-09-28",
                    "fiscal_period":        "Q4",
                    "form_type":            "10-Q",
                    "revenue_m":            94930.0,
                    "net_income_m":         14736.0,
                    "gross_profit_m":       43887.0,
                    "operating_income_m":   29590.0,
                    "eps_diluted":          0.97,
                    "shares_outstanding_m": 15115.82,
                    "gross_margin":         0.4623,
                    "operating_margin":     0.3117,
                    "ingested_at":          "2025-06-01T12:00:00+00:00"
                  },
                  …
                }

        Returns an empty dict when no quarterly data exists for *ticker*.
        """
        sql = """
            SELECT {cols}
            FROM   financial_snapshots
            WHERE  ticker    = ?
              AND  form_type = '10-Q'
            ORDER BY period_end DESC
            LIMIT  ?;
        """.format(cols=", ".join(_COLUMNS))

        rows = self._conn.execute(sql, (ticker.upper(), n)).fetchall()
        return self._rows_to_dict(rows)

    def get_annual_snapshots(self, ticker: str) -> dict[str, dict[str, Any]]:
        """
        Return all annual (10-K / 20-F / 40-F) snapshots for *ticker*.

        Parameters
        ----------
        ticker:
            Exchange ticker symbol (case-insensitive).

        Returns
        -------
        dict[str, dict[str, Any]]
            Ordered mapping of ``period_end → snapshot_dict``, most-recent-first.
        """
        sql = """
            SELECT {cols}
            FROM   financial_snapshots
            WHERE  ticker    = ?
              AND  form_type IN ('10-K', '20-F', '40-F')
            ORDER BY period_end DESC;
        """.format(cols=", ".join(_COLUMNS))

        rows = self._conn.execute(sql, (ticker.upper(),)).fetchall()
        return self._rows_to_dict(rows)

    def get_snapshots(
        self,
        ticker: str,
        form_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        General-purpose snapshot query with optional filters.

        Parameters
        ----------
        ticker:
            Exchange ticker symbol (case-insensitive).
        form_type:
            Optional form type filter, e.g. ``"10-K"`` or ``"10-Q"``.
        start_date:
            Optional inclusive start date filter (``YYYY-MM-DD``).
        end_date:
            Optional inclusive end date filter (``YYYY-MM-DD``).

        Returns
        -------
        dict[str, dict[str, Any]]
            Ordered mapping of ``period_end → snapshot_dict``, most-recent-first.
        """
        clauses: list[str]  = ["ticker = ?"]
        params:  list[Any]  = [ticker.upper()]

        if form_type:
            clauses.append("form_type = ?")
            params.append(form_type)
        if start_date:
            clauses.append("period_end >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("period_end <= ?")
            params.append(end_date)

        sql = """
            SELECT {cols}
            FROM   financial_snapshots
            WHERE  {where}
            ORDER BY period_end DESC;
        """.format(cols=", ".join(_COLUMNS), where=" AND ".join(clauses))

        rows = self._conn.execute(sql, params).fetchall()
        return self._rows_to_dict(rows)

    def list_tickers(self) -> list[str]:
        """
        Return an alphabetically sorted list of all tickers in the database.

        Returns
        -------
        list[str]
        """
        rows = self._conn.execute(
            "SELECT DISTINCT ticker FROM financial_snapshots ORDER BY ticker;"
        ).fetchall()
        return [r[0] for r in rows]

    def snapshot_counts(self) -> dict[str, dict[str, int]]:
        """
        Return annual and quarterly snapshot counts per ticker.

        Returns
        -------
        dict[str, dict[str, int]]
            ``{ "AAPL": { "annual": 10, "quarterly": 40 }, … }``
        """
        rows = self._conn.execute(
            """
            SELECT
                ticker,
                SUM(CASE WHEN form_type IN ('10-K','20-F','40-F') THEN 1 ELSE 0 END),
                SUM(CASE WHEN form_type = '10-Q' THEN 1 ELSE 0 END)
            FROM  financial_snapshots
            GROUP BY ticker
            ORDER BY ticker;
            """
        ).fetchall()
        return {r[0]: {"annual": r[1], "quarterly": r[2]} for r in rows}

    # ── Internal row-mapping helper ───────────────────────────────────────

    @staticmethod
    def _rows_to_dict(rows: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
        """
        Convert ``sqlite3.Row`` objects to an ordered ``period_end → dict``
        mapping with floats rounded for readability.

        Parameters
        ----------
        rows:
            Raw rows from any ``SELECT … FROM financial_snapshots`` query that
            selects columns in ``_COLUMNS`` order.

        Returns
        -------
        dict[str, dict[str, Any]]
        """
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            rec: dict[str, Any] = dict(zip(_COLUMNS, row))
            # 2 dp for monetary totals and share counts
            for col in (
                "revenue_m", "net_income_m", "gross_profit_m",
                "operating_income_m", "shares_outstanding_m",
            ):
                if rec[col] is not None:
                    rec[col] = round(rec[col], 2)
            # 4 dp for ratios (e.g. 0.4623 = 46.23%)
            for col in ("gross_margin", "operating_margin"):
                if rec[col] is not None:
                    rec[col] = round(rec[col], 4)
            # 4 dp for EPS
            if rec["eps_diluted"] is not None:
                rec["eps_diluted"] = round(rec["eps_diluted"], 4)
            result[rec["period_end"]] = rec
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="financials_parser",
        description=(
            "Parse EDGAR XBRL company-facts JSON files into a SQLite database."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--db", default=str(DEFAULT_DB), metavar="PATH",
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # ingest-dir ─────────────────────────────────────────────────────────
    d = sub.add_parser(
        "ingest-dir",
        help="Batch-ingest all *_facts.json files from a directory.",
    )
    d.add_argument(
        "directory", nargs="?", default=str(DEFAULT_RAW_DIR),
        help=f"Directory to scan (default: {DEFAULT_RAW_DIR})",
    )

    # ingest ─────────────────────────────────────────────────────────────
    i = sub.add_parser("ingest", help="Ingest a single *_facts.json file.")
    i.add_argument("ticker", help="Ticker symbol, e.g. AAPL")
    i.add_argument("path",   help="Path to *_facts.json")

    # fetch (live, from SEC EDGAR) ────────────────────────────────────────
    f = sub.add_parser("fetch", help="Fetch live from SEC EDGAR and ingest.")
    f.add_argument("ticker", help="Ticker symbol, e.g. AAPL")
    f.add_argument("cik",    help="SEC CIK, e.g. 0000320193")

    # query ──────────────────────────────────────────────────────────────
    q = sub.add_parser("query", help="Print snapshots for a ticker.")
    q.add_argument("ticker")
    q.add_argument(
        "--quarters", type=int, default=8,
        help="Number of quarterly periods to return (default: 8)",
    )
    q.add_argument(
        "--annual", action="store_true",
        help="Return annual snapshots instead of quarterly",
    )

    # tickers ────────────────────────────────────────────────────────────
    sub.add_parser("tickers", help="List all tickers in the database.")

    # counts ─────────────────────────────────────────────────────────────
    sub.add_parser("counts", help="Show annual/quarterly snapshot counts per ticker.")

    return p


def _cli() -> None:
    import pprint
    args = _build_arg_parser().parse_args()

    with FinancialsParser(args.db) as parser:

        if args.cmd == "ingest-dir":
            results = parser.ingest_directory(args.directory)
            print("\n── Ingest summary ───────────────────────────────────────")
            print(f"  {'Ticker':<10} {'Rows':>6}")
            print("  " + "─" * 18)
            for ticker, n in sorted(results.items()):
                print(f"  {ticker:<10} {n if n >= 0 else 'ERROR':>6}")

        elif args.cmd == "ingest":
            n = parser.ingest_file(args.ticker, args.path)
            print(f"Upserted {n} rows for {args.ticker.upper()}.")

        elif args.cmd == "fetch":
            n = parser.ingest_cik(args.ticker, args.cik)
            print(f"Upserted {n} rows for {args.ticker.upper()}.")

        elif args.cmd == "query":
            if args.annual:
                data  = parser.get_annual_snapshots(args.ticker)
                label = "annual snapshots"
            else:
                data  = parser.get_last_8_quarters(args.ticker, n=args.quarters)
                label = f"last {args.quarters} quarters"
            if not data:
                print(f"No {label} found for {args.ticker.upper()!r}.")
            else:
                print(f"\n── {args.ticker.upper()} – {label} ──")
                pprint.pprint(data, sort_dicts=False)

        elif args.cmd == "tickers":
            tickers = parser.list_tickers()
            print("\n".join(tickers) if tickers else "(database is empty)")

        elif args.cmd == "counts":
            counts = parser.snapshot_counts()
            print(f"\n  {'Ticker':<10} {'Annual':>7} {'Quarterly':>10}")
            print("  " + "─" * 30)
            for ticker, c in counts.items():
                print(f"  {ticker:<10} {c['annual']:>7} {c['quarterly']:>10}")


if __name__ == "__main__":
    _cli()
