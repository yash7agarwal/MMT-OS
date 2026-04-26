"""Deterministic KG snapshot for the executive report generator (v0.17.0).

Purpose:
The report generator needs ONE canonical view of "what does Prism know
about project X right now" — and it needs it to be HASHABLE so we can
cache the LLM-synthesized narrative across re-downloads. This module
pulls everything in a fixed order, dedups, sorts, and returns a plain
dict that can be JSON-serialized + sha256'd.

Why call route handlers directly:
The same aggregation logic that the Lenses / Trends / Impacts tabs
already use is what the report needs. Duplicating it here would cause
the bug class v0.15.0 fixed (two paths computing the same metric
differently). Instead we import the handler functions and call them
in-process — they're plain Session-taking Python functions, no HTTP
overhead.

Output shape — see ReportSnapshot dataclass below. Stable field order;
a stable JSON serialization is the input to content_hash.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from webapp.api.models import (
    AgentSession,
    KnowledgeArtifact,
    KnowledgeEntity,
    KnowledgeObservation,
    KnowledgeRelation,
    Project,
)

logger = logging.getLogger(__name__)


@dataclass
class ReportSnapshot:
    """Frozen view of project state at one moment.

    Field order is deliberate — content_hash() depends on it being stable.
    """
    project_id: int
    project_name: str
    project_description: str
    app_package: str | None
    portfolio_summary: str | None  # from website_grounding (v0.16.2)

    # Counts (for cover/exec-summary at-a-glance)
    stats: dict[str, int] = field(default_factory=dict)

    # Aggregations (each item is a dict — same shape as the corresponding API endpoint)
    competitors: list[dict] = field(default_factory=list)
    trends: list[dict] = field(default_factory=list)
    regulations: list[dict] = field(default_factory=list)
    technologies: list[dict] = field(default_factory=list)
    effects: list[dict] = field(default_factory=list)

    # Lens matrix — {lens: [{competitor_name, count}]}
    lens_matrix: dict = field(default_factory=dict)
    # Per-lens drilldown — {lens: [{entity_name, observations: [...]}]}
    lens_detail: dict[str, list] = field(default_factory=dict)

    # Impact cascade graph — {nodes: [...], edges: [...]}
    impact_graph: dict = field(default_factory=dict)

    # Existing PRD docs to cross-link in appendix
    prd_artifacts: list[dict] = field(default_factory=list)

    # Source URL deduped index — every URL Prism cited, with citation count
    source_index: list[dict] = field(default_factory=list)

    # Loupe enrichment (filled by report_generator if include_loupe=True
    # and Loupe is reachable; empty list otherwise)
    loupe_runs: list[dict] = field(default_factory=list)

    # Methodology — agent run history
    agent_sessions: list[dict] = field(default_factory=list)

    generated_at: str = ""

    def content_hash(self) -> str:
        """sha256 over the entire snapshot, EXCLUDING generated_at + loupe_runs.

        loupe_runs is excluded because Loupe reachability shouldn't invalidate
        cached narrative — if Loupe is back online next time, we just enrich
        the binary render without re-paying for the LLM synthesis.
        """
        payload = asdict(self)
        payload.pop("generated_at", None)
        payload.pop("loupe_runs", None)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_snapshot(db: Session, project_id: int) -> ReportSnapshot:
    """Pull the full project state into a deterministic snapshot.

    Reuses route-handler logic where possible to stay aligned with the UI's
    metric definitions. See module docstring for why.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    # Portfolio summary from website grounding — same call site as
    # research_brief.build_brief uses, so identical text every run when
    # the homepage hasn't changed.
    from agent.website_grounding import fetch_portfolio_summary
    portfolio_summary = fetch_portfolio_summary(project.app_package, project.name)

    # Lens matrix — call the route handler directly (it's a plain function).
    from webapp.api.routes import knowledge as kn_routes
    lens_matrix_payload = kn_routes.get_lens_matrix(project_id=project_id, db=db)
    # Per-lens detail — call the same handler the Lens drilldown uses.
    lens_detail: dict[str, list] = {}
    for lens in lens_matrix_payload.get("lenses", []):
        try:
            ld = kn_routes.get_lens_detail(lens_name=lens, project_id=project_id, db=db)
            lens_detail[lens] = ld.get("entities", [])
        except Exception as exc:
            logger.warning(f"[report_snapshot] lens detail for {lens} failed: {exc}")
            lens_detail[lens] = []

    trends_payload = kn_routes.get_trends_view(project_id=project_id, db=db)
    impact_graph = kn_routes.get_impact_graph(project_id=project_id, db=db)

    # Entities by type (single query — cheaper than separate calls per type).
    all_entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            (KnowledgeEntity.user_signal.is_(None))
            | (KnowledgeEntity.user_signal != "dismissed"),
        )
        .order_by(KnowledgeEntity.entity_type, KnowledgeEntity.name)
        .all()
    )

    def _entity_dict(e: KnowledgeEntity) -> dict:
        return {
            "id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "description": e.description or "",
            "confidence": e.confidence,
            "metadata": e.metadata_json or {},
            "last_updated_at": e.last_updated_at.isoformat() if e.last_updated_at else None,
        }

    competitors = [_entity_dict(e) for e in all_entities if e.entity_type == "company"]
    regulations = [_entity_dict(e) for e in all_entities if e.entity_type == "regulation"]
    technologies = [_entity_dict(e) for e in all_entities if e.entity_type == "technology"]
    effects = [_entity_dict(e) for e in all_entities if e.entity_type == "effect"]

    # Source index — every observation's URL deduped + citation count.
    source_rows = (
        db.query(KnowledgeObservation)
        .join(KnowledgeEntity, KnowledgeObservation.entity_id == KnowledgeEntity.id)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeObservation.source_url.isnot(None),
            KnowledgeObservation.source_url != "",
        )
        .all()
    )
    source_counts: dict[str, int] = {}
    for o in source_rows:
        url = o.source_url or ""
        if url:
            source_counts[url] = source_counts.get(url, 0) + 1
    source_index = [
        {"url": url, "citations": cnt, "host": _host(url)}
        for url, cnt in sorted(source_counts.items(), key=lambda x: (-x[1], x[0]))
    ]

    # Existing PRD artifacts — cross-link in appendix.
    prd_rows = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == "prd_doc",
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .all()
    )
    prd_artifacts = [
        {
            "id": a.id,
            "title": a.title,
            "generated_at": a.generated_at.isoformat() if a.generated_at else None,
        }
        for a in prd_rows
    ]

    # Methodology — agent run summaries (last 30, completed only, for "how this was gathered").
    sessions = (
        db.query(AgentSession)
        .filter(
            AgentSession.project_id == project_id,
            AgentSession.completed_at.isnot(None),
        )
        .order_by(AgentSession.completed_at.desc())
        .limit(30)
        .all()
    )
    agent_sessions = [
        {
            "id": s.id,
            "agent_type": s.agent_type,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "items_completed": s.items_completed or 0,
            "items_failed": s.items_failed or 0,
            "knowledge_added": s.knowledge_added or 0,
        }
        for s in sessions
    ]

    # Stats — use the same shape as /api/projects/{id}.stats so it's
    # directly comparable to what the user already sees in the UI.
    obs_count = (
        db.query(KnowledgeObservation)
        .join(KnowledgeEntity, KnowledgeObservation.entity_id == KnowledgeEntity.id)
        .filter(KnowledgeEntity.project_id == project_id)
        .count()
    )
    relation_count = (
        db.query(KnowledgeRelation)
        .join(KnowledgeEntity, KnowledgeRelation.from_entity_id == KnowledgeEntity.id)
        .filter(KnowledgeEntity.project_id == project_id)
        .count()
    )
    stats = {
        "competitor_count": len(competitors),
        "trend_count": len(trends_payload) if isinstance(trends_payload, list) else len(trends_payload.get("trends", [])),
        "regulation_count": len(regulations),
        "technology_count": len(technologies),
        "effect_count": len(effects),
        "observation_count": obs_count,
        "relation_count": relation_count,
        "source_count": len(source_index),
        "session_count": len(agent_sessions),
    }

    return ReportSnapshot(
        project_id=project_id,
        project_name=project.name,
        project_description=project.description or "",
        app_package=project.app_package,
        portfolio_summary=portfolio_summary,
        stats=stats,
        competitors=competitors,
        trends=trends_payload if isinstance(trends_payload, list) else trends_payload.get("trends", []),
        regulations=regulations,
        technologies=technologies,
        effects=effects,
        lens_matrix=lens_matrix_payload,
        lens_detail=lens_detail,
        impact_graph=impact_graph,
        prd_artifacts=prd_artifacts,
        source_index=source_index,
        agent_sessions=agent_sessions,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


def _host(url: str) -> str:
    """Best-effort host extraction without importing urllib for one regex match."""
    try:
        from urllib.parse import urlparse
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
