"""LLM-driven deep competitor profiling (v0.20.2).

Why this exists:
  v0.19.0 LLM-as-search produces ONE observation per competitor (the
  differentiator + URL). The dynamic-confidence formula in
  /api/knowledge/competitors then buckets that into "30%" — which the user
  reads as "incomplete." Old `competitor_profile` work items would deepen
  via web search (Tavily/Exa), but those are quota-exhausted in prod.

What this does:
  Given (competitor_name, project_name, project_description), generate a
  set of probing, project-specific questions designed to extract the
  most strategically useful and non-obvious facts. Then run each question
  through the LLM and return structured `Fact` rows tagged for observation
  category. Caller persists each Fact as a KnowledgeObservation, which
  pushes the competitor into the 5+ findings band → 100%.

Cost: 1 prompt-gen call + N fact-extract calls (default N=10). All Groq
free-tier; falls back to Claude only if Groq is dry. Per-competitor cost
in cents on the paid path.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# Map prompt categories → KnowledgeObservation.observation_type
# Falls back to "general" if unknown.
_CATEGORY_TO_OBS_TYPE: dict[str, str] = {
    "recent_moves": "news",
    "pricing": "pricing_update",
    "feature": "feature_change",
    "metric": "metric",
    "regulatory": "regulatory",
    "leadership": "news",
    "technical_moat": "general",
    "weakness": "general",
    "ma_activity": "news",
    "growth_signal": "metric",
    "controversy": "news",
}

# Map prompt categories → lens tags (matches the existing 8-lens model so
# matrix/heatmap views in the UI light up correctly).
_CATEGORY_TO_LENS: dict[str, list[str]] = {
    "recent_moves": ["growth"],
    "pricing": ["business_model"],
    "feature": ["product_craft"],
    "metric": ["growth"],
    "regulatory": ["risk"],
    "leadership": ["org_culture"],
    "technical_moat": ["technology"],
    "weakness": ["risk"],
    "ma_activity": ["business_model"],
    "growth_signal": ["growth"],
    "controversy": ["risk"],
}


@dataclass
class ProbingPrompt:
    category: str  # one of _CATEGORY_TO_OBS_TYPE keys, or free-form
    question: str
    rationale: str = ""  # why this question matters for THIS project


@dataclass
class Fact:
    category: str
    question: str
    answer: str
    confidence: Literal["high", "medium", "low"] = "medium"
    date_qualifier: str = ""  # e.g. "Q4 2025", "as of Jan 2026"
    source_hint: str = ""  # URL or "training_data"
    observation_type: str = "general"
    lens_tags: list[str] = field(default_factory=list)


@dataclass
class DeepProfile:
    competitor: str
    facts: list[Fact] = field(default_factory=list)
    rejected_low_confidence: int = 0
    raw_prompt_response: str = ""


_PROMPT_GENERATION_TEMPLATE = """You are a senior competitive-intelligence analyst preparing a brief on \
**{competitor}** for the team behind **{project_name}** ({project_description}).

Generate {n} probing questions that, when answered with high fidelity, would surface \
the most strategically useful and non-obvious facts about {competitor} — facts a PM \
at {project_name} would actually act on.

Hard rules:
- Each question must target a SPECIFIC fact-category, not vague strategy ("what is their plan?"). \
  Examples of GOOD: "What was {competitor}'s most recent documented price increase, and on which SKU?", \
  "Which engineering hire in the last 12 months signals a new product bet?", \
  "What regulatory action against {competitor} is currently active?"
- Mix categories — pick from: recent_moves, pricing, feature, metric, regulatory, \
  leadership, technical_moat, weakness, ma_activity, growth_signal, controversy.
- Each question must be answerable with a concrete fact, date, name, or number — \
  not a generic narrative.
- Tailor to the relationship between {competitor} and {project_name}. Don't ask generic \
  industry questions.
- No softball questions ("strengths and weaknesses?"). Provoke specifics.

Return ONLY this JSON:
{{
  "questions": [
    {{"category": "<category>", "question": "<concrete question>", "rationale": "<one line why this matters for {project_name}>"}}
  ]
}}"""


_FACT_EXTRACTION_TEMPLATE = """You are an expert market analyst with deep up-to-date knowledge of \
**{competitor}**. The team behind **{project_name}** ({project_description}) needs a sharp, \
factual answer to this question:

QUESTION: {question}

Answer with a SPECIFIC, FACTUAL claim. Rules:
- Cite the strongest fact you actually know. Date or time-qualifier if relevant.
- If you genuinely don't have a strong fact, say so — set confidence="low" and the answer \
  may be a partial signal or "no high-confidence fact available."
- NEVER fabricate a number, date, name, or URL. If unsure, omit it.
- Keep the answer to 1-3 sentences. No padding, no hedging adverbs.

Return ONLY this JSON:
{{
  "answer": "<1-3 sentence factual claim>",
  "confidence": "high" | "medium" | "low",
  "date_qualifier": "<e.g. 'Q4 2025', 'as of late 2025', or '' if not time-bound>",
  "source_hint": "<URL if you remember one, or 'training_data' for general knowledge, or '' if low confidence>"
}}"""


def _parse_json_response(text: str) -> dict | list:
    """Strip markdown fences, locate first JSON object/array, parse."""
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if m:
            text = m.group(1).strip()
    # Locate first { or [
    for opener, closer in (("{", "}"), ("[", "]")):
        i = text.find(opener)
        if i >= 0:
            j = text.rfind(closer)
            if j > i:
                text = text[i:j + 1]
                break
    return json.loads(text)


def generate_probing_prompts(
    competitor: str,
    project_name: str,
    project_description: str,
    n: int = 10,
) -> list[ProbingPrompt]:
    """Ask the LLM to write project-specific probing questions about a competitor.

    Falls back from Groq → Claude. Returns a list of ProbingPrompt; empty list on failure.
    """
    prompt = _PROMPT_GENERATION_TEMPLATE.format(
        competitor=competitor,
        project_name=project_name,
        project_description=project_description or "(no description)",
        n=n,
    )

    text = _call_llm(prompt)
    if not text:
        return []

    try:
        parsed = _parse_json_response(text)
    except Exception as exc:
        logger.warning("[deep_profile] prompt-gen JSON parse failed: %s | text=%r", exc, text[:200])
        return []

    questions = parsed.get("questions") if isinstance(parsed, dict) else parsed
    if not isinstance(questions, list):
        return []

    out: list[ProbingPrompt] = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        question = (q.get("question") or "").strip()
        if not question:
            continue
        out.append(ProbingPrompt(
            category=(q.get("category") or "general").strip(),
            question=question,
            rationale=(q.get("rationale") or "").strip(),
        ))
    return out[:n]


def extract_fact(
    competitor: str,
    project_name: str,
    project_description: str,
    prompt: ProbingPrompt,
) -> Fact | None:
    """Run a single probing prompt and return a parsed Fact (or None)."""
    text = _call_llm(_FACT_EXTRACTION_TEMPLATE.format(
        competitor=competitor,
        project_name=project_name,
        project_description=project_description or "(no description)",
        question=prompt.question,
    ))
    if not text:
        return None

    try:
        parsed = _parse_json_response(text)
    except Exception as exc:
        logger.warning("[deep_profile] fact JSON parse failed: %s | text=%r", exc, text[:200])
        return None
    if not isinstance(parsed, dict):
        return None

    answer = (parsed.get("answer") or "").strip()
    if not answer:
        return None

    confidence = parsed.get("confidence")
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    return Fact(
        category=prompt.category,
        question=prompt.question,
        answer=answer,
        confidence=confidence,
        date_qualifier=(parsed.get("date_qualifier") or "").strip(),
        source_hint=(parsed.get("source_hint") or "").strip(),
        observation_type=_CATEGORY_TO_OBS_TYPE.get(prompt.category, "general"),
        lens_tags=_CATEGORY_TO_LENS.get(prompt.category, []),
    )


def deep_profile_competitor(
    competitor: str,
    project_name: str,
    project_description: str,
    n_questions: int = 10,
    drop_low_confidence: bool = True,
    parallel: bool = True,
) -> DeepProfile:
    """End-to-end: generate probing prompts, extract facts in parallel, return profile.

    Drops low-confidence facts by default — they pollute observations without
    adding signal. Set drop_low_confidence=False to keep them with a label.
    """
    prompts = generate_probing_prompts(competitor, project_name, project_description, n=n_questions)
    if not prompts:
        logger.warning("[deep_profile] no probing prompts generated for %s", competitor)
        return DeepProfile(competitor=competitor)

    facts: list[Fact] = []
    rejected = 0

    def _run(p: ProbingPrompt) -> Fact | None:
        return extract_fact(competitor, project_name, project_description, p)

    if parallel:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(_run, prompts))
    else:
        results = [_run(p) for p in prompts]

    for f in results:
        if f is None:
            continue
        if drop_low_confidence and f.confidence == "low":
            rejected += 1
            continue
        facts.append(f)

    return DeepProfile(competitor=competitor, facts=facts, rejected_low_confidence=rejected)


# ---------------------------------------------------------------------------
# LLM dispatch — Groq primary, Claude fallback. Mirrors agent.llm_search.
# ---------------------------------------------------------------------------


def _call_llm(prompt: str) -> str:
    """Try Groq first; fall back to Claude on any failure. Return text or ''."""
    try:
        from utils import groq_client
        if groq_client.is_available():
            return groq_client.synthesize(prompt, max_tokens=2048)
    except Exception as exc:
        logger.warning("[deep_profile] Groq call failed: %s — falling back to Claude", exc)

    try:
        from utils import claude_client
        return claude_client.ask(prompt, max_tokens=2048)
    except Exception as exc:
        logger.error("[deep_profile] Claude fallback also failed: %s", exc)
        return ""
