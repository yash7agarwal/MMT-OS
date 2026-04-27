"""LLM-synthesized narrative for the executive report (v0.17.0).

Six functions, each producing one section of the report. Every function
has the same hard contract:
  - input includes a `source_pool: set[str]` of URLs the synthesizer is
    ALLOWED to cite
  - output is parsed; any claim citing a URL outside `source_pool` is
    DROPPED before returning
  - if the LLM returns nothing usable, return a clearly-marked fallback
    (`"(insufficient evidence to synthesize)"`) — never a hallucination

This pattern was established by `agent/prd_synthesizer.py` (v0.15.0) and
hardened by `agent/extraction_guard.py` (v0.16.0). The contract here is
the same: the LLM is a writing tool, NOT a knowledge source. Every fact
the report states must be in the snapshot.

Total cost: ~6 Sonnet calls per fresh report, ~$0.50–1.50. Cached in
the artifact manifest so re-downloads cost $0.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from utils.claude_client import ask, DEFAULT_MODEL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    title: str
    body: str
    evidence_urls: list[str]


def _extract_urls(text: str) -> list[str]:
    """Pull every URL out of LLM output. Used for the source-URL gate."""
    return re.findall(r"https?://[^\s\)\]\"']+", text or "")


def _gate_urls(text: str, source_pool: set[str]) -> tuple[str, list[str]]:
    """Validate every URL the synthesizer cited is in the allowed pool.

    Returns (gated_text, hallucinated_urls). hallucinated_urls is a log
    signal — surfaces synthesizer drift; we don't try to surgically remove
    them from prose because that would corrupt sentence flow. Instead we
    log + the caller decides whether to keep or drop the section.
    """
    cited = _extract_urls(text)
    hallucinated = [u for u in cited if u not in source_pool]
    if hallucinated:
        logger.warning(
            f"[report_synthesis] {len(hallucinated)} hallucinated URL(s) "
            f"in synthesizer output: {hallucinated[:3]}"
        )
    return text, hallucinated


_FALLBACK = "(insufficient evidence to synthesize this section — see appendix tables for raw data)"


# v0.18.0: tier verification by claim type instead of forcing URL citations
# on every section. Per the user's "zero hallucination ≠ always cite URL"
# correction, common-knowledge sections (industry framing, well-known
# competitive landscape) can answer from training data; only dated /
# quantified / niche claims need strict source-URL grounding.
TIER_COMMON_KNOWLEDGE = "common_knowledge"
TIER_NEEDS_GROUNDING = "needs_grounding"

# Per-section tier defaults. The split reflects: framing + recommendations
# can lean on LLM knowledge; lens insights + regulatory facts cannot
# because they're domain-specific and time-sensitive.
TIER_BY_SECTION: dict[str, str] = {
    "executive_summary": TIER_COMMON_KNOWLEDGE,
    "competitive_framing": TIER_COMMON_KNOWLEDGE,
    "lens_insights": TIER_NEEDS_GROUNDING,
    "regulatory_framing": TIER_NEEDS_GROUNDING,
    "strategic_implications": TIER_COMMON_KNOWLEDGE,
    "recommendations": TIER_NEEDS_GROUNDING,
}


_SYSTEM_GROUNDED = (
    "You are a McKinsey/BCG senior consultant writing for a CEO/board audience. "
    "Tone: confident, specific, evidence-led. Avoid generic platitudes. "
    "CRITICAL: every factual claim must cite a source URL from the data provided. "
    "If you cannot find evidence for a claim, omit the claim — do not fabricate. "
    "If you cannot synthesize anything useful from the data, say so plainly."
)

_SYSTEM_COMMON = (
    "You are a McKinsey/BCG senior consultant writing for a CEO/board audience. "
    "Tone: confident, specific, evidence-led. Avoid generic platitudes. "
    "Use your training knowledge for well-established industry facts and competitor "
    "identification. Cite source URLs from the provided data WHEN they specifically "
    "back a quantified or recent claim — but you do NOT need to cite URLs for "
    "well-known facts the reader can independently verify (e.g. that Yatra is an "
    "online travel agency, that PVC stabilizers are used in plastic manufacturing). "
    "DO NOT fabricate specific numbers, percentages, dates, or quotes. If a "
    "specific datum isn't in the provided data and you don't reliably know it, "
    "omit it or describe directionally (e.g. 'meaningful share' instead of '34%')."
)


def _ask(prompt: str, max_tokens: int = 800, system: str = "", tier: str = TIER_NEEDS_GROUNDING) -> str:
    """Synthesize via Groq (free, fast) with Claude as fallback.

    v0.18.1: report synthesis switched from Claude-primary to Groq-primary.
    Why: report generation is bursty — six LLM calls per report — and burns
    Anthropic credits fast, while Groq's 30 RPM / 14,400 RPD free tier
    handles a typical report (≈10 calls) with room to spare. Claude stays
    as the fallback for the rare case Groq is hard-down or rejecting,
    not for routine traffic. Quality tradeoff: Llama 3.3 70B is a step
    below Sonnet 4.6 on dense analytical writing but well-suited for the
    well-structured prompts we use here. The hallucination guard
    (`_gate_urls`) is provider-agnostic and still applies to Groq output.
    """
    base = _SYSTEM_COMMON if tier == TIER_COMMON_KNOWLEDGE else _SYSTEM_GROUNDED
    full_system = f"{base}\n\n{system}" if system else base

    # Primary: Groq (free, fast, plenty of headroom for a report's ~10 calls)
    try:
        from utils import groq_client
        if groq_client.is_available():
            return groq_client.synthesize(
                prompt=prompt, max_tokens=max_tokens, system=full_system,
            )
    except Exception as exc:
        logger.warning(f"[report_synthesis] Groq failed, falling back to Claude: {exc}")

    # Fallback: Claude — only fires when Groq is unavailable or errored.
    try:
        return ask(prompt, max_tokens=max_tokens, system=full_system, model=DEFAULT_MODEL)
    except Exception as exc:
        logger.error(f"[report_synthesis] both Groq and Claude failed: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Section 1: Executive summary
# ---------------------------------------------------------------------------

def executive_summary(snapshot: dict, source_pool: set[str]) -> str:
    """One-page executive summary. ~250 words.

    Frames the company, market position, and the 2–3 most consequential
    findings. Not a recap of every section — a synthesis that a CEO can
    skim and immediately know what matters.
    """
    portfolio = snapshot.get("portfolio_summary") or "(no homepage portfolio extracted)"
    stats = snapshot.get("stats", {})
    competitors = snapshot.get("competitors") or []
    top_trends = (snapshot.get("trends") or [])[:5]

    competitor_names = ", ".join(c.get("name", "") for c in competitors[:8]) or "(none identified yet)"
    trend_lines = "\n".join(
        f"- {t.get('name','?')} (timeline: {t.get('timeline','?')})"
        for t in top_trends
    ) or "(none surfaced)"

    prompt = f"""Write a 250-word executive summary for the CEO/board of {snapshot['project_name']}.

CONTEXT:
{portfolio}

COMPETITIVE LANDSCAPE: {len(competitors)} tracked competitors — {competitor_names}.

TOP TRENDS:
{trend_lines}

PORTFOLIO METRICS: {stats}

STRUCTURE:
1. Two sentences framing the company in its market.
2. Two-to-three sentences naming the most consequential strategic finding.
3. One sentence on the most pressing regulatory or technology shift.
4. One sentence pointing to where the strategic opportunity sits.

Constraints:
- 250 words MAX, prose paragraphs, no bullet lists, no headers.
- No generic phrases ("in today's landscape", "rapidly evolving market").
- Specific names and numbers wherever the data supports them.
- Do not invent metrics that aren't in the data above."""

    text = _ask(prompt, max_tokens=600, tier=TIER_BY_SECTION["executive_summary"])
    text, _hallucinated = _gate_urls(text, source_pool)
    return text.strip() or _FALLBACK


# ---------------------------------------------------------------------------
# Section 2: Competitive landscape framing
# ---------------------------------------------------------------------------

def competitive_landscape_framing(snapshot: dict, source_pool: set[str]) -> str:
    """200-word framing for the competitive section.

    Names the dominant axes of differentiation, who's winning each axis,
    and where the white space is. Not a list of competitors — a synthesis
    of what the lens-matrix data is telling us.
    """
    competitors = snapshot.get("competitors") or []
    if len(competitors) < 2:
        return _FALLBACK

    matrix = snapshot.get("lens_matrix") or {}
    lenses = matrix.get("lenses") or []

    competitor_block = "\n".join(
        f"- {c.get('name')}: {len([k for k,v in (next((cm for cm in matrix.get('competitors',[]) if cm.get('id')==c['id']), {}).get('lens_counts') or {}).items() if v>0])} lenses with evidence"
        for c in competitors[:10]
    )

    prompt = f"""Write a 200-word framing of the competitive landscape for {snapshot['project_name']}.

DATA:
{len(competitors)} competitors tracked across {len(lenses)} strategic lenses ({', '.join(lenses)}).

PER-COMPETITOR LENS COVERAGE:
{competitor_block}

WRITE:
- Open with the single sentence describing the shape of competition (e.g., "fragmented", "two-leader", "regulatory-driven").
- Then 2–3 sentences naming the lens(es) where competitive intensity is highest and who leads there.
- Then 1–2 sentences on which lens is the most under-defended (white space).
- Close with one sentence on the strategic implication.

Constraints: prose paragraphs, no bullet lists, 200 words MAX, no fabricated facts."""

    text = _ask(prompt, max_tokens=500, tier=TIER_BY_SECTION["competitive_framing"])
    text, _hallucinated = _gate_urls(text, source_pool)
    return text.strip() or _FALLBACK


# ---------------------------------------------------------------------------
# Section 3: Per-lens insights (BATCHED — 1 LLM call for all 8 lenses)
# ---------------------------------------------------------------------------

def lens_insights_batch(snapshot: dict, source_pool: set[str]) -> dict[str, str]:
    """One Claude call returning {lens_name: ~70-word insight} for all 8 lenses.

    Batched for cost — eight short narratives are well-suited to a single
    structured-JSON prompt. Returns a dict keyed by lens name; missing
    lenses (no evidence) get the fallback string.
    """
    matrix = snapshot.get("lens_matrix") or {}
    lenses = matrix.get("lenses") or []
    detail = snapshot.get("lens_detail") or {}

    if not lenses:
        return {}

    # Compact data per lens — top 3 observations, each <200 chars
    lens_data_lines = []
    for lens in lenses:
        lens_obs: list[str] = []
        for entity in (detail.get(lens) or [])[:5]:
            for o in (entity.get("observations") or [])[:2]:
                content = (o.get("content") or "").strip()[:160]
                if content:
                    lens_obs.append(f"  · ({entity.get('name','?')}) {content}")
        block = "\n".join(lens_obs) if lens_obs else "  · (no observations recorded)"
        lens_data_lines.append(f"\n[{lens}]\n{block}")

    prompt = f"""For {snapshot['project_name']}, write a ~70-word strategic-lens insight for EACH of the 8 lenses below.

DATA:
{chr(10).join(lens_data_lines)}

OUTPUT — JSON object exactly this shape:
{{
  "product_craft": "70-word insight or 'insufficient evidence' if no observations",
  "growth": "...",
  "supply": "...",
  "monetization": "...",
  "technology": "...",
  "brand_trust": "...",
  "moat": "...",
  "trajectory": "..."
}}

Each insight should:
- Lead with the most consequential pattern across competitors for that lens
- Name specific competitors / specific tactics where evidence supports
- End with the strategic implication for {snapshot['project_name']}
- Be 60–80 words; no bullets; no preamble; no URLs in the text

Return ONLY the JSON, no other text."""

    raw = _ask(prompt, max_tokens=2000, tier=TIER_BY_SECTION["lens_insights"])
    if not raw:
        return {l: _FALLBACK for l in lenses}

    # Strip markdown code fences if present
    text = raw.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
        result = {}
        for lens in lenses:
            v = parsed.get(lens, "").strip()
            if not v or "insufficient evidence" in v.lower():
                result[lens] = _FALLBACK
            else:
                # Run URL gate even though we asked for no URLs
                v, _h = _gate_urls(v, source_pool)
                result[lens] = v
        return result
    except json.JSONDecodeError as exc:
        logger.warning(f"[report_synthesis] lens_insights JSON parse failed: {exc}")
        return {l: _FALLBACK for l in lenses}


# ---------------------------------------------------------------------------
# Section 4: Regulatory framing
# ---------------------------------------------------------------------------

def regulatory_framing(snapshot: dict, source_pool: set[str]) -> str:
    """150-word framing of the regulatory landscape."""
    regs = snapshot.get("regulations") or []
    if not regs:
        return _FALLBACK

    reg_lines = "\n".join(
        f"- {r.get('name','?')}: {(r.get('description') or '')[:160]}"
        for r in regs[:12]
    )

    prompt = f"""Write a 150-word framing of the regulatory landscape relevant to {snapshot['project_name']}.

REGULATIONS TRACKED:
{reg_lines}

WRITE:
- Open with one sentence on the dominant regulatory theme.
- Cover 2–3 specific compliance pressures with their consequence.
- Close with one sentence on what the company should be preparing for.

Constraints: prose, no bullets, 150 words MAX, no fabricated regulations."""

    text = _ask(prompt, max_tokens=400, tier=TIER_BY_SECTION["regulatory_framing"])
    text, _h = _gate_urls(text, source_pool)
    return text.strip() or _FALLBACK


# ---------------------------------------------------------------------------
# Section 5: Strategic implications (impact cascades)
# ---------------------------------------------------------------------------

def strategic_implications(snapshot: dict, source_pool: set[str]) -> str:
    """200-word framing of the 2nd/3rd-order effects."""
    graph = snapshot.get("impact_graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if not nodes or not edges:
        return _FALLBACK

    # Render the cascade as condensed text for the prompt
    by_id = {n.get("id"): n for n in nodes}
    cascade_lines = []
    for e in edges[:30]:
        src = by_id.get(e.get("from"), {})
        dst = by_id.get(e.get("to"), {})
        if src.get("name") and dst.get("name"):
            cascade_lines.append(f"- {src['name']} → [{e.get('relation','related')}] → {dst['name']}")

    cascade_block = "\n".join(cascade_lines) if cascade_lines else "(no cascade edges recorded)"

    prompt = f"""Identify the most consequential 2nd- and 3rd-order strategic implications for {snapshot['project_name']}.

CASCADE GRAPH:
{cascade_block}

WRITE:
- 200 words.
- Surface the 2–3 cascades that matter most strategically.
- For each: name the trend → name the downstream effect → name the company-level implication.
- Avoid restating the cascade verbatim; synthesize what it MEANS.
- Close with one sentence on what this should change about strategy.

Constraints: prose, no bullets, no fabrication."""

    text = _ask(prompt, max_tokens=500, tier=TIER_BY_SECTION["strategic_implications"])
    text, _h = _gate_urls(text, source_pool)
    return text.strip() or _FALLBACK


# ---------------------------------------------------------------------------
# Section 6: Recommendations (the close — 5–7 numbered, evidence-anchored)
# ---------------------------------------------------------------------------

def recommendations(
    snapshot: dict,
    narrative_pieces: dict[str, str],
    source_pool: set[str],
) -> list[Recommendation]:
    """5–7 numbered recommendations, each with title + body + evidence URLs.

    Depends on all earlier narrative pieces — this is the synthesizer's
    job: fold everything into prescriptive moves the company can execute.
    """
    portfolio = snapshot.get("portfolio_summary") or ""
    summaries = "\n\n".join(
        f"--- {k.upper()} ---\n{v}"
        for k, v in narrative_pieces.items()
        if v and v != _FALLBACK
    ) or "(no upstream narrative — relying on raw data only)"

    # Provide source pool as a numbered list the LLM can cite by reference
    sources_list = "\n".join(
        f"  [{i+1}] {url}" for i, url in enumerate(sorted(source_pool)[:50])
    )

    prompt = f"""Synthesize 5–7 strategic recommendations for {snapshot['project_name']}'s leadership.

CONTEXT:
{portfolio}

UPSTREAM NARRATIVE:
{summaries}

EVIDENCE POOL — cite these URLs by number, e.g. "[3]":
{sources_list}

OUTPUT — JSON array exactly this shape:
[
  {{
    "title": "5-8 word imperative — must start with a verb (Build, Acquire, Reposition, Defend, etc.)",
    "body": "60-word recommendation. Cite evidence references like [3] inline. Be specific about HOW.",
    "evidence_refs": [3, 7]
  }},
  ...
]

Each recommendation must:
- Be ACTIONABLE (a thing leadership can authorize this quarter)
- Have ≥1 evidence reference from the pool
- Address a different strategic axis (don't write 5 recs about the same lens)

Return ONLY the JSON array."""

    raw = _ask(prompt, max_tokens=2400, tier=TIER_BY_SECTION["recommendations"])
    if not raw:
        return []

    text = raw.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(f"[report_synthesis] recommendations JSON parse failed: {exc}")
        return []

    if not isinstance(parsed, list):
        return []

    sources_ordered = sorted(source_pool)
    recs: list[Recommendation] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        body = (item.get("body") or "").strip()
        if not title or not body:
            continue
        # Resolve evidence_refs to URLs
        urls: list[str] = []
        for ref in item.get("evidence_refs") or []:
            try:
                idx = int(ref) - 1
                if 0 <= idx < len(sources_ordered):
                    urls.append(sources_ordered[idx])
            except (ValueError, TypeError):
                continue
        # Recommendations without evidence are dropped — that's the gate.
        if not urls:
            logger.info(f"[report_synthesis] dropped rec (no evidence): {title!r}")
            continue
        recs.append(Recommendation(title=title, body=body, evidence_urls=urls))

    return recs[:7]
