"""SEC EDGAR auto-fetch for US-listed competitors (v0.21.1).

EDGAR is the SEC's filing system — free, no API key, just respects a UA
header. We do a best-effort lookup:
  1. Company name → CIK (10-digit identifier) via EDGAR's full-text search
  2. CIK → most recent 10-K filing index
  3. Filing index → primary document URL → fetch PDF or HTML

Many competitors won't be US-listed — that's expected. The function returns
None on miss; the UI shows an "Auto-fetch unavailable for this entity" message
and the user can fall back to manual upload.

Strict rules:
  - Identify ourselves with a real UA header (SEC requires this)
  - Cache CIK lookups (in-memory, per process) to avoid hammering EDGAR
  - 10-second timeout per request
  - Never fail loudly — return None and log
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# SEC requires a real-looking User-Agent; they will rate-limit anonymous clients.
_UA = "Prism Product Intelligence research@prism-ros.vercel.app"
_TIMEOUT = 10.0

_CIK_CACHE: dict[str, str] = {}


@dataclass
class EdgarReport:
    cik: str
    company_name: str
    form_type: str  # e.g. "10-K", "20-F"
    filed: str  # YYYY-MM-DD
    accession_number: str
    primary_doc_url: str
    raw_text: str  # the document body as text


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def lookup_cik(company_name: str) -> tuple[str, str] | None:
    """Resolve a company name to (cik, official_name). None if not found.

    Uses the public company_tickers.json file — small, refreshed daily,
    no rate limit beyond fairness. Cached in process.
    """
    if company_name in _CIK_CACHE:
        cik = _CIK_CACHE[company_name]
        return (cik, company_name) if cik else None

    try:
        r = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.warning("[edgar] tickers fetch failed: %s", exc)
        _CIK_CACHE[company_name] = ""
        return None

    target = _normalize(company_name)
    # data is keyed by integer-like strings; each value has cik_str, ticker, title
    best: tuple[str, str] | None = None
    best_score = 0
    for _, row in data.items():
        title = (row.get("title") or "")
        ticker = (row.get("ticker") or "")
        norm = _normalize(title)
        # Exact-substring match preferred. Fall back to token overlap.
        if target == norm or target in norm or norm in target:
            score = 100
        else:
            t_tokens = set(target.split())
            n_tokens = set(norm.split())
            if not t_tokens:
                continue
            score = int(100 * len(t_tokens & n_tokens) / len(t_tokens))
        if score > best_score:
            cik = str(row.get("cik_str", "")).zfill(10)
            best_score, best = score, (cik, title)
        if score == 100:
            break

    if best and best_score >= 60:
        _CIK_CACHE[company_name] = best[0]
        return best

    _CIK_CACHE[company_name] = ""
    return None


def fetch_latest_annual_report(company_name: str) -> EdgarReport | None:
    """Fetch most recent 10-K (or 20-F for foreign filers) for a company.

    Returns an EdgarReport with extracted text, or None if:
      - Company isn't US-listed (no CIK match)
      - No annual filings found
      - Fetch / extract failed
    """
    found = lookup_cik(company_name)
    if not found:
        logger.info("[edgar] no CIK found for %r", company_name)
        return None
    cik, official = found

    # Filings index for this CIK
    try:
        idx = httpx.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
        )
        idx.raise_for_status()
        idx_data = idx.json()
    except Exception as exc:
        logger.warning("[edgar] submissions fetch failed for CIK %s: %s", cik, exc)
        return None

    recent = idx_data.get("filings", {}).get("recent", {}) or {}
    forms = recent.get("form") or []
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []

    target_idx = None
    for i, form in enumerate(forms):
        if form in ("10-K", "20-F", "40-F"):
            target_idx = i
            break
    if target_idx is None:
        logger.info("[edgar] no annual filing for %s (CIK %s)", official, cik)
        return None

    accession = accs[target_idx]
    primary = docs[target_idx]
    filed = dates[target_idx]
    form_type = forms[target_idx]
    accession_clean = accession.replace("-", "")
    doc_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_clean}/{primary}"
    )

    try:
        doc = httpx.get(doc_url, headers={"User-Agent": _UA}, timeout=30.0)
        doc.raise_for_status()
    except Exception as exc:
        logger.warning("[edgar] doc fetch failed: %s", exc)
        return None

    raw = doc.text
    # Strip HTML if it's an .htm — minimal, just enough for the LLM to read.
    if doc_url.lower().endswith((".htm", ".html")):
        raw = re.sub(r"<script[\s\S]*?</script>", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<style[\s\S]*?</style>", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()

    # PDF case: caller should detect and route through the PDF extractor.
    # We don't extract here to keep this module thin.

    return EdgarReport(
        cik=cik,
        company_name=official,
        form_type=form_type,
        filed=filed,
        accession_number=accession,
        primary_doc_url=doc_url,
        raw_text=raw,
    )
