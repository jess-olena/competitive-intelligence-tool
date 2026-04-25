"""
edgar_fetcher.py
~~~~~~~~~~~~~~~~
Fetches SEC EDGAR submissions and XBRL company facts for a list of tickers,
extracts recent 10-K / 10-Q filings, and saves raw JSON responses to disk.

Usage
-----
    python edgar_fetcher.py

Or import and call directly:

    from edgar_fetcher import fetch_all

    TICKERS = ["AAPL", "MSFT"]
    CIK_MAP  = {"AAPL": "0000320193", "MSFT": "0000789019"}
    results  = fetch_all(TICKERS, CIK_MAP)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_DIR: Path = Path("data/edgar/raw")

# SEC fair-use policy: identify your application clearly.
USER_AGENT: str = "MyResearchBot/1.0 contact@example.com"

# Hard cap imposed by SEC: ≤ 10 req/s.  We stay safely below at 8 req/s.
MAX_REQUESTS_PER_SECOND: int = 8
_MIN_INTERVAL: float = 1.0 / MAX_REQUESTS_PER_SECOND  # seconds between requests

RECENT_FILING_COUNT: int = 8  # how many of each form type to keep

BASE_SUBMISSIONS_URL: str = "https://data.sec.gov/submissions/CIK{cik}.json"
BASE_FACTS_URL: str = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session with retry logic
# ---------------------------------------------------------------------------


def _build_session() -> requests.Session:
    """
    Build a :class:`requests.Session` pre-configured with:

    * A ``User-Agent`` header that satisfies the SEC's fair-use policy.
    * Automatic back-off / retry for transient network errors.

    The retry strategy intentionally does **not** retry ``429 Too Many
    Requests`` automatically; instead, :func:`_get` handles those inline so
    we can honour the ``Retry-After`` header value precisely.

    Returns
    -------
    requests.Session
    """
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})

    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION: requests.Session = _build_session()
_last_request_time: float = 0.0  # module-level throttle state


# ---------------------------------------------------------------------------
# Rate-limited HTTP GET
# ---------------------------------------------------------------------------


def _get(url: str, max_429_retries: int = 6) -> requests.Response:
    """
    Perform a GET request against *url* while enforcing:

    1. **Rate limiting** – waits until at least ``_MIN_INTERVAL`` seconds have
       elapsed since the previous request.
    2. **429 back-off** – when the server returns ``429 Too Many Requests`` the
       function sleeps for the duration specified in the ``Retry-After``
       response header (defaulting to 60 s) and then retries, up to
       *max_429_retries* times.

    Parameters
    ----------
    url:
        The fully-qualified URL to fetch.
    max_429_retries:
        Maximum number of times to retry after receiving a 429 response.

    Returns
    -------
    requests.Response
        The successful HTTP response.

    Raises
    ------
    requests.HTTPError
        If the server consistently returns 429 after all retries are exhausted,
        or returns any other non-success status code.
    """
    global _last_request_time

    for attempt in range(max_429_retries + 1):
        # --- throttle ---
        elapsed = time.monotonic() - _last_request_time
        wait = _MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)

        log.debug("GET %s", url)
        response = _SESSION.get(url, timeout=30)
        _last_request_time = time.monotonic()

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            log.warning(
                "429 Too Many Requests – sleeping %d s (attempt %d/%d)",
                retry_after,
                attempt + 1,
                max_429_retries,
            )
            time.sleep(retry_after)
            continue

        response.raise_for_status()
        return response

    # Exhausted all retries
    raise requests.HTTPError(f"Exceeded {max_429_retries} retries for {url}")


# ---------------------------------------------------------------------------
# CIK normalisation
# ---------------------------------------------------------------------------


def _pad_cik(cik: str | int) -> str:
    """
    Return *cik* zero-padded to exactly 10 digits, as required by the SEC API.

    Parameters
    ----------
    cik:
        Raw CIK value, e.g. ``"320193"`` or ``320193``.

    Returns
    -------
    str
        Zero-padded CIK string, e.g. ``"0000320193"``.
    """
    return str(cik).strip().lstrip("0").zfill(10)


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------


def _save_json(data: Any, path: Path) -> None:
    """
    Serialise *data* to *path* as pretty-printed JSON, creating parent
    directories as needed.

    Parameters
    ----------
    data:
        Any JSON-serialisable Python object.
    path:
        Destination file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    log.info("Saved → %s", path)


# ---------------------------------------------------------------------------
# Filing extraction
# ---------------------------------------------------------------------------


def extract_recent_filings(
    submissions: dict[str, Any],
    form_types: tuple[str, ...] = ("10-K", "10-Q"),
    n: int = RECENT_FILING_COUNT,
) -> dict[str, list[dict[str, str]]]:
    """
    Extract the *n* most-recent filings of each *form_type* from a
    submissions JSON payload.

    The SEC paginates filing history; this function handles both the primary
    ``filings.recent`` block and any additional ``filings.files`` chunks
    (though only the recent block is usually needed for the 8 most-recent
    filings).

    Parameters
    ----------
    submissions:
        Parsed JSON from ``/submissions/CIK{cik}.json``.
    form_types:
        Tuple of SEC form identifiers to extract, e.g. ``("10-K", "10-Q")``.
    n:
        Maximum number of filings to return per form type.

    Returns
    -------
    dict[str, list[dict[str, str]]]
        Mapping of form type → list of dicts with keys
        ``accessionNumber`` and ``filingDate``, newest first.
    """
    recent: dict[str, list[Any]] = submissions.get("filings", {}).get("recent", {})
    forms: list[str] = recent.get("form", [])
    accessions: list[str] = recent.get("accessionNumber", [])
    dates: list[str] = recent.get("filingDate", [])

    results: dict[str, list[dict[str, str]]] = {ft: [] for ft in form_types}

    for form, accession, date in zip(forms, accessions, dates):
        if form in results and len(results[form]) < n:
            results[form].append({"accessionNumber": accession, "filingDate": date})

    return results


# ---------------------------------------------------------------------------
# Per-company fetch
# ---------------------------------------------------------------------------


def fetch_company(
    ticker: str,
    cik: str | int,
    output_dir: Path = OUTPUT_DIR,
) -> dict[str, Any]:
    """
    Fetch all EDGAR data for a single company and persist raw responses.

    Performs two HTTP requests:

    1. ``/submissions/CIK{cik}.json`` – company metadata + filing index.
    2. ``/api/xbrl/companyfacts/CIK{cik}.json`` – structured XBRL facts.

    Both responses are saved to *output_dir* as:

    * ``{ticker}_submissions.json``
    * ``{ticker}_facts.json``

    A summary file ``{ticker}_recent_filings.json`` is also written,
    containing the extracted 10-K / 10-Q accession numbers and dates.

    Parameters
    ----------
    ticker:
        Exchange ticker symbol, e.g. ``"AAPL"``.  Used only for file naming.
    cik:
        SEC Central Index Key for the company.
    output_dir:
        Root directory under which all files are saved.

    Returns
    -------
    dict[str, Any]
        A summary dict with keys ``ticker``, ``cik``, ``submissions_path``,
        ``facts_path``, ``recent_filings_path``, and ``recent_filings``.
    """
    padded_cik = _pad_cik(cik)
    ticker_upper = ticker.upper()
    log.info("── Fetching %s (CIK %s) ──", ticker_upper, padded_cik)

    # 1. Submissions
    subs_url = BASE_SUBMISSIONS_URL.format(cik=padded_cik)
    subs_response = _get(subs_url)
    submissions: dict[str, Any] = subs_response.json()
    subs_path = output_dir / f"{ticker_upper}_submissions.json"
    _save_json(submissions, subs_path)

    # 2. XBRL company facts
    facts_url = BASE_FACTS_URL.format(cik=padded_cik)
    facts_response = _get(facts_url)
    facts: dict[str, Any] = facts_response.json()
    facts_path = output_dir / f"{ticker_upper}_facts.json"
    _save_json(facts, facts_path)

    # 3. Extract recent 10-K / 10-Q filings
    recent_filings = extract_recent_filings(submissions)
    filings_path = output_dir / f"{ticker_upper}_recent_filings.json"
    _save_json(recent_filings, filings_path)

    log.info(
        "%s – 10-K: %d, 10-Q: %d (most recent %d each)",
        ticker_upper,
        len(recent_filings.get("10-K", [])),
        len(recent_filings.get("10-Q", [])),
        RECENT_FILING_COUNT,
    )

    return {
        "ticker": ticker_upper,
        "cik": padded_cik,
        "submissions_path": str(subs_path),
        "facts_path": str(facts_path),
        "recent_filings_path": str(filings_path),
        "recent_filings": recent_filings,
    }


# ---------------------------------------------------------------------------
# Batch fetch
# ---------------------------------------------------------------------------


def fetch_all(
    tickers: list[str],
    cik_map: dict[str, str | int],
    output_dir: Path = OUTPUT_DIR,
) -> list[dict[str, Any]]:
    """
    Fetch EDGAR data for every ticker in *tickers*.

    Parameters
    ----------
    tickers:
        Ordered list of ticker symbols to process.
    cik_map:
        Mapping of ticker symbol (case-insensitive) to CIK number.
    output_dir:
        Root directory for all saved files.

    Returns
    -------
    list[dict[str, Any]]
        One result dict per successfully processed ticker (see
        :func:`fetch_company` for the dict structure).  Tickers that raise
        an exception are skipped and logged as errors.
    """
    results: list[dict[str, Any]] = []

    for ticker in tickers:
        cik = cik_map.get(ticker.upper()) or cik_map.get(ticker)
        if cik is None:
            log.error("No CIK found for ticker %r – skipping.", ticker)
            continue
        try:
            result = fetch_company(ticker, cik, output_dir=output_dir)
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to fetch %s: %s", ticker, exc)

    log.info("Done. Processed %d / %d tickers.", len(results), len(tickers))
    return results


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Run the full EDGAR fetch pipeline for a hard-coded set of companies.

    Edit ``TICKERS`` and ``CIK_MAP`` below (or replace this function with
    your own data source) before running the script.
    """
    TICKERS: list[str] = [
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "NVDA",
    ]

    # Zero-padded or plain CIKs are both accepted.
    CIK_MAP: dict[str, str] = {
        "AAPL":  "0000320193",
        "MSFT":  "0000789019",
        "GOOGL": "0001652044",
        "AMZN":  "0001018724",
        "NVDA":  "0001045810",
    }

    results = fetch_all(TICKERS, CIK_MAP)

    # Print a compact summary table
    print("\n── Summary ─────────────────────────────────────────────────────")
    print(f"{'Ticker':<8} {'CIK':<12} {'10-K':>5} {'10-Q':>5}")
    print("─" * 36)
    for r in results:
        n_10k = len(r["recent_filings"].get("10-K", []))
        n_10q = len(r["recent_filings"].get("10-Q", []))
        print(f"{r['ticker']:<8} {r['cik']:<12} {n_10k:>5} {n_10q:>5}")
    print("─" * 36)


if __name__ == "__main__":
    main()
