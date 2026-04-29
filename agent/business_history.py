"""Business-history synthesis from annual reports / filings (v0.21.0).

User insight: PMs want a sharper read on a competitor than "what features does
their app have." They want:
  - Why this company makes (or doesn't make) sense in its market
  - How it's actually performing (revenue, margins, growth — the real numbers)
  - The specific business model (SaaS / take-rate / aggregator / ads / mixed)
  - Margin profile + the nuance ("looks like SaaS, actually carries inventory")
  - Contrarian insights — "something most people don't know" — the spicy bits

Two paths land raw text in the system:
  1. Manual upload (PDF) — primary path, always available
  2. SEC EDGAR auto-fetch (US-listed only) — see agent/sec_edgar.py

Both produce extracted text strings, which this module synthesizes into a
structured BusinessProfile. The profile is persisted as a KnowledgeArtifact
of type='business_history' so it shows up in the Reports list and the
Business section of the competitor detail page.

LLM dispatch: Groq primary (free), Claude fallback. ~1-3 calls per
synthesis depending on text size (chunking).
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

# Hard cap so a 300-page filing doesn't blow up our LLM context window.
# 60K chars ≈ ~15K Llama tokens — fits in a single Groq request with room
# for the prompt + structured-output instruction.
MAX_TEXT_CHARS = 60_000

# Pages we care about most in an annual report. The narrative + business
# sections live early; later pages are auditor exhibits / forms.
MAX_PAGES_EXTRACT = 80


@dataclass
class BusinessProfile:
    """Structured synthesis of a company's business landscape."""
    competitor: str
    market_thesis: str = ""           # why this company makes / doesn't make sense
    business_model: str = ""          # SaaS / take-rate / marketplace / aggregator / mixed — with rationale
    margin_profile: str = ""          # rough margins + qualitative
    performance: str = ""             # growth, key metrics — quoted from the source if possible
    contrarian_insights: list[str] = field(default_factory=list)  # 3-5 "most people don't know" items
    nuances: list[str] = field(default_factory=list)              # subtle business-model details
    risks_and_red_flags: list[str] = field(default_factory=list)  # downside signals
    sources: list[str] = field(default_factory=list)              # title/year of each feeding doc
    raw_response: str = ""

    def to_markdown(self) -> str:
        """Render as a markdown blob for KnowledgeArtifact.content_md."""
        out = [f"# Business History · {self.competitor}\n"]
        if self.market_thesis:
            out.append("## Market thesis\n" + self.market_thesis + "\n")
        if self.business_model:
            out.append("## Business model\n" + self.business_model + "\n")
        if self.margin_profile:
            out.append("## Margin profile\n" + self.margin_profile + "\n")
        if self.performance:
            out.append("## Performance\n" + self.performance + "\n")
        if self.contrarian_insights:
            out.append("## Contrarian insights — what most people don't know")
            out.extend(f"- {s}" for s in self.contrarian_insights)
            out.append("")
        if self.nuances:
            out.append("## Business nuances")
            out.extend(f"- {s}" for s in self.nuances)
            out.append("")
        if self.risks_and_red_flags:
            out.append("## Risks & red flags")
            out.extend(f"- {s}" for s in self.risks_and_red_flags)
            out.append("")
        if self.sources:
            out.append("## Sources synthesized")
            out.extend(f"- {s}" for s in self.sources)
            out.append("")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def extract_text_from_pdf_bytes(pdf_bytes: bytes, max_pages: int = MAX_PAGES_EXTRACT) -> tuple[str, dict]:
    """Extract text from PDF bytes in-memory. Returns (text, metadata).

    Returns ('', {...}) if extraction fails or yields too little text
    (likely a scanned-image PDF). Caller should treat that as a soft error.
    """
    from pypdf import PdfReader

    meta: dict = {"page_count": 0, "extracted_pages": 0, "extraction_error": None}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        meta["page_count"] = len(reader.pages)
    except Exception as exc:
        meta["extraction_error"] = f"PDF parse failed: {exc}"
        return "", meta

    chunks: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            txt = page.extract_text() or ""
            if txt.strip():
                chunks.append(txt)
                meta["extracted_pages"] = i + 1
        except Exception as exc:
            logger.debug("[business_history] page %d extract failed: %s", i, exc)
            continue

    full = "\n\n".join(chunks)
    # Collapse repeating whitespace — annual reports have a lot of it.
    full = re.sub(r"[ \t]+", " ", full)
    full = re.sub(r"\n{3,}", "\n\n", full)

    if len(full.strip()) < 1000:
        meta["extraction_error"] = (
            "Less than 1000 chars extracted — likely a scanned-image PDF. "
            "OCR not yet supported; please upload a text-based filing."
        )

    if len(full) > MAX_TEXT_CHARS:
        full = full[:MAX_TEXT_CHARS]
        meta["truncated_at_chars"] = MAX_TEXT_CHARS

    return full, meta


# ---------------------------------------------------------------------------
# Synthesis prompt + JSON parsing
# ---------------------------------------------------------------------------


_SYNTHESIS_PROMPT = """You are a senior equities analyst preparing a sharp, opinionated brief on \
**{competitor}** for a PM at **{project_name}** ({project_description}).

You have these source materials (extracted text from filings / annual reports / IR docs). \
Synthesize them into a STRUCTURED business-history profile that goes well beyond what \
a casual reader would notice.

SOURCES:
{sources_summary}

EXTRACTED TEXT (may be truncated):
---
{combined_text}
---

Produce JSON with these exact keys (no others):

{{
  "market_thesis": "<2-4 sentences on why this company makes (or doesn't make) sense in its market. Reference specific assets, network effects, distribution, capital structure. Don't be generic.>",
  "business_model": "<2-3 sentences identifying the actual model — SaaS, take-rate, aggregator, ads, mixed — with the LEVER that drives revenue. Be specific. 'Marketplace with X% take rate on Y volume' beats 'they charge fees'.>",
  "margin_profile": "<gross margin, operating margin if disclosed, qualitative observation. Quote numbers from the source when available.>",
  "performance": "<recent revenue growth, key metrics — quote specific figures + period from the source. If the data shows DECEL or accel, call it out.>",
  "contrarian_insights": [
    "<a SPECIFIC, non-obvious fact a casual observer wouldn't catch. 1-2 sentences each. 3-5 items.>",
    "<another...>"
  ],
  "nuances": [
    "<a subtle business-model detail. e.g. 'Reports SaaS-like ARR but 30% of revenue is one-time impl fees', 'Headline growth is driven by acquired entities, not organic'>"
  ],
  "risks_and_red_flags": [
    "<concrete downside signal grounded in the filing — e.g. customer concentration, going-concern note, related-party deals, deferred revenue normalization>"
  ]
}}

Hard rules:
- Every claim must be grounded in the text above. If something isn't in the source, omit it.
- NEVER invent numbers, dates, or named entities. Better to write "" than to fabricate.
- Aim for sharp, specific, useful. No hedging adverbs. No "synergies." No "differentiated platform."
- For contrarian_insights specifically — push for things that EVEN a 10-year industry watcher might miss. \
  The bar is "after reading this, the PM should DM me 'whoa, didn't know that.'"

Return ONLY the JSON, no preamble."""


def _parse_json(text: str) -> dict:
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
    return json.loads(text)


def synthesize_business_profile(
    competitor: str,
    project_name: str,
    project_description: str,
    sources: list[dict],  # each: {"title": "...", "text": "...", "year": "..."}
) -> BusinessProfile:
    """Combine source texts and run a single structured-output LLM synthesis.

    `sources` is a list of {title, text, year?} dicts. Each text is included
    verbatim up to a per-source cap; aggregated total is capped at MAX_TEXT_CHARS.
    """
    if not sources:
        return BusinessProfile(competitor=competitor)

    # Budget per source — split MAX_TEXT_CHARS evenly with a hard floor.
    per_source_cap = max(8000, MAX_TEXT_CHARS // max(len(sources), 1))
    sources_summary_lines: list[str] = []
    text_blocks: list[str] = []
    src_titles: list[str] = []
    total_chars = 0
    for i, s in enumerate(sources, 1):
        title = s.get("title", f"Source {i}")
        year = s.get("year") or ""
        body = (s.get("text") or "")[:per_source_cap]
        if not body.strip():
            continue
        if total_chars + len(body) > MAX_TEXT_CHARS:
            body = body[: max(0, MAX_TEXT_CHARS - total_chars)]
            if not body.strip():
                break
        total_chars += len(body)
        sources_summary_lines.append(f"  [{i}] {title}{f' ({year})' if year else ''} — {len(body)} chars")
        text_blocks.append(f"=== SOURCE [{i}] {title} ===\n{body}\n")
        src_titles.append(f"{title}{f' ({year})' if year else ''}")

    if not text_blocks:
        return BusinessProfile(competitor=competitor, sources=src_titles)

    prompt = _SYNTHESIS_PROMPT.format(
        competitor=competitor,
        project_name=project_name,
        project_description=project_description or "(no description)",
        sources_summary="\n".join(sources_summary_lines),
        combined_text="\n\n".join(text_blocks),
    )

    raw = _call_llm(prompt, max_tokens=4096)
    if not raw:
        return BusinessProfile(competitor=competitor, sources=src_titles, raw_response="")

    try:
        parsed = _parse_json(raw)
    except Exception as exc:
        logger.warning("[business_history] JSON parse failed: %s | raw=%r", exc, raw[:300])
        return BusinessProfile(competitor=competitor, sources=src_titles, raw_response=raw)

    if not isinstance(parsed, dict):
        return BusinessProfile(competitor=competitor, sources=src_titles, raw_response=raw)

    def _list_or_empty(v) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    return BusinessProfile(
        competitor=competitor,
        market_thesis=str(parsed.get("market_thesis") or "").strip(),
        business_model=str(parsed.get("business_model") or "").strip(),
        margin_profile=str(parsed.get("margin_profile") or "").strip(),
        performance=str(parsed.get("performance") or "").strip(),
        contrarian_insights=_list_or_empty(parsed.get("contrarian_insights")),
        nuances=_list_or_empty(parsed.get("nuances")),
        risks_and_red_flags=_list_or_empty(parsed.get("risks_and_red_flags")),
        sources=src_titles,
        raw_response=raw,
    )


# ---------------------------------------------------------------------------
# LLM dispatch — Groq primary, Claude fallback. Mirrors agent.llm_search.
# ---------------------------------------------------------------------------


def _call_llm(prompt: str, max_tokens: int = 2048) -> str:
    """Try Groq first; fall back to Claude. Return text or ''."""
    try:
        from utils import groq_client
        if groq_client.is_available():
            return groq_client.synthesize(prompt, max_tokens=max_tokens)
    except Exception as exc:
        logger.warning("[business_history] Groq call failed: %s — falling back to Claude", exc)

    try:
        from utils import claude_client
        return claude_client.ask(prompt, max_tokens=max_tokens)
    except Exception as exc:
        logger.error("[business_history] Claude fallback also failed: %s", exc)
        return ""
