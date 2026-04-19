"""Research query planner — domain-native seeds generated from a ResearchBrief.

Replaces the hardcoded travel-domain seeds in efficient_researcher.py. One
Haiku call per (project, brief_hash) per TTL window produces a structured
research plan: discovery + deepening + validation + lateral queries.

The brief renders to a stable system prompt that is explicitly cached, so a
re-run on the same brief hits the prompt cache (~90% discount on input tokens).

Plans are persisted as `KnowledgeArtifact(artifact_type='research_plan')` and
keyed on `(project_id, brief_hash)`. A repeat run within `ttl_hours` reuses the
cached plan; any brief change (new competitor, user signal, etc.) yields a new
hash and regenerates.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from agent.research_brief import ResearchBrief
from utils import claude_client
from webapp.api.models import KnowledgeArtifact

load_dotenv()

logger = logging.getLogger(__name__)

PLANNER_MODEL = "claude-haiku-4-5-20251001"
PLAN_ARTIFACT_TYPE = "research_plan"
DEFAULT_TTL_HOURS = 24


PLANNER_SYSTEM_PROMPT = """You are the research planner for Prism, a product-intelligence platform.

You receive a brief describing a single product/company (the "subject") and
produce a structured research plan for discovering real, deep, recent insights
about that subject's domain. The plan drives web search queries in the next stage.

Quality bar — non-negotiable:
- Every query must be **specific to the subject's domain**. Generic "X trends 2026"
  queries return SEO sludge. Prefer niche, named patterns: specific user segments,
  behavior shifts, product categories, business model pivots, regulatory changes,
  quantified phenomena, named competitor moves, platform/infra shifts.
- Use the subject's own description and known competitors as anchors. Mention
  them by name in queries when it sharpens the signal.
- Avoid any query that would return mostly travel-industry content UNLESS the
  subject IS a travel company. This is a known failure mode.
- Deepening queries must reference a specific prior trend/entity by name.
- Validation queries must target low-confidence entities and ask questions that
  would produce contradicting or confirming evidence.
- Lateral queries bridge to adjacent domains that could influence the subject
  (e.g. for a food-delivery app: quick commerce, dark kitchens, labor regulation).

Return a single tool-use call with the structured plan. Do not emit prose."""


PLAN_TOOL_SCHEMA = {
    "name": "submit_research_plan",
    "description": "Submit the structured research plan.",
    "input_schema": {
        "type": "object",
        "required": ["inferred_industry", "queries"],
        "properties": {
            "inferred_industry": {
                "type": "string",
                "description": "Short tag for the subject's industry (e.g. 'food delivery', 'online travel', 'fintech payments', 'hospitality'). Derived from the brief description — used to guide retrieval filters.",
            },
            "queries": {
                "type": "array",
                "minItems": 8,
                "maxItems": 18,
                "items": {
                    "type": "object",
                    "required": ["kind", "query", "rationale"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["discovery", "deepening", "validation", "lateral"],
                        },
                        "query": {
                            "type": "string",
                            "description": "The search query string — written as a human would search, 3-12 words.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One sentence: why this query, what signal it's after.",
                        },
                        "target_canonical": {
                            "type": "string",
                            "description": "For deepening/validation: the canonical name of the prior entity this probes. Empty for discovery/lateral.",
                        },
                    },
                },
            },
        },
    },
}


@dataclass
class PlannedQuery:
    kind: str
    query: str
    rationale: str
    target_canonical: str | None = None


@dataclass
class ResearchPlan:
    brief_hash: str
    project_id: int
    inferred_industry: str
    generated_at: str
    ttl_expires_at: str
    queries: list[PlannedQuery] = field(default_factory=list)
    artifact_id: int | None = None
    cached: bool = False

    def queries_by_kind(self, kind: str) -> list[PlannedQuery]:
        return [q for q in self.queries if q.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_hash": self.brief_hash,
            "project_id": self.project_id,
            "inferred_industry": self.inferred_industry,
            "generated_at": self.generated_at,
            "ttl_expires_at": self.ttl_expires_at,
            "queries": [
                {
                    "kind": q.kind,
                    "query": q.query,
                    "rationale": q.rationale,
                    "target_canonical": q.target_canonical,
                }
                for q in self.queries
            ],
        }


def _find_cached_plan(
    db: Session, project_id: int, brief_hash: str, ttl_hours: int,
) -> ResearchPlan | None:
    cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
    artifact = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == PLAN_ARTIFACT_TYPE,
            KnowledgeArtifact.title == brief_hash,
            KnowledgeArtifact.generated_at >= cutoff,
            KnowledgeArtifact.is_stale.is_(False),
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .first()
    )
    if artifact is None:
        return None
    try:
        payload = json.loads(artifact.content_md)
    except json.JSONDecodeError:
        logger.warning("Cached plan %s unparseable; regenerating", artifact.id)
        return None
    plan = ResearchPlan(
        brief_hash=payload["brief_hash"],
        project_id=payload["project_id"],
        inferred_industry=payload.get("inferred_industry", ""),
        generated_at=payload["generated_at"],
        ttl_expires_at=payload["ttl_expires_at"],
        queries=[PlannedQuery(**q) for q in payload.get("queries", [])],
        artifact_id=artifact.id,
        cached=True,
    )
    return plan


def _call_planner(brief: ResearchBrief) -> tuple[str, list[PlannedQuery]]:
    """Route through claude_client.ask_with_tools so Claude→Gemini fallback is automatic.

    NOTE: prompt caching is deferred — ask_with_tools doesn't expose cache_control
    markers today. Re-enable once a cacheable variant exists, or when raw SDK
    access + fallback are both needed.
    """
    system_prompt = PLANNER_SYSTEM_PROMPT + "\n\n" + brief.to_prompt_context()

    user_prompt = (
        "Produce the structured research plan now. "
        "Mix: 5–8 discovery, 3–5 deepening (targeting recent tracked trends by name), "
        "2–3 validation (targeting low-confidence entities), 1–2 lateral (adjacent domains). "
        "Submit via the submit_research_plan tool. "
        "Remember: generic industry-trend queries are forbidden — every query must be "
        "specific to this subject's domain."
    )

    resp = claude_client.ask_with_tools(
        messages=[{"role": "user", "content": user_prompt}],
        tools=[PLAN_TOOL_SCHEMA],
        system=system_prompt,
        model=PLANNER_MODEL,
        max_tokens=2048,
    )

    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == "submit_research_plan":
            payload = block.input
            queries = [
                PlannedQuery(
                    kind=q["kind"],
                    query=q["query"],
                    rationale=q.get("rationale", ""),
                    target_canonical=(q.get("target_canonical") or None) or None,
                )
                for q in payload.get("queries", [])
            ]
            return payload.get("inferred_industry", ""), queries
    raise RuntimeError("Planner did not emit submit_research_plan tool call")


def get_or_generate_plan(
    db: Session,
    brief: ResearchBrief,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    force_regenerate: bool = False,
) -> ResearchPlan:
    """Return a research plan for this brief — from cache if fresh, else regenerated.

    The plan is persisted as a KnowledgeArtifact with title=brief_hash so the
    next identical run hits the cache. Cache invalidates automatically when the
    brief's content changes (hash changes) or TTL expires.
    """
    brief_hash = brief.content_hash()

    if not force_regenerate:
        cached = _find_cached_plan(db, brief.project_id, brief_hash, ttl_hours)
        if cached is not None:
            logger.info(
                "[planner] Cache hit for project=%d brief_hash=%s (%d queries)",
                brief.project_id, brief_hash, len(cached.queries),
            )
            return cached

    logger.info(
        "[planner] Generating plan for project=%d brief_hash=%s (stats=%s)",
        brief.project_id, brief_hash, brief.stats,
    )

    inferred_industry, queries = _call_planner(brief)

    now = datetime.utcnow()
    plan = ResearchPlan(
        brief_hash=brief_hash,
        project_id=brief.project_id,
        inferred_industry=inferred_industry,
        generated_at=now.isoformat() + "Z",
        ttl_expires_at=(now + timedelta(hours=ttl_hours)).isoformat() + "Z",
        queries=queries,
        cached=False,
    )

    # Persist as KnowledgeArtifact — title holds the brief_hash for fast lookup.
    artifact = KnowledgeArtifact(
        project_id=brief.project_id,
        artifact_type=PLAN_ARTIFACT_TYPE,
        title=brief_hash,
        content_md=json.dumps(plan.to_dict(), separators=(",", ":")),
        generated_by_agent="query_planner",
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    plan.artifact_id = artifact.id

    logger.info(
        "[planner] Persisted plan artifact=%d industry=%r queries=%d",
        artifact.id, inferred_industry, len(queries),
    )
    return plan
