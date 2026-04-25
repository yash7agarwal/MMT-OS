"""Efficient research module — deterministic search + single LLM synthesis.

Instead of 10-15 LLM calls per work item (tool-use loop), this module:
1. Runs web searches from a planner-generated plan (not hardcoded seeds)
2. Fetches and extracts content from top results (no LLM needed)
3. Makes ONE LLM call to synthesize findings from the raw data
4. Returns both synthesized candidates and the retrieval bundle so callers
   can run the source_url validator before writing to the KG.

Synthesis provider priority (Phase 1 of the research-architecture rework):
  Claude (Sonnet) — default, highest fidelity for synthesis
  Groq (Llama)   — only if PRISM_SYNTH_CHEAP=1 in env (cheap-mode opt-in)
  Gemini          — fallback when Claude is unavailable

Rationale: synthesis is the stage where hallucination matters most; we no
longer default to free-tier Llama here.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from tools.web_research import WebResearcher

logger = logging.getLogger(__name__)

_web = WebResearcher()


def _get_synthesizer():
    """Return the synthesis function per priority: Claude > Groq (if cheap mode) > Gemini.

    Set PRISM_SYNTH_CHEAP=1 to prefer Groq first (budget-conscious runs).
    """
    cheap_mode = os.environ.get("PRISM_SYNTH_CHEAP", "").strip() in ("1", "true", "yes")

    if cheap_mode:
        try:
            from utils.groq_client import synthesize, is_available
            if is_available():
                logger.info("[researcher] PRISM_SYNTH_CHEAP=1 — using Groq for synthesis")
                return synthesize
        except ImportError:
            pass

    try:
        from utils.claude_client import ask
        logger.info("[researcher] Using Claude for synthesis (default)")
        return ask
    except Exception:
        pass

    from utils.gemini_client import ask
    logger.info("[researcher] Using Gemini for synthesis (Claude unavailable)")
    return ask


def research_competitor(
    competitor_name: str,
    project_name: str,
    project_description: str,
) -> dict:
    """Research a competitor using deterministic search + single synthesis.

    Returns: {
        "findings": [{"type": str, "content": str, "lenses": [str], "source_url": str}],
        "profile_md": str,  # markdown profile report
        "entities_created": int,
        "observations_added": int,
    }
    """
    synthesize = _get_synthesizer()

    # Step 1: Deterministic searches (no LLM needed)
    searches = [
        f"{competitor_name} revenue annual report 2025 2026",
        f"{competitor_name} new features launches 2025 2026",
        f"{competitor_name} pricing commission fees",
        f"{competitor_name} app rating reviews Play Store",
        f"{competitor_name} acquisitions partnerships funding 2025 2026",
        f"{competitor_name} market share users downloads",
    ]

    raw_data = []
    sources = []
    for query in searches:
        results = _web.search(query, max_results=3)
        for r in results[:2]:  # Top 2 per search
            page = _web.fetch_page(r.get("url", ""), max_length=5000)
            if page.get("content") and len(page["content"]) > 100:
                raw_data.append({
                    "query": query,
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "content": page["content"][:3000],
                })
                sources.append(r.get("url", ""))

    if not raw_data:
        return {"findings": [], "profile_md": "", "entities_created": 0, "observations_added": 0}

    # Step 2: ONE synthesis call
    raw_text = "\n\n---\n\n".join([
        f"Source: {d['url']}\nTitle: {d['title']}\n{d['content']}"
        for d in raw_data[:8]  # Cap at 8 sources to fit context
    ])

    synthesis_prompt = f"""Analyze the following raw research data about {competitor_name} (a competitor of {project_name}).

{project_name} description: {project_description}

RAW DATA:
{raw_text}

Extract SPECIFIC findings. For each finding, provide:
- "type": one of "metric", "feature_change", "pricing_update", "news", "general"
- "content": the specific finding (2-3 sentences, include numbers/dates)
- "lenses": array of 1-3 from: product_craft, growth, supply, monetization, technology, brand_trust, moat, trajectory
- "source_url": the URL this came from

Return a JSON object:
{{
    "findings": [
        {{"type": "metric", "content": "...", "lenses": ["growth", "monetization"], "source_url": "..."}},
        ...
    ],
    "summary": "One paragraph executive summary of this competitor's position"
}}

Rules:
- Extract 4-8 findings, each SPECIFIC with numbers/dates
- Every finding MUST have a source_url from the raw data
- Do NOT invent facts not present in the raw data
- Focus on: revenue/financials, recent product changes, pricing model, strategic moves, app metrics"""

    try:
        response = synthesize(
            synthesis_prompt,
            max_tokens=3000,
            system="You are a competitive intelligence analyst. Extract specific, evidence-backed findings from raw research data. Return valid JSON only.\n\nCRITICAL: Do NOT invent facts, numbers, or percentages not present in the raw data. If data is unavailable, say 'data not available'. Every claim must cite which source it came from. Fabricating data is strictly prohibited.",
        )

        # Parse response
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        findings = result.get("findings", [])
        summary = result.get("summary", "")

        return {
            "findings": findings,
            "profile_md": f"# {competitor_name} — Competitive Profile\n\n{summary}\n\n## Key Findings\n\n" +
                          "\n".join([f"- **[{f['type']}]** {f['content']}" for f in findings]),
            "entities_created": 0,
            "observations_added": len(findings),
        }

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[researcher] Synthesis failed for {competitor_name}: {e}")
        return {"findings": [], "profile_md": "", "entities_created": 0, "observations_added": 0}


def research_industry_trends(
    brief: "ResearchBrief",  # noqa: F821 — forward ref, imported lazily below
    plan: "ResearchPlan",    # noqa: F821
) -> dict:
    """Research consumer-behavior trends, niche segments, and emerging needs.

    Driven by a planner-generated ResearchPlan — no hardcoded domain seeds.
    The brief provides project identity + feedback signal; the plan provides
    the query set (discovery + deepening + validation + lateral).

    Returns:
        {
            "trends": list[dict],          # synthesized candidates; each carries a source_url
            "retrieval_bundle": list[dict], # {url, title, content} for validator input
            "inferred_industry": str,
        }
    Callers MUST pass the returned `retrieval_bundle` to
    `agent.synthesis_validator.validate_candidates` before writing to the KG.
    """
    # Lazy imports to avoid circulars at module import time.
    from agent.research_brief import ResearchBrief  # noqa: F401
    from agent.query_planner import ResearchPlan  # noqa: F401

    synthesize = _get_synthesizer()

    # Deterministic retrieval over the planner's queries — no hardcoded seeds.
    raw_data: list[dict[str, Any]] = []
    for pq in plan.queries:
        results = _web.search(pq.query, max_results=3)
        for r in results[:2]:
            page = _web.fetch_page(r.get("url", ""), max_length=4000)
            if page.get("content") and len(page["content"]) > 100:
                raw_data.append({
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "content": page["content"][:2500],
                    "query": pq.query,
                    "query_kind": pq.kind,
                })

    # Feature-flagged alt retrievers (RSS / Reddit). No-op when PRISM_RETRIEVERS
    # isn't set; additive to the web-search bundle when it is.
    try:
        from tools import rss_retriever
        for r in rss_retriever.fetch_for_plan(plan.inferred_industry):
            raw_data.append({**r, "query": r.get("feed", "rss"), "query_kind": "discovery"})
    except Exception as exc:
        logger.warning("[researcher] RSS retrieval failed: %s", exc)
    try:
        from tools import reddit_retriever
        for r in reddit_retriever.fetch_for_plan(plan.inferred_industry):
            raw_data.append({**r, "query": r.get("subreddit", "reddit"), "query_kind": "lateral"})
    except Exception as exc:
        logger.warning("[researcher] Reddit retrieval failed: %s", exc)

    if not raw_data:
        return {
            "trends": [],
            "retrieval_bundle": [],
            "inferred_industry": plan.inferred_industry,
        }

    # Cap the synthesis context to avoid exploding the LLM input — pick highest-
    # value sources (deepening/validation first, then discovery, then lateral).
    _kind_priority = {"deepening": 0, "validation": 1, "discovery": 2, "lateral": 3}
    raw_data.sort(key=lambda d: _kind_priority.get(d.get("query_kind", "discovery"), 2))
    capped = raw_data[:10]

    raw_text = "\n\n---\n\n".join([
        f"Source: {d['url']}\nTitle: {d['title']}\n{d['content']}"
        for d in capped
    ])

    known_block = ""
    if brief.recent_trends:
        known_block = (
            "\nAlready tracked (do not re-emit these names — but a NEW observation "
            "about one of them IS welcome if you have fresh evidence):\n- "
            + "\n- ".join(t.name for t in brief.recent_trends[:20])
        )
    dismissed_block = ""
    if brief.dismissed_canonicals:
        dismissed_block = (
            "\nPreviously DISMISSED by the user (avoid this pattern):\n- "
            + "\n- ".join(brief.dismissed_canonicals[:20])
        )

    portfolio_block = ""
    if getattr(brief, "portfolio_summary", None):
        portfolio_block = (
            "\nAUTHORITATIVE PORTFOLIO (extracted from the company's homepage — "
            "this is what they ACTUALLY do; if a finding contradicts this or "
            "comes from an unrelated industry, drop it):\n"
            f"{brief.portfolio_summary}\n"
        )

    synthesis_prompt = f"""You are a consumer insights researcher for {brief.project_name}.

SUBJECT (this is the company you are researching FOR — do NOT extract it as a competitor or trend):
  Name: {brief.project_name}
  Description: {brief.project_description or '(no description)'}
{portfolio_block}
INFERRED INDUSTRY: {plan.inferred_industry or 'unknown'}
{known_block}
{dismissed_block}

RAW DATA (pulled against the research plan for this subject):
{raw_text}

Your job: identify NICHE CONSUMER TRENDS **specific to {brief.project_name}'s domain**
({plan.inferred_industry}) that reveal new product opportunities.

DO NOT extract:
- {brief.project_name} itself (in any phrasing — descriptions OF {brief.project_name} are NOT trends)
- Specific people / executives / scientists by name (those are not trends)
- Specific organizations / regulators / agencies by name (e.g. "European Chemicals Agency") — those are entities, not trends. If you must mention them, do so inside the description of an actual trend, not as the trend's name.
- Commodity-market / metals-market / financial-instrument commentary that happens to share a keyword with the subject's name (e.g. if the subject is "Platinum Industries" the company, do not extract "platinum metal market deficit" — that's a different domain).
- Generic "market is growing" / size / growth claims
- Trends that belong to OTHER industries — cross-industry leakage is a bug.

DO extract:
- Specific underserved segments with unmet needs
- Behavioral shifts that create new JTBD (Jobs To Be Done)
- Emerging categories with quantified demand
- Regulatory / platform / infrastructure changes creating new needs
- Named product-strategy moves by competitors that reveal a gap

For each trend, provide:
- "name": specific, named pattern (not a vague phrase, NOT {brief.project_name}, NOT a person, NOT an organization)
- "description": 3-4 sentences — the consumer need, why it's underserved, what a product could do
- "timeline": "past" | "present" | "emerging" | "future"
- "category": "consumer_behavior" | "technology" | "regulation" | "demographics" | "market_structure"
- "quantification": {{"search_volume": "...", "market_size": "...", "growth_rate": "...", "user_demand": "..."}} — include only figures actually present in the raw data; omit keys without evidence
- "jtbd": the job the customer is trying to get done
- "product_opportunity": what {brief.project_name} could build
- "source_url": a URL from the raw data above (MANDATORY — candidates without a valid source_url will be rejected)

Return JSON: {{"trends": [...]}}

Extract 5-10 trends. Every trend must be SPECIFIC, domain-relevant, ACTIONABLE, and **about a pattern in the market — not about a single named entity or person**."""

    try:
        response = synthesize(
            synthesis_prompt,
            max_tokens=4096,
            system=(
                "You are a consumer insights researcher. Extract specific, niche, "
                "actionable consumer trends — NOT obvious industry facts. Return valid JSON only.\n\n"
                "CRITICAL: Do NOT invent facts, numbers, percentages, or URLs not present in the raw data. "
                "Every claim must cite which source URL it came from, and that URL MUST appear in the raw data above. "
                "If data is unavailable, omit the claim. Fabrication is a hard failure."
            ),
        )

        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        parsed = json.loads(text)
        return {
            "trends": parsed.get("trends", []),
            "retrieval_bundle": capped,
            "inferred_industry": plan.inferred_industry,
        }

    except (json.JSONDecodeError, Exception) as e:
        logger.error("[researcher] Trend synthesis failed: %s", e)
        return {
            "trends": [],
            "retrieval_bundle": capped,
            "inferred_industry": plan.inferred_industry,
        }


def research_impact_cascade(
    trend_name: str,
    trend_description: str,
    competitor_names: list[str],
    project_name: str,
) -> dict:
    """Analyze 2nd and 3rd order impacts using deep causal reasoning.

    3rd order thinking explained:
    - 1st order: The trend itself (e.g., "Rise of AI in travel")
    - 2nd order: Direct consequences (e.g., "OTAs build AI trip planners")
    - 3rd order: Non-obvious downstream effects (e.g., "AI planners recommend
      off-beat destinations → boutique hotels in Tier-3 cities see a significant
      booking increase → MMT's hotel supply team needs to rapidly onboard new
      properties in small towns to capture this demand")

    The 3rd order is where the strategic insight lives — it's what competitors
    DON'T see coming.
    """
    synthesize = _get_synthesizer()

    # Search for real-world examples of this trend's effects
    search_results = _web.search(f'"{trend_name}" impact consequences effects 2025 2026', max_results=3)
    context = ""
    for r in search_results[:2]:
        page = _web.fetch_page(r.get("url", ""), max_length=2000)
        if page.get("content") and len(page["content"]) > 100:
            context += f"\nSource: {r.get('url','')}\n{page['content'][:1500]}\n---\n"

    prompt = f"""You are a strategic analyst who thinks in 3rd order effects.

TREND: {trend_name}
DESCRIPTION: {trend_description}
COMPANIES: {', '.join(competitor_names)}
OUR COMPANY: {project_name}

{f"REAL-WORLD CONTEXT:{context}" if context else ""}

3RD ORDER THINKING EXPLAINED:
Most analysts stop at 2nd order: "AI → companies build AI features." That's obvious.
3rd order asks: "And THEN what happens?"

Example of good 3rd order chain:
- Trend: "Rise of AI trip planners"
- 2nd order: "OTAs integrate AI for personalized recommendations"
- 3rd order: "AI planners recommend off-beat destinations based on user preferences →
  demand shifts from top-10 tourist cities to Tier-3 towns → hotels in small towns
  see a significant booking increase → {project_name}'s hotel supply team needs to
  urgently onboard new properties in small towns OR lose this demand to Airbnb which
  already has home-stay inventory there"

That 3rd order effect is ACTIONABLE — it tells the PM exactly what to build next.

BAD (generic, obvious):
- "AI will increase competition" — so what?
- "Companies will invest in technology" — meaningless
- "Users will benefit from better experience" — not actionable

GOOD (specific, non-obvious, actionable):
- "AI concierge substantially reduces post-booking support calls → {project_name}'s
  large call center becomes a cost liability → restructure to AI-first support yields
  major savings but requires a long migration timeline"

For each effect chain, think: "What does this mean the PM should BUILD or STOP BUILDING?"

Return JSON:
{{
    "effects": [
        {{
            "name": "specific non-obvious effect",
            "description": "3-4 sentences explaining the causal chain and WHY this matters",
            "severity": "high|medium|low",
            "timeframe": "near (<6mo)|medium (6-18mo)|long (>18mo)",
            "what_to_build": "specific product action this implies"
        }}
    ],
    "impacts": [
        {{
            "effect": "effect name from above",
            "company": "specific company name",
            "description": "How THIS specific company is affected, with numbers if possible. What do they lose or gain?",
            "is_threat": true/false,
            "severity": "high|medium|low",
            "strategic_implication": "What should {project_name} do about this?"
        }}
    ]
}}

Extract 3-5 effects with 2-3 company impacts each. Every effect must pass the "so what?" test — if a PM reads it and shrugs, it's not specific enough."""

    try:
        response = synthesize(
            prompt,
            max_tokens=3000,
            system="You are a strategic analyst specializing in 3rd order effects. Think non-obviously. Every insight must be specific enough that a PM can act on it. Return valid JSON only.\n\nCRITICAL: Do NOT invent facts, numbers, or percentages not present in the raw data. If data is unavailable, say 'data not available'. Every claim must cite which source it came from. Fabricating data is strictly prohibited.",
        )

        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return json.loads(text)

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[researcher] Impact cascade failed: {e}")
        return {"effects": [], "impacts": []}
