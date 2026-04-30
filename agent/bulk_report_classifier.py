"""Bulk report classifier (v0.21.1).

User feedback:
  "Per-competitor PDF upload is too much friction. Let me drop in a folder
   of mixed annual + quarterly reports for all competitors and have you
   organize them. No hallucination — if you can't tell, mark unmatched."

Pipeline per file:
  1. Filename substring match against competitor canonical names —
     deterministic, high-confidence, no LLM call.
  2. If filename is ambiguous, run LLM disambiguation with a forced
     "matched_entity_id: null" option for "I genuinely can't tell."
  3. Period extraction from filename + first-page text — strict regex,
     no LLM (regex can't hallucinate dates that aren't in the source).
  4. Return a ClassifiedReport with confidence + method + reasoning so
     the UI can surface unmatched / low-confidence items for manual fixup.

Hard rules (the "no hallucination" guarantees):
  - LLM is told explicitly that "matched_entity_id: null" is the correct
    answer when nothing matches well.
  - Period regex returns None on no match — never invents a year/quarter.
  - We never write the synthesized "business_history" artifact during
    classification — only the raw extracted-text artifact tied to the
    matched entity (or untagged if unmatched).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReportPeriod:
    fiscal_year: int | None = None
    quarter: int | None = None  # 1..4 for quarterly; None means "annual or unknown"
    period_label: str = ""       # e.g. "FY2024", "Q3 2024", "Annual 2023"
    is_annual: bool = False
    raw_match: str = ""          # the substring that matched, for debugging


@dataclass
class ClassifiedReport:
    filename: str
    matched_entity_id: int | None = None
    matched_entity_name: str | None = None
    match_confidence: Literal["high", "medium", "low", "none"] = "none"
    match_method: Literal["filename_substring", "body_text_count", "llm_disambiguation", "manual", "none"] = "none"
    period: ReportPeriod | None = None
    reasoning: str = ""
    error: str | None = None
    text_chars: int = 0


# ---------------------------------------------------------------------------
# Period extraction — regex only, no LLM
# ---------------------------------------------------------------------------

# NOTE: \b doesn't work here because filenames typically use `_` separators
# and `_` is a word character — \b between `_` and `Q` doesn't match.
# We use (?<![a-z0-9]) / (?![0-9]) as char-class boundaries instead.
_QUARTER_PATTERNS = [
    re.compile(r"(?<![a-z0-9])Q([1-4])[\s_\-]*([12]\d{3})(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])([12]\d{3})[\s_\-]*Q([1-4])(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])([1-4])Q[\s_\-]*([12]\d{3})(?![0-9])", re.IGNORECASE),
]

# "FY 2024", "FY24", "fiscal 2024", "annual report 2024", "10-K 2024"
_ANNUAL_PATTERNS = [
    re.compile(r"(?<![a-z])FY[\s_\-]*([12]?\d{2,3})(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z])fiscal[\s_\-]*([12]\d{3})(?![0-9])", re.IGNORECASE),
    re.compile(r"(?<![a-z0-9])(?:annual[\s_\-]*report|10[\s_\-]*K|20[\s_\-]*F|40[\s_\-]*F)[\s_\-]*([12]\d{3})(?![0-9])", re.IGNORECASE),
]


def _coerce_year(s: str) -> int | None:
    """Accept '24', '2024', '024' → 2024. Years <50 → 20XX, >=50 → 19XX."""
    s = s.strip()
    if not s.isdigit():
        return None
    n = int(s)
    if 0 <= n < 100:
        return 2000 + n if n < 50 else 1900 + n
    if 1900 <= n <= 2099:
        return n
    return None


def parse_period(filename: str, head_text: str = "", strict: bool = True) -> ReportPeriod | None:
    """Extract fiscal_year + quarter from filename and the document's first
    1000 chars. `strict=True` (default) means we require an explicit
    annual/quarterly cue — a bare year alone is NOT enough.

    Returns None if no period can be confidently extracted; caller should
    save the report as undated rather than guess.
    """
    needles = [filename, (head_text or "")[:1000]]
    combined = " ".join(needles)

    # Quarterly patterns first — more specific.
    for pat in _QUARTER_PATTERNS:
        m = pat.search(combined)
        if m:
            g1, g2 = m.group(1), m.group(2)
            # Determine which is quarter, which is year
            if g1.isdigit() and len(g1) <= 1:
                q, year = int(g1), _coerce_year(g2)
            else:
                year, q = _coerce_year(g1), int(g2)
            if year and q:
                return ReportPeriod(
                    fiscal_year=year, quarter=q,
                    period_label=f"Q{q} {year}", is_annual=False,
                    raw_match=m.group(0),
                )

    # Annual patterns
    for pat in _ANNUAL_PATTERNS:
        m = pat.search(combined)
        if m:
            year = _coerce_year(m.group(1))
            if year:
                return ReportPeriod(
                    fiscal_year=year, quarter=None,
                    period_label=f"FY{year}", is_annual=True,
                    raw_match=m.group(0),
                )

    # Non-strict fallback: a bare 4-digit year in the FILENAME only
    # (not in body text — too noisy). Lower-confidence; caller can decide.
    if not strict:
        m = re.search(r"\b([12]\d{3})\b", filename)
        if m:
            year = _coerce_year(m.group(1))
            if year:
                return ReportPeriod(
                    fiscal_year=year, quarter=None,
                    period_label=f"{year}", is_annual=True,
                    raw_match=m.group(0),
                )

    return None


# ---------------------------------------------------------------------------
# Filename → competitor matching (deterministic)
# ---------------------------------------------------------------------------


def _normalize_for_match(s: str) -> str:
    """Lowercase, strip non-alnum, collapse — so 'Microsoft Azure' and
    'microsoft-azure_2024' both reduce to the same comparable form."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------------------
# v0.21.4: deterministic body-text matcher with structural co-signal gate
#
# Background: an "AI Industry Report 2025" can mention OpenAI 100×, Anthropic
# 30×, Google 25×. Pure dominance ratio (100/30 = 3.3×) above the 3× threshold
# would misattribute the report to OpenAI. Code review (Apr 2026) caught
# this. We now require a STRUCTURAL CO-SIGNAL in addition to dominance:
#
#   (a) competitor name appears in the filename, OR
#   (b) competitor name appears in the first 200 chars of body (cover page), OR
#   (c) competitor name appears within 500 chars of a 10-K/20-F/40-F marker
#       (e.g. "UNITED STATES SECURITIES AND EXCHANGE COMMISSION").
#
# Without ANY of these, dominance alone returns None — we'd rather flag
# unmatched than auto-attribute an industry report to a vendor.
# ---------------------------------------------------------------------------


_SEC_MARKERS = (
    "united states securities and exchange commission",
    "annual report pursuant to section",
    "form 10-k",
    "form 10k",
    "form 20-f",
    "form 20f",
    "form 40-f",
    "form 40f",
)

_LEGAL_SUFFIXES = (
    ", inc.", " inc.", ", inc", " inc",
    ", llc", " llc",
    ", corp.", " corp.", ", corporation", " corporation",
    ", co.", " co.",
    " ltd", " limited",
    " plc",
    ", n.v.", " n.v.",
)


def _strip_legal_suffix(name: str) -> str:
    """Lowercase + strip trailing legal suffix. 'Acme, Inc.' → 'acme'."""
    n = name.lower().strip()
    for sfx in _LEGAL_SUFFIXES:
        if n.endswith(sfx):
            return n[: -len(sfx)].rstrip(", ").strip()
    return n


def _has_structural_signal(needle_lower: str, filename: str, head_text: str, full_text_lower: str) -> bool:
    """True iff `needle_lower` appears in: filename, OR head_text (first 200
    chars of body), OR — for SEC marker case — BOTH marker AND name appear
    within the first 2000 chars of body (real 10-K cover pages have both
    within page 1).

    v0.21.4 (review must-fix #5): the previous implementation was a 500-char
    proximity window around marker-anywhere. An industry report aggregating
    SEC filings ('Below we summarize the FORM 10-K of OpenAI, Anthropic...')
    would still pass. Authentic filings have the marker + company name BOTH
    on the cover page (first 2000 chars). Tightened accordingly.

    `needle_lower` must already be lowercase + suffix-stripped.
    `full_text_lower` must be lowercase (we lowercase once at call site).
    """
    if len(needle_lower) < 3:
        return False
    fn_lower = filename.lower()
    if needle_lower in fn_lower:
        return True
    if needle_lower in head_text.lower():
        return True
    # v0.21.4 tightened: SEC marker AND needle must both appear in the
    # first 2000 chars (cover-page region of an authentic 10-K/20-F).
    cover_region = full_text_lower[:2000]
    if needle_lower in cover_region:
        for marker in _SEC_MARKERS:
            if marker in cover_region:
                return True
    return False


def body_text_match(
    pdf_text: str,
    filename: str,
    competitors: list[dict],
    min_occurrences: int = 5,
    dominance_ratio: float = 3.0,
) -> tuple[int, str, int] | None:
    """Deterministic occurrence-count matcher with structural co-signal gate.

    Returns (entity_id, name, count) when a competitor:
      1. Is mentioned `≥ min_occurrences` in the first 60K chars of body
      2. Beats the runner-up by `≥ dominance_ratio×` (or has ≥20 mentions
         and beats by ≥2× — for the strong-but-noisy case)
      3. ALSO has a structural co-signal (filename / cover page / SEC marker)

    Without all three, returns None. Sub-millisecond for typical inputs.
    """
    if not pdf_text or not competitors:
        return None
    haystack = pdf_text[:60_000].lower()
    head = pdf_text[:200]  # cover-page window for co-signal

    counts: list[tuple[int, str, str, int]] = []  # (id, name, needle_lower, count)
    for c in competitors:
        name = c.get("name") or ""
        if not name or len(name) < 3:
            continue
        needle = _strip_legal_suffix(name)
        if len(needle) < 3:
            continue
        cnt = haystack.count(needle)
        if cnt > 0:
            counts.append((c["id"], name, needle, cnt))

    if not counts:
        return None
    counts.sort(key=lambda x: x[3], reverse=True)
    top_id, top_name, top_needle, top_count = counts[0]
    runner = counts[1][3] if len(counts) > 1 else 0

    # Min-occurrences floor
    if top_count < min_occurrences:
        return None

    # Dominance: 3× and ≥10, OR 2× and ≥20 (strong-but-noisy)
    primary_dominance = top_count >= 10 and top_count >= runner * dominance_ratio
    fallback_dominance = top_count >= 20 and top_count >= runner * 2
    if not (primary_dominance or fallback_dominance):
        return None

    # Co-signal gate (must-fix #1 from code review)
    if not _has_structural_signal(top_needle, filename, head, haystack):
        return None

    return (top_id, top_name, top_count)


def filename_match(filename: str, competitors: list[dict]) -> tuple[int, str, float] | None:
    """Match a filename against competitor names by substring.

    Returns (entity_id, name, score) where score in [0, 1].
    None if nothing scored above 0.5 — caller falls back to LLM.
    """
    fn_norm = _normalize_for_match(filename)
    if not fn_norm:
        return None

    best: tuple[int, str, float] | None = None
    for c in competitors:
        name = c.get("name") or c.get("canonical_name") or ""
        if not name:
            continue
        cn = _normalize_for_match(name)
        if not cn or len(cn) < 3:
            continue
        # Exact substring of competitor name (>= 4 chars) appears verbatim in
        # the filename → strong signal. We don't penalize for date/format
        # suffix noise — that's expected in real filenames.
        if cn in fn_norm and len(cn) >= 4:
            # Score grows with longer competitor names matched. Floor 0.85,
            # cap 1.0. Tie-break by preferring the longer canonical name
            # so "Microsoft Azure Cognitive Services" beats "Microsoft Azure"
            # when both substrings are present.
            score = min(1.0, 0.85 + 0.01 * len(cn))
            if best is None or score > best[2]:
                best = (c["id"], name, score)
        # Filename ⊆ competitor name (e.g. file "openai.pdf" vs
        # competitor "OpenAI Inc.") — also strong.
        elif fn_norm in cn and len(fn_norm) >= 4:
            score = min(0.9, 0.7 + 0.02 * len(fn_norm))
            if best is None or score > best[2]:
                best = (c["id"], name, score)

    if best and best[2] >= 0.7:
        return best
    return None


# ---------------------------------------------------------------------------
# LLM disambiguation — only when filename match is weak/missing
# ---------------------------------------------------------------------------


_LLM_PROMPT = """A user uploaded a financial filing PDF. Your job is to identify which COMPETITOR \
this document is about — or determine that it doesn't clearly belong to ANY of the listed competitors.

DO NOT guess. If the document is ambiguous, generic, or about a company not in the list, \
return matched_entity_id=null. There is NO penalty for saying null.

Competitors in the project:
{competitor_lines}

First {head_chars} chars of the extracted PDF text:
---
{head_text}
---

Filename: {filename}

Return ONLY this JSON:
{{
  "matched_entity_id": <int or null>,
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence — what specific signal in the text or filename let you decide>"
}}

Hard rules:
- If you're <90% sure it's a specific competitor in the list, return null.
- "low" confidence + a non-null id is worse than null + "high" confidence.
- Don't pick a partial-name match (e.g. don't match a Microsoft 10-K to "Microsoft Azure Cognitive Services").
- The filename and the document body must AGREE on a company name. If they conflict, return null.
"""


def llm_classify(filename: str, head_text: str, competitors: list[dict]) -> tuple[int | None, str, str]:
    """Returns (matched_entity_id, confidence, reasoning).
    matched_entity_id may be None — that's a valid "no match" answer.
    """
    if not competitors:
        return (None, "none", "no competitors to match against")

    lines = [f"  - id={c['id']}: {c.get('name', '')!r}" for c in competitors]
    prompt = _LLM_PROMPT.format(
        competitor_lines="\n".join(lines),
        head_chars=min(3000, len(head_text)),
        head_text=(head_text or "")[:3000],
        filename=filename,
    )

    text = _call_llm(prompt)
    if not text:
        return (None, "none", "LLM unavailable")

    parsed = _parse_json(text)
    if not isinstance(parsed, dict):
        return (None, "none", "LLM returned non-JSON")

    raw_id = parsed.get("matched_entity_id")
    confidence = parsed.get("confidence", "low")
    reasoning = (parsed.get("reasoning") or "").strip()[:300]

    if raw_id is None or raw_id == "":
        return (None, confidence, reasoning)

    try:
        eid = int(raw_id)
    except (TypeError, ValueError):
        return (None, "none", f"LLM returned non-int matched_entity_id: {raw_id!r}")

    valid_ids = {c["id"] for c in competitors}
    if eid not in valid_ids:
        return (None, "none", f"LLM returned id {eid} not in competitor list")

    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    return (eid, confidence, reasoning)


def _parse_json(text: str) -> dict | list:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if m:
            text = m.group(1).strip()
    i = text.find("{")
    if i >= 0:
        j = text.rfind("}")
        if j > i:
            text = text[i:j + 1]
    try:
        return json.loads(text)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Top-level classify() function
# ---------------------------------------------------------------------------


def classify(
    filename: str,
    pdf_text: str,
    competitors: list[dict],
    *,
    allow_llm: bool = False,
) -> ClassifiedReport:
    """End-to-end classifier for a single uploaded report.

    Strategy (v0.21.4):
      1. Period extraction from filename + first 1000 chars (strict regex).
      2. Filename → competitor match (deterministic, sub-ms).
      3. Body-text occurrence count + structural co-signal (deterministic, sub-ms).
      4. If allow_llm=True AND nothing matched, ask LLM (opt-in only).
      5. Return ClassifiedReport.

    `allow_llm` defaults to False — bulk uploads must be fast and deterministic.
    The per-file UI passes True if the user explicitly opts into the slower
    "thorough" classification strategy.
    """
    out = ClassifiedReport(filename=filename, text_chars=len(pdf_text or ""))

    # Period — never blocks; no period is fine.
    period = parse_period(filename, pdf_text, strict=True)
    if period is None:
        period = parse_period(filename, "", strict=False)  # filename-only fallback
    out.period = period

    # 1. Filename match (deterministic, sub-ms)
    fm = filename_match(filename, competitors)
    if fm and fm[2] >= 0.7:
        out.matched_entity_id = fm[0]
        out.matched_entity_name = fm[1]
        out.match_confidence = "high" if fm[2] >= 0.9 else "medium"
        out.match_method = "filename_substring"
        out.reasoning = f"Filename '{filename}' contains competitor name {fm[1]!r} (score={fm[2]:.2f})"
        return out

    # 2. Body-text occurrence count + structural co-signal (v0.21.4)
    btm = body_text_match(pdf_text, filename, competitors)
    if btm is not None:
        eid, name, count = btm
        out.matched_entity_id = eid
        out.matched_entity_name = name
        out.match_confidence = "high" if count >= 30 else "medium"
        out.match_method = "body_text_count"
        out.reasoning = f"Body text mentions {name!r} {count} times with structural co-signal (filename / cover-page / SEC marker)"
        return out

    # 3. LLM fallback (opt-in only)
    if allow_llm:
        eid, conf, reasoning = llm_classify(filename, pdf_text, competitors)
        if eid is not None:
            ent = next((c for c in competitors if c["id"] == eid), None)
            out.matched_entity_id = eid
            out.matched_entity_name = ent.get("name") if ent else None
            out.match_confidence = conf  # type: ignore[assignment]
            out.match_method = "llm_disambiguation"
            out.reasoning = reasoning or "LLM disambiguation"
            return out
        # LLM ran but said null — fall through with reasoning preserved
        out.reasoning = reasoning or "LLM said no match"
    else:
        out.reasoning = "Filename + body-text deterministic match failed; LLM disambiguation disabled (allow_llm=False)"

    # No match — explicit
    out.match_confidence = "none"
    return out


# ---------------------------------------------------------------------------
# LLM dispatch — Groq primary, Claude fallback. Mirrors agent.business_history.
# ---------------------------------------------------------------------------


def _call_llm(prompt: str, max_tokens: int = 1024) -> str:
    try:
        from utils import groq_client
        if groq_client.is_available():
            return groq_client.synthesize(prompt, max_tokens=max_tokens)
    except Exception as exc:
        logger.warning("[bulk_classifier] Groq failed: %s — falling back to Claude", exc)
    try:
        from utils import claude_client
        return claude_client.ask(prompt, max_tokens=max_tokens)
    except Exception as exc:
        logger.error("[bulk_classifier] Claude fallback failed: %s", exc)
        return ""
