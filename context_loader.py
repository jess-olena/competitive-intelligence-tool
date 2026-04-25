# -*- coding: utf-8 -*-
"""
AI Project
Created: 14APR2026
context_loader.py
"""
# -*- coding: utf-8 -*-
"""
context_loader.py
~~~~~~~~~~~~~~~~~
Loads static knowledge context (sector skills files and company profiles)
and assembles them into a single dict ready for injection into Claude prompts.

Usage:
    from context_loader import build_static_context

    ctx = build_static_context(["AAPL", "MSFT"])
    print(ctx["skills"])    # tech sector skills file
    print(ctx["profiles"])  # AAPL and MSFT profiles combined
"""

from pathlib import Path
from config import settings

# ── Sector skills file locations ───────────────────────────────────────────
SKILLS_DIR = Path(__file__).parent / "skills"

SECTOR_SKILLS: dict[str, Path] = {
    "tech":       SKILLS_DIR / "tech_sector.md",
    "energy":     SKILLS_DIR / "energy_sector.md",
    "healthcare": SKILLS_DIR / "healthcare_sector.md",
    "consumer":   SKILLS_DIR / "consumer_sector.md",
}

# ── Company to sector mapping ──────────────────────────────────────────────
COMPANY_SECTORS: dict[str, str] = {
    "AAPL": "tech",       "MSFT": "tech",       "NVDA": "tech",
    "XOM":  "energy",     "NEE":  "energy",      "CVX":  "energy",
    "JNJ":  "healthcare", "LLY":  "healthcare",  "UNH":  "healthcare",
    "AMZN": "consumer",   "WMT":  "consumer",    "MCD":  "consumer",
}


# ── Internal file reader ───────────────────────────────────────────────────

def _read(path: Path) -> str:
    """Read a file and return its text. Returns empty string if not found."""
    if not path.exists():
        print(f"  [WARNING] File not found: {path}")
        return ""
    return path.read_text(encoding="utf-8")


# ── Public loader functions ────────────────────────────────────────────────

def load_sector_skills(ticker: str) -> str:
    """Return the sector skills markdown for the given ticker."""
    sector = COMPANY_SECTORS.get(ticker.upper())
    if not sector:
        print(f"  [WARNING] No sector mapping for ticker: {ticker}")
        return ""
    path = SECTOR_SKILLS.get(sector)
    return _read(path) if path else ""


def load_company_profile(ticker: str) -> str:
    """Return the company profile markdown for the given ticker."""
    path = settings.PROFILES_DIR / f"{ticker.upper()}.md"
    return _read(path)


def build_static_context(tickers: list[str]) -> dict[str, str]:
    """
    Assemble sector skills and company profiles for one or more tickers.

    Deduplicates sector skills so a two-company tech query only injects
    the tech skills file once. Returns a dict with two keys:

        {
            "skills":   "## Sector skills: tech\\n\\n...",
            "profiles": "## Company profile: AAPL\\n\\n..."
        }

    Both values are empty strings if no files are found.
    """
    seen_sectors: set[str] = set()
    skills_blocks: list[str] = []
    profile_blocks: list[str] = []

    for ticker in tickers:
        ticker = ticker.upper()

        # ── Skills (one per sector, deduplicated) ──────────────────────
        sector = COMPANY_SECTORS.get(ticker, "")
        if sector and sector not in seen_sectors:
            skills = load_sector_skills(ticker)
            if skills:
                skills_blocks.append(
                    f"## Sector skills: {sector}\n\n{skills}"
                )
            seen_sectors.add(sector)

        # ── Company profile ────────────────────────────────────────────
        profile = load_company_profile(ticker)
        if profile:
            profile_blocks.append(
                f"## Company profile: {ticker}\n\n{profile}"
            )
        else:
            print(f"  [WARNING] No profile found for {ticker} — "
                  f"add data/profiles/{ticker}.md to fix this.")

    return {
        "skills":   "\n\n---\n\n".join(skills_blocks),
        "profiles": "\n\n---\n\n".join(profile_blocks),
    }