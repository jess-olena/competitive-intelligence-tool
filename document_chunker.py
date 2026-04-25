"""
document_chunker.py
~~~~~~~~~~~~~~~~~~~
Fetches an SEC EDGAR filing by accession number, extracts plain text via
BeautifulSoup, isolates key 10-K / 10-Q sections (Item 1, 1A, 7, 7A), and
returns token-aware paragraph chunks ready for downstream embedding or LLM
ingestion.

Pipeline position
-----------------
This module slots between ``edgar_fetcher.py`` (raw JSON → disk) and any
embedding / vector-store step::

    edgar_fetcher  →  document_chunker  →  vector store / LLM

Typical usage
-------------
::

    from document_chunker import chunk_filing

    chunks = chunk_filing(
        accession_number="0000320193-23-000106",
        ticker="AAPL",
        cik="0000320193",
        company_name="Apple Inc.",
        sector="Technology",
        filing_date="2023-11-03",
        fiscal_period="FY2023",
    )
    for c in chunks:
        print(c["chunk_id"], c["section_name"], c["token_count"])

Each element of the returned list is a dict with the fields
``chunk_id``, ``ticker``, ``company_name``, ``sector``, ``source_type``,
``filing_date``, ``fiscal_period``, ``section_name``, ``content``, and
``token_count``.

Dependencies
------------
    pip install requests beautifulsoup4 lxml tiktoken

The module reuses the rate-limited HTTP session pattern from
``edgar_fetcher.py`` but creates its own session so it can be used
independently.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests
import tiktoken
from bs4 import BeautifulSoup, NavigableString, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Logging
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

# SEC fair-use policy: identify your application.
USER_AGENT: str = "Jessica Olena jjolena@buffalo.edu"

# Stay safely below SEC's hard cap of 10 req/s.
_MAX_RPS: int = 8
_MIN_INTERVAL: float = 1.0 / _MAX_RPS

# Target token budget per chunk.  Paragraphs that exceed this on their own
# are hard-split at sentence boundaries; chunks stop accumulating paragraphs
# once adding the next would breach the limit.
MAX_TOKENS_PER_CHUNK: int = 400

# tiktoken encoding used to count tokens (matches GPT-4 / text-embedding-3-*).
_TIKTOKEN_ENCODING: str = "cl100k_base"

# SEC base URLs
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_INDEX_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&CIK={cik}&type={form_type}"
    "&dateb=&owner=include&count=1&search_text="
)
_ARCHIVE_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_plain}/{accession_plain}/{accession_dashed}-index.htm"
)
_ARCHIVE_BASE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_plain}/{accession_plain}/"

# Sections we want to extract, keyed by a normalised label.
# Each entry is a list of regex patterns tried in order against the
# plain-text heading line.  Patterns are case-insensitive.
_SECTION_PATTERNS: dict[str, list[str]] = {
    "Item 1 – Business": [
        r"item\s+1[\.\s]*business\b",
        r"item\s+1\b(?!\s*a)",        # bare "Item 1" only if not followed by A
    ],
    "Item 1A – Risk Factors": [
        r"item\s+1a[\.\s]*risk\s+factors",
        r"item\s+1a\b",
    ],
    "Item 7 – MD&A": [
        r"item\s+7[\.\s]*management.{0,10}discussion",
        r"item\s+7\b(?!\s*a)",
    ],
    "Item 7A – Market Risk": [
        r"item\s+7a[\.\s]*quantitative",
        r"item\s+7a\b",
    ],
}

# The ordered list drives the sequential extraction sweep.
_SECTION_ORDER: list[str] = list(_SECTION_PATTERNS.keys())

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

_last_request_time: float = 0.0


def _build_session() -> requests.Session:
    """Return a :class:`requests.Session` with retry logic and SEC headers."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    )
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


def _get(url: str, max_429_retries: int = 6) -> requests.Response:
    """
    Rate-limited GET with inline 429 back-off.

    Mirrors the pattern in ``edgar_fetcher._get`` so both modules obey the
    same SEC fair-use constraints even when run concurrently in different
    threads (note: ``_last_request_time`` is module-level and therefore
    shared only within a single process).

    Parameters
    ----------
    url:
        Fully-qualified URL to fetch.
    max_429_retries:
        How many times to honour a ``Retry-After`` and try again.

    Returns
    -------
    requests.Response
        Successful response (2xx).

    Raises
    ------
    requests.HTTPError
        After exhausting retries or on non-429 error status.
    """
    global _last_request_time

    for attempt in range(max_429_retries + 1):
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

    raise requests.HTTPError(f"Exceeded {max_429_retries} retries for {url}")


# ---------------------------------------------------------------------------
# Accession number helpers
# ---------------------------------------------------------------------------


def _normalise_accession(accession: str) -> tuple[str, str]:
    """
    Return ``(dashed, plain)`` forms of an accession number.

    SEC uses both:

    * Dashed:  ``0000320193-23-000106``  (in URLs and index pages)
    * Plain:   ``000032019323000106``    (in archive directory paths)

    Parameters
    ----------
    accession:
        Accession number in either form.

    Returns
    -------
    tuple[str, str]
        ``(dashed_form, plain_form)``
    """
    plain = accession.replace("-", "")
    # Reconstruct canonical dashed form CIK(10)-YY-SEQ(6)
    dashed = f"{plain[:10]}-{plain[10:12]}-{plain[12:]}"
    return dashed, plain


def _pad_cik(cik: str | int) -> str:
    """Zero-pad *cik* to 10 digits (mirrors ``edgar_fetcher._pad_cik``)."""
    return str(cik).strip().lstrip("0").zfill(10)


# ---------------------------------------------------------------------------
# Filing index → primary document URL
# ---------------------------------------------------------------------------


def _fetch_filing_index(cik: str, accession: str) -> dict[str, str]:
    """
    Download the filing index page and return a mapping of
    ``document_type → relative_filename`` for every document in the filing.

    The index page is an HTML table with columns:
    Document / Description / Type / Size.

    Parameters
    ----------
    cik:
        SEC CIK (any padding is fine; we normalise internally).
    accession:
        Accession number in dashed or plain form.

    Returns
    -------
    dict[str, str]
        Mapping of SEC document type label → filename,
        e.g. ``{"10-K": "aapl-20230930.htm", "EX-21.1": "..."}``
    """
    dashed, plain = _normalise_accession(accession)
    cik_plain = _pad_cik(cik).lstrip("0")  # directory uses un-padded CIK
    url = _ARCHIVE_INDEX_URL.format(
        cik_plain=cik_plain, accession_plain=plain, accession_dashed=dashed
    )
    log.info("Fetching filing index: %s", url)
    resp = _get(url)
    soup = BeautifulSoup(resp.text, "lxml")

    docs: dict[str, str] = {}
    table = soup.find("table", {"class": "tableFile"})
    if table is None:
        # Some index pages use a different structure; fall back to any table.
        table = soup.find("table")
    if table is None:
        log.warning("No document table found in filing index.")
        return docs

    for row in table.find_all("tr")[1:]:  # skip header row
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        # Table columns: Seq | Description | Document | Type | Size
        # Actual column count varies; anchor is always in the 3rd cell (index 2).
        link_tag = cells[2].find("a")
        doc_type = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        if link_tag and doc_type:
            href = link_tag.get("href", "")
            filename = href.split("/")[-1]
            docs[doc_type] = filename

    log.info("Index contains %d document entries.", len(docs))
    return docs


def _primary_document_url(cik: str, accession: str, form_type: str = "10-K") -> str:
    """
    Resolve the URL of the primary HTML document for a filing.

    Strategy (in order):

    1. Exact match on *form_type* in the index (e.g. ``"10-K"``).
    2. Any ``*.htm`` / ``*.html`` file whose type starts with the form type.
    3. The first ``*.htm`` / ``*.html`` file in the index.

    Parameters
    ----------
    cik:
        SEC CIK.
    accession:
        Accession number.
    form_type:
        The filing form type, used to select the primary document.

    Returns
    -------
    str
        Absolute URL of the primary HTML document.

    Raises
    ------
    FileNotFoundError
        If no suitable HTML document is found.
    """
    _, plain = _normalise_accession(accession)
    cik_plain = _pad_cik(cik).lstrip("0")
    base_url = _ARCHIVE_BASE_URL.format(cik_plain=cik_plain, accession_plain=plain)

    docs = _fetch_filing_index(cik, accession)

    def _full(filename: str) -> str:
        return base_url + filename

    # 1. Exact form_type match
    if form_type in docs:
        fn = docs[form_type]
        if fn.lower().endswith((".htm", ".html")):
            log.info("Primary document (exact match): %s", fn)
            return _full(fn)

    # 2. Prefix match on form_type, HTML files only
    for doc_type, filename in docs.items():
        if doc_type.startswith(form_type) and filename.lower().endswith((".htm", ".html")):
            log.info("Primary document (prefix match, type=%s): %s", doc_type, filename)
            return _full(filename)

    # 3. First HTML file
    for filename in docs.values():
        if filename.lower().endswith((".htm", ".html")):
            log.info("Primary document (first HTML fallback): %s", filename)
            return _full(filename)

    raise FileNotFoundError(
        f"No HTML primary document found for accession {accession} "
        f"(CIK {cik}, form {form_type})."
    )


# ---------------------------------------------------------------------------
# HTML → plain text
# ---------------------------------------------------------------------------

# Tags whose content we always discard (navigation, scripts, boilerplate).
_SKIP_TAGS: frozenset[str] = frozenset(
    {"script", "style", "nav", "footer", "header", "noscript", "img", "svg"}
)


def _html_to_paragraphs(html: str) -> list[str]:
    """
    Convert raw HTML to a list of non-empty plain-text paragraph strings.

    Approach
    --------
    * Parse with ``lxml`` for speed and robustness.
    * Skip decorative / structural tags (scripts, styles, nav…).
    * Treat ``<p>``, ``<div>``, ``<br>``, ``<tr>``, and heading tags as
      paragraph / line breaks.
    * Collapse runs of whitespace; drop paragraphs that are pure whitespace
      or shorter than 20 characters (likely table headers, page numbers, etc.).

    Parameters
    ----------
    html:
        Raw HTML string.

    Returns
    -------
    list[str]
        Ordered list of paragraph strings extracted from the document.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove boilerplate sub-trees entirely.
    for tag in soup.find_all(_SKIP_TAGS):
        tag.decompose()

    block_tags = {
        "p", "div", "tr", "li",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "blockquote",
    }

    paragraphs: list[str] = []
    current_parts: list[str] = []

    def _flush() -> None:
        text = " ".join(current_parts).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) >= 20:
            paragraphs.append(text)
        current_parts.clear()

    def _walk(node: Tag | NavigableString) -> None:
        if isinstance(node, NavigableString):
            text = str(node)
            if text.strip():
                current_parts.append(text.strip())
            return

        tag_name = node.name.lower() if node.name else ""

        if tag_name in _SKIP_TAGS:
            return

        # <br> acts as a flush point but doesn't guarantee a new paragraph;
        # handle it before recursing into children.
        if tag_name == "br":
            _flush()
            return

        is_block = tag_name in block_tags
        if is_block:
            _flush()

        for child in node.children:
            _walk(child)

        if is_block:
            _flush()

    body = soup.body or soup
    _walk(body)
    _flush()  # catch any trailing text

    return paragraphs


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


@dataclass
class _Section:
    name: str
    paragraphs: list[str] = field(default_factory=list)


def _is_section_heading(text: str, patterns: list[str]) -> bool:
    """
    Return True if *text* matches any of *patterns* (case-insensitive).

    The text is stripped and compared against a compiled regex that also
    allows for leading/trailing whitespace or punctuation, matching typical
    SEC filing heading formats such as:

        "ITEM 1.  BUSINESS"
        "Item 1A. Risk Factors"
        "ITEM 7 — MANAGEMENT'S DISCUSSION AND ANALYSIS"
    """
    clean = text.strip()
    for pattern in patterns:
        if re.search(pattern, clean, re.IGNORECASE):
            return True
    return False


def _extract_sections(paragraphs: list[str]) -> dict[str, list[str]]:
    """
    Sweep *paragraphs* once from top to bottom, assigning each paragraph to
    whichever target section is currently "open".

    A section opens when its heading pattern is matched and closes when the
    next target section's heading pattern is matched (or end-of-document).
    Paragraphs before the first recognised heading are discarded (they are
    usually the table of contents / cover page).

    Parameters
    ----------
    paragraphs:
        Ordered list of plain-text paragraphs from :func:`_html_to_paragraphs`.

    Returns
    -------
    dict[str, list[str]]
        Mapping of section label → list of paragraph strings.
        Sections not found in the document are absent from the dict.
    """
    # Precompile patterns for each section.
    compiled: dict[str, list[re.Pattern[str]]] = {
        label: [re.compile(p, re.IGNORECASE) for p in pats]
        for label, pats in _SECTION_PATTERNS.items()
    }

    sections: dict[str, list[str]] = {label: [] for label in _SECTION_ORDER}
    current_section: str | None = None

    # We match against the first 300 characters of each paragraph to avoid
    # false positives from body text that happens to start with "Item".
    _HEADING_WINDOW = 300

    for para in paragraphs:
        probe = para[:_HEADING_WINDOW]

        matched_section: str | None = None
        for label in _SECTION_ORDER:
            pats = compiled[label]
            if any(p.search(probe) for p in pats):
                # Only treat as a heading if the paragraph is relatively short
                # (genuine headings are rarely > 200 chars).
                if len(para) <= 200:
                    matched_section = label
                    break

        if matched_section is not None:
            current_section = matched_section
            # Don't store the heading line itself in the section body.
            continue

        if current_section is not None:
            sections[current_section].append(para)

    # Drop empty sections so callers can check `label in result` cleanly.
    return {label: paras for label, paras in sections.items() if paras}


# ---------------------------------------------------------------------------
# Tokenisation and chunking
# ---------------------------------------------------------------------------

_ENCODER = tiktoken.get_encoding(_TIKTOKEN_ENCODING)


def _count_tokens(text: str) -> int:
    """Return the number of tokens in *text* using the module-level encoder."""
    return len(_ENCODER.encode(text))


def _split_long_paragraph(para: str, max_tokens: int) -> list[str]:
    """
    Hard-split a single paragraph that exceeds *max_tokens* at sentence
    boundaries.

    Falls back to word-boundary splitting if sentence splitting still yields
    over-long fragments (e.g., extremely long financial data strings).

    Parameters
    ----------
    para:
        A paragraph string that is known to exceed *max_tokens*.
    max_tokens:
        Target maximum token count for each resulting fragment.

    Returns
    -------
    list[str]
        One or more text fragments each ≤ *max_tokens* tokens (best-effort).
    """
    # Try sentence-boundary splitting first.
    sentences = re.split(r"(?<=[.!?])\s+", para)
    fragments: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = _count_tokens(sent)
        if current_tokens + sent_tokens > max_tokens and current:
            fragments.append(" ".join(current))
            current = [sent]
            current_tokens = sent_tokens
        else:
            current.append(sent)
            current_tokens += sent_tokens

    if current:
        fragments.append(" ".join(current))

    # If any fragment still exceeds the limit, word-split it.
    final: list[str] = []
    for frag in fragments:
        if _count_tokens(frag) <= max_tokens:
            final.append(frag)
        else:
            words = frag.split()
            buf: list[str] = []
            buf_tokens = 0
            for word in words:
                w_tokens = _count_tokens(word + " ")
                if buf_tokens + w_tokens > max_tokens and buf:
                    final.append(" ".join(buf))
                    buf = [word]
                    buf_tokens = w_tokens
                else:
                    buf.append(word)
                    buf_tokens += w_tokens
            if buf:
                final.append(" ".join(buf))

    return [f for f in final if f.strip()]


def _chunk_paragraphs(
    paragraphs: list[str], max_tokens: int = MAX_TOKENS_PER_CHUNK
) -> list[tuple[str, int]]:
    """
    Greedily pack paragraphs into chunks of at most *max_tokens* tokens.

    A paragraph that is itself larger than *max_tokens* is first split via
    :func:`_split_long_paragraph`; its fragments are then packed like any
    other paragraph.

    Parameters
    ----------
    paragraphs:
        Ordered list of paragraph strings for one section.
    max_tokens:
        Token budget per chunk.

    Returns
    -------
    list[tuple[str, int]]
        List of ``(chunk_text, token_count)`` pairs.
    """
    # Expand any over-sized paragraphs first.
    expanded: list[str] = []
    for para in paragraphs:
        if _count_tokens(para) > max_tokens:
            expanded.extend(_split_long_paragraph(para, max_tokens))
        else:
            expanded.append(para)

    chunks: list[tuple[str, int]] = []
    current_parts: list[str] = []
    current_tokens = 0

    for para in expanded:
        para_tokens = _count_tokens(para)
        if current_tokens + para_tokens > max_tokens and current_parts:
            # Flush current chunk before starting a new one.
            text = "\n\n".join(current_parts)
            chunks.append((text, _count_tokens(text)))
            current_parts = [para]
            current_tokens = para_tokens
        else:
            current_parts.append(para)
            current_tokens += para_tokens

    if current_parts:
        text = "\n\n".join(current_parts)
        chunks.append((text, _count_tokens(text)))

    return chunks


# ---------------------------------------------------------------------------
# Chunk dict construction
# ---------------------------------------------------------------------------


def _build_chunk_id(ticker: str, accession: str, section_label: str, idx: int) -> str:
    """
    Return a deterministic, URL-safe chunk identifier.

    Format::

        {TICKER}_{ACCESSION_PLAIN}_{SECTION_SLUG}_{IDX:04d}

    Example::

        AAPL_000032019323000106_item1_business_0001
    """
    _, plain = _normalise_accession(accession)
    slug = re.sub(r"[^a-z0-9]+", "_", section_label.lower()).strip("_")
    return f"{ticker.upper()}_{plain}_{slug}_{idx:04d}"


def _make_chunk(
    *,
    chunk_id: str,
    ticker: str,
    company_name: str,
    sector: str,
    source_type: str,
    filing_date: str,
    fiscal_period: str,
    section_name: str,
    content: str,
    token_count: int,
) -> dict[str, Any]:
    """Assemble and return the canonical chunk dictionary."""
    return {
        "chunk_id": chunk_id,
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "source_type": source_type,
        "filing_date": filing_date,
        "fiscal_period": fiscal_period,
        "section_name": section_name,
        "content": content,
        "token_count": token_count,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chunk_filing(
    accession_number: str,
    ticker: str,
    cik: str | int,
    company_name: str,
    sector: str,
    filing_date: str,
    fiscal_period: str,
    form_type: str = "10-K",
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
) -> list[dict[str, Any]]:
    """
    Fetch, parse, and chunk a single SEC EDGAR filing.

    This is the module's primary entry point.  It:

    1. Resolves the primary HTML document URL from the filing index.
    2. Downloads the HTML.
    3. Extracts plain-text paragraphs with BeautifulSoup.
    4. Identifies and extracts Item 1 (Business), Item 1A (Risk Factors),
       Item 7 (MD&A), and Item 7A (Market Risk) via regex pattern matching.
    5. Chunks each section at paragraph boundaries, respecting *max_tokens*.
    6. Returns a flat list of chunk dicts.

    Sections that are absent from the document are silently skipped; the
    caller receives only the chunks that could be extracted.

    Parameters
    ----------
    accession_number:
        SEC accession number in either dashed (``0000320193-23-000106``) or
        plain (``000032019323000106``) form.
    ticker:
        Exchange ticker symbol (e.g. ``"AAPL"``).  Stored verbatim in chunks.
    cik:
        SEC Central Index Key.  Padding is handled internally.
    company_name:
        Human-readable company name stored in every chunk for convenience.
    sector:
        Industry / sector label (e.g. ``"Technology"``).
    filing_date:
        ISO-8601 filing date string (``"YYYY-MM-DD"``).
    fiscal_period:
        Fiscal period label (e.g. ``"FY2023"`` or ``"Q2 2024"``).
    form_type:
        SEC form type used to select the primary document from the filing
        index.  Defaults to ``"10-K"``.
    max_tokens:
        Maximum number of tokens per chunk.  Defaults to
        :data:`MAX_TOKENS_PER_CHUNK` (400).

    Returns
    -------
    list[dict[str, Any]]
        List of chunk dicts.  Each dict has these keys:

        ``chunk_id``
            Deterministic identifier: ``{TICKER}_{ACCESSION}_{SECTION}_{SEQ}``.
        ``ticker``
            Ticker symbol.
        ``company_name``
            Company name.
        ``sector``
            Sector / industry label.
        ``source_type``
            The *form_type* argument (e.g. ``"10-K"``).
        ``filing_date``
            ISO-8601 filing date.
        ``fiscal_period``
            Fiscal period label.
        ``section_name``
            One of the four target section labels (e.g. ``"Item 1 – Business"``).
        ``content``
            Plain-text chunk content.
        ``token_count``
            Number of tokens in *content* per the ``cl100k_base`` encoder.

    Raises
    ------
    FileNotFoundError
        If no HTML primary document is found in the filing index.
    requests.HTTPError
        On unrecoverable HTTP errors fetching SEC resources.

    Examples
    --------
    >>> chunks = chunk_filing(
    ...     accession_number="0000320193-23-000106",
    ...     ticker="AAPL",
    ...     cik="0000320193",
    ...     company_name="Apple Inc.",
    ...     sector="Technology",
    ...     filing_date="2023-11-03",
    ...     fiscal_period="FY2023",
    ... )
    >>> print(len(chunks), "chunks produced")
    >>> print(chunks[0]["section_name"], "–", chunks[0]["token_count"], "tokens")
    """
    padded_cik = _pad_cik(cik)
    dashed_acc, _ = _normalise_accession(accession_number)

    # ── Step 1: resolve primary document URL ─────────────────────────────
    primary_url = _primary_document_url(padded_cik, dashed_acc, form_type)

    # ── Step 2: download HTML ────────────────────────────────────────────
    log.info("Downloading primary document: %s", primary_url)
    html_response = _get(primary_url)
    html_text = html_response.text

    # ── Step 3: HTML → paragraph list ───────────────────────────────────
    log.info("Parsing HTML and extracting paragraphs…")
    paragraphs = _html_to_paragraphs(html_text)
    log.info("Extracted %d paragraphs.", len(paragraphs))

    # ── Step 4: section extraction ───────────────────────────────────────
    log.info("Extracting target sections…")
    sections = _extract_sections(paragraphs)
    found = list(sections.keys())
    missing = [s for s in _SECTION_ORDER if s not in sections]

    if found:
        log.info("Found sections: %s", ", ".join(found))
    if missing:
        log.warning("Sections not found (will be skipped): %s", ", ".join(missing))

    # ── Step 5 & 6: chunk and build output dicts ─────────────────────────
    all_chunks: list[dict[str, Any]] = []

    for section_label in _SECTION_ORDER:
        if section_label not in sections:
            continue

        section_paragraphs = sections[section_label]
        raw_chunks = _chunk_paragraphs(section_paragraphs, max_tokens=max_tokens)

        log.info(
            "  %-30s → %3d paragraphs → %3d chunks",
            section_label,
            len(section_paragraphs),
            len(raw_chunks),
        )

        for idx, (content, token_count) in enumerate(raw_chunks, start=1):
            chunk_id = _build_chunk_id(ticker, dashed_acc, section_label, idx)
            all_chunks.append(
                _make_chunk(
                    chunk_id=chunk_id,
                    ticker=ticker.upper(),
                    company_name=company_name,
                    sector=sector,
                    source_type=form_type,
                    filing_date=filing_date,
                    fiscal_period=fiscal_period,
                    section_name=section_label,
                    content=content,
                    token_count=token_count,
                )
            )

    log.info(
        "chunk_filing complete: %d total chunks for %s %s.",
        len(all_chunks),
        ticker.upper(),
        dashed_acc,
    )
    return all_chunks


def chunk_filings_from_fetch_results(
    fetch_results: list[dict[str, Any]],
    company_meta: dict[str, dict[str, str]],
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
) -> list[dict[str, Any]]:
    """
    Batch-process filings produced by ``edgar_fetcher.fetch_all``.

    This convenience wrapper iterates over the ``recent_filings`` inside each
    ``fetch_all`` result dict, chunking the most-recent 10-K and 10-Q
    for each ticker.

    Parameters
    ----------
    fetch_results:
        Return value of ``edgar_fetcher.fetch_all()``.  Each element must
        have the keys ``ticker``, ``cik``, and ``recent_filings`` (a dict
        keyed by form type whose values are lists of
        ``{"accessionNumber": …, "filingDate": …}`` dicts).
    company_meta:
        Mapping of uppercase ticker → dict with optional keys:

        * ``"company_name"`` – defaults to the ticker if absent.
        * ``"sector"``       – defaults to ``"Unknown"`` if absent.
        * ``"fiscal_period"``– defaults to ``""`` if absent.
    max_tokens:
        Token budget per chunk; forwarded to :func:`chunk_filing`.

    Returns
    -------
    list[dict[str, Any]]
        Flat list of all chunks across all tickers and form types.

    Notes
    -----
    Filings that raise exceptions are logged and skipped so that one bad
    filing does not abort the entire batch.  Only the single most-recent
    filing of each form type is chunked per ticker.
    """
    all_chunks: list[dict[str, Any]] = []

    for result in fetch_results:
        ticker = result["ticker"].upper()
        cik = result["cik"]
        meta = company_meta.get(ticker, {})
        company_name = meta.get("company_name", ticker)
        sector = meta.get("sector", "Unknown")

        for form_type in ("10-K", "10-Q"):
            filings = result.get("recent_filings", {}).get(form_type, [])
            if not filings:
                log.warning("%s – no %s filings found, skipping.", ticker, form_type)
                continue

            most_recent = filings[0]  # newest first (edgar_fetcher ordering)
            accession = most_recent["accessionNumber"]
            filing_date = most_recent["filingDate"]
            fiscal_period = meta.get("fiscal_period", "")

            try:
                chunks = chunk_filing(
                    accession_number=accession,
                    ticker=ticker,
                    cik=cik,
                    company_name=company_name,
                    sector=sector,
                    filing_date=filing_date,
                    fiscal_period=fiscal_period,
                    form_type=form_type,
                    max_tokens=max_tokens,
                )
                all_chunks.extend(chunks)
                log.info(
                    "%s %s (%s): %d chunks added.",
                    ticker,
                    form_type,
                    filing_date,
                    len(chunks),
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Failed to chunk %s %s %s: %s",
                    ticker,
                    form_type,
                    accession,
                    exc,
                )

    return all_chunks


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _cli() -> None:
    """
    Minimal CLI for ad-hoc testing::

        python document_chunker.py \\
            --ticker AAPL \\
            --cik 0000320193 \\
            --accession 0000320193-23-000106 \\
            --company "Apple Inc." \\
            --sector Technology \\
            --filing-date 2023-11-03 \\
            --fiscal-period FY2023

    Prints a summary table to stdout.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="document_chunker",
        description="Fetch and chunk an SEC EDGAR 10-K / 10-Q filing.",
    )
    parser.add_argument("--ticker",        required=True,  help="Ticker symbol, e.g. AAPL")
    parser.add_argument("--cik",           required=True,  help="SEC CIK, e.g. 0000320193")
    parser.add_argument("--accession",     required=True,  help="Accession number")
    parser.add_argument("--company",       default="",     help="Company name")
    parser.add_argument("--sector",        default="Unknown", help="Sector label")
    parser.add_argument("--filing-date",   default="",     dest="filing_date")
    parser.add_argument("--fiscal-period", default="",     dest="fiscal_period")
    parser.add_argument("--form-type",     default="10-K", dest="form_type")
    parser.add_argument("--max-tokens",    default=MAX_TOKENS_PER_CHUNK, type=int,
                        dest="max_tokens")
    args = parser.parse_args()

    chunks = chunk_filing(
        accession_number=args.accession,
        ticker=args.ticker,
        cik=args.cik,
        company_name=args.company or args.ticker,
        sector=args.sector,
        filing_date=args.filing_date,
        fiscal_period=args.fiscal_period,
        form_type=args.form_type,
        max_tokens=args.max_tokens,
    )

    print(f"\n── {args.ticker.upper()} {args.accession} ({'─' * 30}")
    print(f"{'chunk_id':<55} {'section':<25} {'tokens':>6}")
    print("─" * 90)
    for c in chunks:
        print(f"{c['chunk_id']:<55} {c['section_name']:<25} {c['token_count']:>6}")
    print("─" * 90)
    print(f"Total chunks: {len(chunks)}")


if __name__ == "__main__":
    _cli()
