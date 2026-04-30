"""Knowledge graph API routes — read-only access to Product OS intelligence."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import String, cast, func, or_, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from webapp.api.db import get_db
from webapp.api.models import (
    KnowledgeArtifact,
    KnowledgeEntity,
    KnowledgeObservation,
    KnowledgeRelation,
    KnowledgeScreenshot,
    WorkItem,
    AgentSession,
)
from webapp.api.schemas import (
    KnowledgeEntityOut,
    KnowledgeEntityDetail,
    KnowledgeObservationOut,
    KnowledgeRelationOut,
    KnowledgeArtifactOut,
    KnowledgeScreenshotOut,
    WorkItemOut,
    AgentSessionOut,
    KnowledgeSummary,
    EntitySignalIn,
    ProjectProgressOut,
)

VALID_SIGNALS = {"kept", "dismissed", "starred", "clear"}

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# ---- Entities ----


@router.get("/entities", response_model=list[KnowledgeEntityOut])
def list_entities(
    project_id: int = Query(...),
    entity_type: str | None = Query(None),
    name: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(KnowledgeEntity).filter(KnowledgeEntity.project_id == project_id)
    if entity_type:
        q = q.filter(KnowledgeEntity.entity_type == entity_type)
    if name:
        q = q.filter(KnowledgeEntity.name.ilike(f"%{name}%"))
    entities = q.order_by(KnowledgeEntity.last_updated_at.desc()).limit(limit).all()

    # Compute dynamic confidence from observation count
    entity_ids = [e.id for e in entities]
    if entity_ids:
        obs_counts = dict(
            db.query(KnowledgeObservation.entity_id, func.count(KnowledgeObservation.id))
            .filter(KnowledgeObservation.entity_id.in_(entity_ids))
            .group_by(KnowledgeObservation.entity_id)
            .all()
        )
        # v0.20.2: uncap to 1.0 so a fully-profiled competitor can show 100%,
        # not 90%. Also stash the raw count + band on metadata_json so the UI
        # can show "X findings · needs N more for next band".
        for e in entities:
            count = obs_counts.get(e.id, 0)
            if count == 0:
                e.confidence = 0.1
            elif count <= 2:
                e.confidence = 0.3
            elif count <= 4:
                e.confidence = 0.6
            elif count <= 7:
                e.confidence = 0.9
            else:
                e.confidence = 1.0
            # Decorate metadata_json (response-only — these are session-level
            # SQLAlchemy attributes that won't auto-commit; safe transient mutation).
            md = dict(e.metadata_json or {})
            md["_finding_count"] = count
            md["_depth_band"] = (
                "empty" if count == 0
                else "shallow" if count <= 2
                else "medium" if count <= 4
                else "deep" if count <= 7
                else "comprehensive"
            )
            e.metadata_json = md

    return entities


@router.get("/entities/{entity_id}", response_model=KnowledgeEntityDetail)
def get_entity(entity_id: int, db: Session = Depends(get_db)):
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    observations = (
        db.query(KnowledgeObservation)
        .filter(KnowledgeObservation.entity_id == entity_id)
        .order_by(KnowledgeObservation.observed_at.desc())
        .limit(20)
        .all()
    )

    relations = (
        db.query(KnowledgeRelation)
        .filter(
            or_(
                KnowledgeRelation.from_entity_id == entity_id,
                KnowledgeRelation.to_entity_id == entity_id,
            )
        )
        .all()
    )

    return KnowledgeEntityDetail(
        id=entity.id,
        project_id=entity.project_id,
        entity_type=entity.entity_type,
        name=entity.name,
        canonical_name=entity.canonical_name,
        description=entity.description,
        metadata_json=entity.metadata_json,
        source_agent=entity.source_agent,
        confidence=entity.confidence,
        first_seen_at=entity.first_seen_at,
        last_updated_at=entity.last_updated_at,
        user_signal=entity.user_signal,
        dismissed_reason=entity.dismissed_reason,
        observations=[KnowledgeObservationOut.model_validate(o) for o in observations],
        relations=[KnowledgeRelationOut.model_validate(r) for r in relations],
    )


@router.post("/entities/{entity_id}/signal", response_model=KnowledgeEntityOut)
def set_entity_signal(
    entity_id: int,
    body: EntitySignalIn,
    db: Session = Depends(get_db),
):
    """Set or clear the user-feedback signal on a knowledge entity.

    Signals feed the compounding loop: dismissed canonicals become negative
    examples in the next research brief; starred canonicals get weighted up.
    Pass signal='clear' to remove a prior signal.
    """
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if body.signal not in VALID_SIGNALS:
        raise HTTPException(
            status_code=400,
            detail=f"signal must be one of {sorted(VALID_SIGNALS)}",
        )
    if body.signal == "clear":
        entity.user_signal = None
        entity.dismissed_reason = None
    else:
        entity.user_signal = body.signal
        entity.dismissed_reason = body.reason if body.signal == "dismissed" else None
    db.commit()
    db.refresh(entity)
    return entity


@router.post("/entities/{entity_id}/purge")
def purge_entity(
    entity_id: int,
    body: EntitySignalIn,
    db: Session = Depends(get_db),
):
    """F3: purge a mis-tagged entity and enqueue a fresh research run.

    Non-destructive on the entity itself (marks user_signal='dismissed' so the
    canonical name blocks re-learning in the next research brief). Destructive
    on the entity's observations and relations — they were the bad data we
    want gone from trends-view, lens pages, and the impact graph.

    Side effect: schedules a high-priority `niche_trend_discovery` work item
    for this project's industry_research agent to refill the trend shelf.
    """
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    reason = (body.reason or "[purged]").strip()[:500]
    project_id = entity.project_id

    # Cascade-delete observations and relations attached to this entity.
    obs_deleted = (
        db.query(KnowledgeObservation)
        .filter(KnowledgeObservation.entity_id == entity_id)
        .delete(synchronize_session=False)
    )
    rel_deleted = (
        db.query(KnowledgeRelation)
        .filter(
            (KnowledgeRelation.from_entity_id == entity_id)
            | (KnowledgeRelation.to_entity_id == entity_id)
        )
        .delete(synchronize_session=False)
    )

    # Keep the entity row as a dismissed tombstone — the canonical_name still
    # blocks re-learning via the research brief's dismissed_canonicals list.
    entity.user_signal = "dismissed"
    entity.dismissed_reason = reason

    # Enqueue a fresh research run for the project.
    wi = WorkItem(
        project_id=project_id,
        agent_type="industry_research",
        priority=8,
        category="niche_trend_discovery",
        description=f"Post-purge re-research (purged entity {entity_id}: {entity.name})",
        status="pending",
    )
    db.add(wi)
    db.commit()

    return {
        "status": "purged",
        "entity_id": entity_id,
        "project_id": project_id,
        "observations_deleted": obs_deleted,
        "relations_deleted": rel_deleted,
        "work_item_enqueued": wi.id,
        "reason": reason,
    }


@router.get("/entities/{entity_id}/observations", response_model=list[KnowledgeObservationOut])
def list_entity_observations(
    entity_id: int,
    obs_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    q = db.query(KnowledgeObservation).filter(KnowledgeObservation.entity_id == entity_id)
    if obs_type:
        q = q.filter(KnowledgeObservation.observation_type == obs_type)
    return q.order_by(KnowledgeObservation.observed_at.desc()).limit(limit).all()


@router.get("/entities/{entity_id}/screenshots", response_model=list[KnowledgeScreenshotOut])
def list_entity_screenshots(
    entity_id: int,
    flow_session_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    q = db.query(KnowledgeScreenshot).filter(KnowledgeScreenshot.entity_id == entity_id)
    if flow_session_id:
        q = q.filter(KnowledgeScreenshot.flow_session_id == flow_session_id)
    return q.order_by(KnowledgeScreenshot.captured_at.desc()).limit(limit).all()


# ---- Artifacts ----


@router.get("/artifacts", response_model=list[KnowledgeArtifactOut])
def list_artifacts(
    project_id: int = Query(...),
    artifact_type: str | None = Query(None),
    stale_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    q = db.query(KnowledgeArtifact).filter(KnowledgeArtifact.project_id == project_id)
    if artifact_type:
        q = q.filter(KnowledgeArtifact.artifact_type == artifact_type)
    if stale_only:
        q = q.filter(KnowledgeArtifact.is_stale.is_(True))
    return q.order_by(KnowledgeArtifact.generated_at.desc()).all()


@router.get("/artifacts/{artifact_id}", response_model=KnowledgeArtifactOut)
def get_artifact(artifact_id: int, db: Session = Depends(get_db)):
    artifact = db.get(KnowledgeArtifact, artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


# ---- Shortcuts ----


@router.get("/competitors", response_model=list[KnowledgeEntityOut])
def list_competitors(project_id: int = Query(...), db: Session = Depends(get_db)):
    """All `company`-typed entities for a project.

    Historical note: earlier versions gated this on a `competes_with` relation
    existing between the company and the project — but not all agent paths
    create that relation when discovering competitors (e.g. Sarvam.ai and
    Intuit had company entities without any relation), which produced a
    mismatch where the project stats card said `competitor_count=3` but the
    competitors page showed empty. The stats counter uses the same simpler
    `entity_type='company'` filter, so aligning here brings them in sync.
    """
    # v0.18.4: filter out dismissed entities so purged competitors stop
    # appearing in the list. trends-view already does this; bringing
    # competitors into alignment.
    entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "company",
            (KnowledgeEntity.user_signal.is_(None))
            | (KnowledgeEntity.user_signal != "dismissed"),
        )
        .order_by(KnowledgeEntity.name)
        .all()
    )
    if not entities:
        return []

    # Compute dynamic confidence from observation count
    entity_ids = [e.id for e in entities]
    if entity_ids:
        obs_counts = dict(
            db.query(KnowledgeObservation.entity_id, func.count(KnowledgeObservation.id))
            .filter(KnowledgeObservation.entity_id.in_(entity_ids))
            .group_by(KnowledgeObservation.entity_id)
            .all()
        )
        # v0.20.2: uncap to 1.0 so a fully-profiled competitor can show 100%,
        # not 90%. Also stash the raw count + band on metadata_json so the UI
        # can show "X findings · needs N more for next band".
        for e in entities:
            count = obs_counts.get(e.id, 0)
            if count == 0:
                e.confidence = 0.1
            elif count <= 2:
                e.confidence = 0.3
            elif count <= 4:
                e.confidence = 0.6
            elif count <= 7:
                e.confidence = 0.9
            else:
                e.confidence = 1.0
            # Decorate metadata_json (response-only — these are session-level
            # SQLAlchemy attributes that won't auto-commit; safe transient mutation).
            md = dict(e.metadata_json or {})
            md["_finding_count"] = count
            md["_depth_band"] = (
                "empty" if count == 0
                else "shallow" if count <= 2
                else "medium" if count <= 4
                else "deep" if count <= 7
                else "comprehensive"
            )
            e.metadata_json = md

    return entities


@router.get("/flows", response_model=list[KnowledgeEntityOut])
def list_flows(project_id: int = Query(...), db: Session = Depends(get_db)):
    return (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "flow",
        )
        .order_by(KnowledgeEntity.name)
        .all()
    )


# ---- Timeline ----


@router.get("/timeline")
def get_timeline(
    project_id: int = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    observations = (
        db.query(KnowledgeObservation, KnowledgeEntity)
        .join(KnowledgeEntity)
        .filter(KnowledgeEntity.project_id == project_id)
        .order_by(KnowledgeObservation.recorded_at.desc())
        .limit(limit)
        .all()
    )

    artifacts = (
        db.query(KnowledgeArtifact)
        .filter(KnowledgeArtifact.project_id == project_id)
        .order_by(KnowledgeArtifact.generated_at.desc())
        .limit(limit)
        .all()
    )

    items = []
    for obs, entity in observations:
        items.append({
            "id": f"obs-{obs.id}",
            "type": "finding",
            "title": entity.name,
            "content": (obs.content or "")[:200],
            "observation_type": obs.observation_type,
            "entity_name": entity.name,
            "entity_type": entity.entity_type,
            "source_url": obs.source_url,
            "timestamp": obs.recorded_at.isoformat() if obs.recorded_at else None,
        })
    for art in artifacts:
        items.append({
            "id": f"art-{art.id}",
            "type": "report",
            "title": art.title,
            "content": art.title or "",
            "observation_type": None,
            "entity_name": None,
            "entity_type": None,
            "source_url": None,
            "timestamp": art.generated_at.isoformat() if art.generated_at else None,
        })

    items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return items[:limit]


# ---- Summary ----


@router.get("/summary", response_model=KnowledgeSummary)
def get_summary(project_id: int = Query(...), db: Session = Depends(get_db)):
    # Entity counts by type
    type_counts = (
        db.query(KnowledgeEntity.entity_type, func.count(KnowledgeEntity.id))
        .filter(KnowledgeEntity.project_id == project_id)
        .group_by(KnowledgeEntity.entity_type)
        .all()
    )
    entity_count_by_type = {t: c for t, c in type_counts}

    # Total observations (join through entities for project scope)
    total_observations = (
        db.query(func.count(KnowledgeObservation.id))
        .join(KnowledgeEntity, KnowledgeObservation.entity_id == KnowledgeEntity.id)
        .filter(KnowledgeEntity.project_id == project_id)
        .scalar()
    ) or 0

    total_artifacts = (
        db.query(func.count(KnowledgeArtifact.id))
        .filter(KnowledgeArtifact.project_id == project_id)
        .scalar()
    ) or 0

    total_screenshots = (
        db.query(func.count(KnowledgeScreenshot.id))
        .filter(KnowledgeScreenshot.project_id == project_id)
        .scalar()
    ) or 0

    stale_artifact_count = (
        db.query(func.count(KnowledgeArtifact.id))
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.is_stale.is_(True),
        )
        .scalar()
    ) or 0

    return KnowledgeSummary(
        entity_count_by_type=entity_count_by_type,
        total_observations=total_observations,
        total_artifacts=total_artifacts,
        total_screenshots=total_screenshots,
        stale_artifact_count=stale_artifact_count,
    )


# ---- Lens Matrix ----


ALL_LENSES = [
    "product_craft", "growth", "supply", "monetization",
    "technology", "brand_trust", "moat", "trajectory",
]


@router.get("/lens-matrix")
def get_lens_matrix(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Return a matrix of lens tag counts per competitor entity."""
    # Get all company entities for the project
    entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "company",
        )
        .order_by(KnowledgeEntity.name)
        .all()
    )

    competitors = []
    for entity in entities:
        # Get observations with non-null lens_tags
        observations = (
            db.query(KnowledgeObservation)
            .filter(
                KnowledgeObservation.entity_id == entity.id,
                KnowledgeObservation.lens_tags.isnot(None),
            )
            .all()
        )

        lens_counts: dict[str, int] = {lens: 0 for lens in ALL_LENSES}
        total = 0
        for obs in observations:
            tags = obs.lens_tags
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    continue
            if not isinstance(tags, list):
                continue
            total += 1
            for tag in tags:
                if tag in lens_counts:
                    lens_counts[tag] += 1

        competitors.append({
            "id": entity.id,
            "name": entity.name,
            "lens_counts": lens_counts,
            "total_observations": total,
        })

    return {
        "lenses": ALL_LENSES,
        "competitors": competitors,
    }


@router.get("/lens/{lens_name}")
def get_lens_detail(
    lens_name: str,
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Return all observations tagged with a specific lens, grouped by entity."""
    if lens_name not in ALL_LENSES:
        raise HTTPException(status_code=400, detail=f"Unknown lens: {lens_name}")

    # Get all company entities for the project
    entities = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "company",
        )
        .order_by(KnowledgeEntity.name)
        .all()
    )

    result_entities = []
    for entity in entities:
        # Postgres rejects LIKE on JSON; cast to text so the same predicate
        # works on both SQLite (TEXT-backed JSON) and Postgres (json/jsonb).
        observations = (
            db.query(KnowledgeObservation)
            .filter(
                KnowledgeObservation.entity_id == entity.id,
                KnowledgeObservation.lens_tags.isnot(None),
                cast(KnowledgeObservation.lens_tags, String).like(f'%"{lens_name}"%'),
            )
            .order_by(KnowledgeObservation.observed_at.desc())
            .all()
        )

        if not observations:
            continue

        result_entities.append({
            "id": entity.id,
            "name": entity.name,
            "entity_type": entity.entity_type,
            "observations": [
                {
                    "id": o.id,
                    "observation_type": o.observation_type,
                    "content": o.content,
                    "source_url": o.source_url,
                    "lens_tags": o.lens_tags,
                    "observed_at": o.observed_at.isoformat() if o.observed_at else None,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                    "source_agent": o.source_agent,
                }
                for o in observations
            ],
        })

    return {
        "lens": lens_name,
        "entities": result_entities,
    }


# ---- Trends View ----


@router.get("/trends-view")
def get_trends_view(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Return a structured view of industry trends with linked competitors and quantification.

    Dismissed entities are hidden — purge + user-dismiss both land here.
    """
    trends_raw = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.project_id == project_id,
        KnowledgeEntity.entity_type == "trend",
        (KnowledgeEntity.user_signal.is_(None))
        | (KnowledgeEntity.user_signal != "dismissed"),
    ).all()

    result = []
    for t in trends_raw:
        meta = t.metadata_json or {}

        # Get recent observations for display + full count separately so the
        # UI doesn't report a truncated number when there are >5 observations.
        obs = db.query(KnowledgeObservation).filter(
            KnowledgeObservation.entity_id == t.id
        ).order_by(KnowledgeObservation.recorded_at.desc()).limit(5).all()
        obs_total = db.query(func.count(KnowledgeObservation.id)).filter(
            KnowledgeObservation.entity_id == t.id
        ).scalar() or 0

        # Get adoption (companies linked via addresses_trend relation)
        adoptions = (
            db.query(KnowledgeRelation, KnowledgeEntity)
            .join(KnowledgeEntity, KnowledgeRelation.to_entity_id == KnowledgeEntity.id)
            .filter(
                KnowledgeRelation.from_entity_id == t.id,
                KnowledgeRelation.relation_type.in_(["addresses_trend", "adopts_trend"]),
            )
            .all()
        )
        # Also check reverse direction
        adoptions_rev = (
            db.query(KnowledgeRelation, KnowledgeEntity)
            .join(KnowledgeEntity, KnowledgeRelation.from_entity_id == KnowledgeEntity.id)
            .filter(
                KnowledgeRelation.to_entity_id == t.id,
                KnowledgeRelation.relation_type.in_(["addresses_trend", "adopts_trend"]),
            )
            .all()
        )

        adoption_list = []
        for rel, company in list(adoptions) + list(adoptions_rev):
            rel_meta = rel.metadata_json or {}
            adoption_list.append({
                "company_id": company.id,
                "company_name": company.name,
                "adoption_level": rel_meta.get("adoption_level", "unknown"),
            })

        result.append({
            "id": t.id,
            "name": t.name,
            "description": t.description or "",
            "timeline": meta.get("timeline", "present"),
            "category": meta.get("category", "general"),
            "user_signal": t.user_signal,
            "dismissed_reason": t.dismissed_reason,
            "confidence": t.confidence,
            "quantification": {
                k: v for k, v in meta.items()
                if k in ("market_size", "growth_rate", "search_volume", "traffic_volume", "revenue_impact", "user_demand")
            },
            "observations": [
                {
                    "id": o.id,
                    "type": o.observation_type,
                    "content": o.content[:300],
                    "source_url": o.source_url,
                    "recorded_at": o.recorded_at.isoformat(),
                    "lens_tags": o.lens_tags,
                }
                for o in obs
            ],
            "adoption": adoption_list,
            "observation_count": obs_total,
        })

    # Sort: future > emerging > present > past
    order = {"future": 0, "emerging": 1, "present": 2, "past": 3}
    result.sort(key=lambda x: order.get(x["timeline"], 2))

    return {"trends": result}


# ---- Impact Graph ----


@router.get("/impact-graph")
def get_impact_graph(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Return graph data for the impact visualization: trends → effects → companies."""
    # 1. Get trend entities
    trends = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "trend",
        )
        .all()
    )

    # 2. Get effect entities
    effects = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "effect",
        )
        .all()
    )

    # 3. Get company entities
    companies = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "company",
        )
        .all()
    )

    # Build a lookup of entity id → node id prefix
    entity_map: dict[int, dict] = {}
    nodes = []

    # v0.20.1: enrich trend nodes with their top observations too — same
    # pattern as effects. The frontend uses these as evidence links.
    trend_ids = [e.id for e in trends]
    obs_by_trend: dict[int, list[dict]] = {}
    if trend_ids:
        trend_obs = (
            db.query(KnowledgeObservation)
            .filter(KnowledgeObservation.entity_id.in_(trend_ids))
            .order_by(KnowledgeObservation.recorded_at.desc())
            .all()
        )
        for o in trend_obs:
            bucket = obs_by_trend.setdefault(o.entity_id, [])
            if len(bucket) < 3:
                bucket.append({
                    "content": o.content or "",
                    "source_url": o.source_url or None,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                })

    for e in trends:
        node = {
            "id": f"trend-{e.id}",
            "type": "trend",
            "name": e.name,
            "description": e.description or "",
            "metadata": e.metadata_json or {},
            "observations": obs_by_trend.get(e.id, []),
        }
        nodes.append(node)
        entity_map[e.id] = node

    # v0.20.1: enrich effect nodes with top observations for the UI's
    # "expand → show evidence" affordance. One query per effect would be
    # N+1; batch by entity_id and group in Python instead.
    effect_ids = [e.id for e in effects]
    obs_by_effect: dict[int, list[dict]] = {}
    if effect_ids:
        obs_rows = (
            db.query(KnowledgeObservation)
            .filter(KnowledgeObservation.entity_id.in_(effect_ids))
            .order_by(KnowledgeObservation.recorded_at.desc())
            .all()
        )
        for o in obs_rows:
            bucket = obs_by_effect.setdefault(o.entity_id, [])
            if len(bucket) < 3:
                bucket.append({
                    "content": o.content or "",
                    "source_url": o.source_url or None,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                })

    for e in effects:
        meta = e.metadata_json or {}
        node = {
            "id": f"effect-{e.id}",
            "type": "effect",
            "name": e.name,
            "description": e.description or "",
            "metadata": meta,
            "observations": obs_by_effect.get(e.id, []),
        }
        nodes.append(node)
        entity_map[e.id] = node

    for e in companies:
        node = {
            "id": f"company-{e.id}",
            "type": "company",
            "name": e.name,
            "description": e.description or "",
            "metadata": e.metadata_json or {},
        }
        nodes.append(node)
        entity_map[e.id] = node

    # 4. Get relevant relations
    all_entity_ids = list(entity_map.keys())
    if not all_entity_ids:
        return {"nodes": [], "edges": []}

    relations = (
        db.query(KnowledgeRelation)
        .filter(
            KnowledgeRelation.relation_type.in_(("causes", "leads_to", "impacts")),
            KnowledgeRelation.from_entity_id.in_(all_entity_ids),
            KnowledgeRelation.to_entity_id.in_(all_entity_ids),
        )
        .all()
    )

    # 5. Build edges
    edges = []
    for r in relations:
        from_node = entity_map.get(r.from_entity_id)
        to_node = entity_map.get(r.to_entity_id)
        if not from_node or not to_node:
            continue
        meta = r.metadata_json or {}
        edges.append({
            "from": from_node["id"],
            "to": to_node["id"],
            "relation": r.relation_type,
            "metadata": meta,
        })

    return {"nodes": nodes, "edges": edges}


# ---- Work Items & Sessions ----


@router.get("/work-items", response_model=list[WorkItemOut])
def list_work_items(
    project_id: int = Query(...),
    agent_type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(WorkItem).filter(WorkItem.project_id == project_id)
    if agent_type:
        q = q.filter(WorkItem.agent_type == agent_type)
    if status:
        q = q.filter(WorkItem.status == status)
    return q.order_by(WorkItem.priority.asc(), WorkItem.created_at.desc()).limit(limit).all()


@router.get("/sessions", response_model=list[AgentSessionOut])
def list_sessions(
    project_id: int = Query(...),
    agent_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(AgentSession).filter(AgentSession.project_id == project_id)
    if agent_type:
        q = q.filter(AgentSession.agent_type == agent_type)
    return q.order_by(AgentSession.started_at.desc()).limit(limit).all()


@router.get("/project-progress", response_model=ProjectProgressOut)
def project_progress(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """v0.20.0: aggregate project-level work-item state for the header banner.

    Answers 'how much research is left?' in one request. The frontend used to
    have to fetch every work item and count them client-side, which scaled
    badly once projects accumulated 500+ items.
    """
    from datetime import datetime, timedelta

    rows = db.query(WorkItem.status, func.count(WorkItem.id)).filter(
        WorkItem.project_id == project_id
    ).group_by(WorkItem.status).all()
    counts = {status: count for status, count in rows}
    pending = counts.get("pending", 0)
    in_prog = counts.get("in_progress", 0)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    total = pending + in_prog + completed + failed

    # Stalled = in_progress AND (no heartbeat OR heartbeat > 10m old). 10m
    # is generous for slow-LLM cases; a real research call rarely takes
    # longer than that without writing observations.
    stall_cutoff = datetime.utcnow() - timedelta(minutes=10)
    stalled = (
        db.query(func.count(WorkItem.id))
        .filter(
            WorkItem.project_id == project_id,
            WorkItem.status == "in_progress",
        )
        .filter(
            (WorkItem.last_progress_at == None) |  # noqa: E711
            (WorkItem.last_progress_at < stall_cutoff)
        )
        .scalar()
    ) or 0

    # ETA = pending * avg_seconds_per_completed_item over last 50 done.
    last_50 = (
        db.query(WorkItem.started_at, WorkItem.completed_at)
        .filter(
            WorkItem.project_id == project_id,
            WorkItem.status == "completed",
            WorkItem.started_at != None,  # noqa: E711
            WorkItem.completed_at != None,  # noqa: E711
        )
        .order_by(WorkItem.completed_at.desc())
        .limit(50)
        .all()
    )
    durations = [(c - s).total_seconds() for s, c in last_50 if c and s and c > s]
    avg_secs = sum(durations) / len(durations) if len(durations) >= 5 else None
    eta_min = int((pending * avg_secs) / 60) if (avg_secs and pending) else None

    pct = round((completed / total) * 100, 1) if total else 0.0
    return ProjectProgressOut(
        project_id=project_id,
        pending=pending,
        in_progress=in_prog,
        completed=completed,
        failed=failed,
        total=total,
        percent_complete=pct,
        stalled=stalled,
        avg_item_seconds=avg_secs,
        estimated_minutes_remaining=eta_min,
    )


@router.post("/work-items/reap-orphans")
def reap_orphans(
    project_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    """v0.20.0: manual trigger of the same orphan logic the startup hook runs.
    Lets a user clear phantom 'in_progress' rows without redeploying. Scoped
    by project if `project_id` provided, else all projects."""
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(minutes=10)
    q = db.query(WorkItem).filter(
        WorkItem.status == "in_progress",
    ).filter(
        (WorkItem.last_progress_at == None) |  # noqa: E711
        (WorkItem.last_progress_at < cutoff)
    )
    if project_id is not None:
        q = q.filter(WorkItem.project_id == project_id)
    orphans = q.all()
    for w in orphans:
        w.status = "failed"
        w.result_summary = (w.result_summary or "")[:200] + " | Reaped (manual)"
        w.completed_at = datetime.utcnow()
    db.commit()
    return {"reaped": len(orphans)}


@router.post("/competitors/{entity_id}/upload-report")
async def upload_annual_report(
    entity_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """v0.21.0: upload an annual report (PDF) for a competitor.

    Flow: read bytes → extract text via pypdf in-memory → persist text as
    `KnowledgeArtifact(artifact_type='annual_report')` → trigger
    business-history synthesis inline → return both artifact ids.

    Storage: we DO NOT persist the binary. Railway free tier has no
    durable filesystem; only extracted text + synthesized markdown are
    durable. Caller can re-upload if they want the source-of-truth file
    on disk.
    """
    from agent.business_history import (
        BusinessProfile,
        extract_text_from_pdf_bytes,
        synthesize_business_profile,
    )
    from webapp.api.models import Project

    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if entity.entity_type != "company":
        raise HTTPException(status_code=400, detail=f"Entity is {entity.entity_type}, not company")

    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(blob) > 50 * 1024 * 1024:  # 50 MB cap
        raise HTTPException(status_code=413, detail="File exceeds 50MB; please trim non-narrative pages.")

    extracted, meta = extract_text_from_pdf_bytes(blob)
    if not extracted:
        raise HTTPException(
            status_code=422,
            detail=meta.get("extraction_error") or "PDF extraction yielded no text.",
        )

    project = db.get(Project, entity.project_id)
    project_desc = (project.description if project else "") or ""

    # Persist raw extracted text as one artifact (annual_report).
    report_art = KnowledgeArtifact(
        project_id=entity.project_id,
        artifact_type="annual_report",
        title=f"{entity.name} — {file.filename}",
        content_md=extracted[:200_000],
        entity_ids_json=[entity_id],
        generated_by_agent="manual_upload",
    )
    db.add(report_art)
    db.commit()
    db.refresh(report_art)

    # Synthesize business profile inline. Single Groq call ~5-10s; saves the
    # need for a separate worker queue for the v1 surface.
    profile = synthesize_business_profile(
        competitor=entity.name,
        project_name=project.name if project else "this product",
        project_description=project_desc,
        sources=[{
            "title": f"{entity.name} — {file.filename}",
            "text": extracted,
            "year": "",
        }],
    )
    profile_md = profile.to_markdown()
    profile_art = KnowledgeArtifact(
        project_id=entity.project_id,
        artifact_type="business_history",
        title=f"Business history · {entity.name}",
        content_md=profile_md,
        entity_ids_json=[entity_id],
        generated_by_agent="business_history_synth",
    )
    db.add(profile_art)
    db.commit()
    db.refresh(profile_art)

    return {
        "annual_report_artifact_id": report_art.id,
        "business_history_artifact_id": profile_art.id,
        "extraction_meta": meta,
        "profile_summary": {
            "thesis": profile.market_thesis[:200],
            "model": profile.business_model[:200],
            "contrarian_count": len(profile.contrarian_insights),
            "nuance_count": len(profile.nuances),
            "risk_count": len(profile.risks_and_red_flags),
        },
    }


@router.post("/competitors/{entity_id}/auto-fetch-report")
def auto_fetch_report(
    entity_id: int,
    db: Session = Depends(get_db),
):
    """v0.21.1: try to auto-fetch the latest 10-K from SEC EDGAR.

    Returns 404 if the company isn't US-listed or no annual filing was found
    — caller should fall back to manual upload.
    """
    from agent.business_history import synthesize_business_profile
    from agent.sec_edgar import fetch_latest_annual_report
    from webapp.api.models import Project

    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if entity.entity_type != "company":
        raise HTTPException(status_code=400, detail=f"Entity is {entity.entity_type}, not company")

    report = fetch_latest_annual_report(entity.name)
    if not report or not report.raw_text or len(report.raw_text) < 5000:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No annual filing auto-fetched for {entity.name!r}. "
                "Likely not US-listed or filing format unsupported. "
                "Use manual upload instead."
            ),
        )

    project = db.get(Project, entity.project_id)
    project_desc = (project.description if project else "") or ""

    report_art = KnowledgeArtifact(
        project_id=entity.project_id,
        artifact_type="annual_report",
        title=f"{entity.name} — {report.form_type} filed {report.filed}",
        content_md=report.raw_text[:200_000],
        entity_ids_json=[entity_id],
        generated_by_agent="sec_edgar_auto",
    )
    db.add(report_art)
    db.commit()
    db.refresh(report_art)

    profile = synthesize_business_profile(
        competitor=entity.name,
        project_name=project.name if project else "this product",
        project_description=project_desc,
        sources=[{
            "title": f"{report.form_type} {report.filed}",
            "text": report.raw_text,
            "year": report.filed[:4],
        }],
    )
    profile_art = KnowledgeArtifact(
        project_id=entity.project_id,
        artifact_type="business_history",
        title=f"Business history · {entity.name}",
        content_md=profile.to_markdown(),
        entity_ids_json=[entity_id],
        generated_by_agent="business_history_synth",
    )
    db.add(profile_art)
    db.commit()
    db.refresh(profile_art)

    return {
        "source": "sec_edgar",
        "cik": report.cik,
        "form_type": report.form_type,
        "filed": report.filed,
        "doc_url": report.primary_doc_url,
        "annual_report_artifact_id": report_art.id,
        "business_history_artifact_id": profile_art.id,
    }


@router.post("/projects/{project_id}/bulk-upload-reports")
async def bulk_upload_reports(
    project_id: int,
    files: list[UploadFile] = File(...),
    auto_synthesize: bool = Query(True),
    db: Session = Depends(get_db),
):
    """v0.21.1: bulk-upload a folder of PDFs and auto-allocate to the
    matching competitor. Per-file flow:

      1. Extract text via pypdf (in-memory).
      2. Try filename substring match against competitor canonical names.
      3. Fall back to LLM disambiguation (with explicit null option).
      4. Extract fiscal_year + quarter from filename / first-page text.
      5. Save raw extracted text as `KnowledgeArtifact(annual_report)` with
         `entity_ids_json=[matched]` if matched, None if unmatched.

    `auto_synthesize=true` (default): kick off business-history synthesis
    per-matched-competitor in a thread pool after all files are saved.
    Synthesis runs against the *aggregate* of all reports for that competitor
    (aggregated text across multiple uploads), so multi-period uploads
    fold into one rich profile.

    Returns a manifest:
      - matched: [{filename, entity, period, confidence}, ...]
      - unmatched: [{filename, period, reason}, ...]
      - failed: [{filename, error}, ...]
    """
    from agent.business_history import (
        extract_text_from_pdf_bytes,
        synthesize_business_profile,
    )
    from agent.bulk_report_classifier import classify, ClassifiedReport
    from webapp.api.models import Project
    from collections import defaultdict

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    competitors_q = (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "company",
        )
        .all()
    )
    competitors = [{"id": c.id, "name": c.name, "canonical_name": c.canonical_name} for c in competitors_q]
    if not competitors:
        raise HTTPException(
            status_code=400,
            detail="No competitors yet. Run intel agent first so reports have somewhere to land."
        )

    matched: list[dict] = []
    unmatched: list[dict] = []
    failed: list[dict] = []
    new_artifacts_by_entity: dict[int, list[int]] = defaultdict(list)

    # v0.21.3: cap batch size to prevent Railway OOM. 20 PDFs × 50MB =
    # 1GB peak memory if all are buffered simultaneously. We process
    # sequentially with each blob discarded after extraction, but if the
    # CALLER tries to upload 50 files at once we still want to fail fast
    # with a clear error rather than CD8 mid-stream.
    if len(files) > 30:
        raise HTTPException(
            status_code=413,
            detail=f"{len(files)} files exceeds the 30-file batch cap. Split into multiple uploads."
        )

    for f in files:
        # File hygiene
        try:
            blob = await f.read()
        except Exception as exc:
            failed.append({"filename": f.filename, "error": f"read failed: {exc}"})
            continue
        if not blob:
            failed.append({"filename": f.filename, "error": "empty"})
            continue
        if len(blob) > 50 * 1024 * 1024:
            failed.append({"filename": f.filename, "error": "exceeds 50MB"})
            continue

        # Extract
        text, meta = extract_text_from_pdf_bytes(blob)
        if not text:
            failed.append({"filename": f.filename, "error": meta.get("extraction_error") or "extraction yielded nothing"})
            continue

        # Classify
        cr: ClassifiedReport = classify(f.filename or "report.pdf", text, competitors)

        # Build artifact metadata
        period_meta = {}
        if cr.period:
            period_meta = {
                "fiscal_year": cr.period.fiscal_year,
                "quarter": cr.period.quarter,
                "period_label": cr.period.period_label,
                "is_annual": cr.period.is_annual,
            }

        artifact = KnowledgeArtifact(
            project_id=project_id,
            artifact_type="annual_report" if (cr.period is None or cr.period.is_annual) else "quarterly_report",
            title=(
                f"{cr.matched_entity_name or 'Unmatched'} — "
                f"{cr.period.period_label if cr.period else 'undated'} "
                f"({f.filename})"
            ),
            content_md=text[:200_000],
            entity_ids_json=[cr.matched_entity_id] if cr.matched_entity_id else None,
            generated_by_agent=f"bulk_upload:{cr.match_method}",
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)

        record = {
            "filename": f.filename,
            "artifact_id": artifact.id,
            "matched_entity_id": cr.matched_entity_id,
            "matched_entity_name": cr.matched_entity_name,
            "match_confidence": cr.match_confidence,
            "match_method": cr.match_method,
            "period": period_meta or None,
            "reasoning": cr.reasoning,
            "text_chars": cr.text_chars,
        }
        if cr.matched_entity_id and cr.match_confidence in ("high", "medium"):
            matched.append(record)
            new_artifacts_by_entity[cr.matched_entity_id].append(artifact.id)
        else:
            unmatched.append(record)

        # v0.21.3: free the blob ASAP after extraction. With 20+ uploads
        # in a single request, holding all bytes resident is what triggers
        # Railway OOM → CD8 502s.
        del blob

    # v0.21.3: Detach synthesis from the request. Synthesis takes ~5-15s per
    # competitor and bulk uploads can hit 5-20 competitors — total request
    # time can blow past Railway's edge proxy timeout (~60s) and trigger a
    # 502 ROUTER_EXTERNAL_TARGET_CONNECTION_ERROR. We now fire-and-forget
    # synthesis in detached daemon threads, return the manifest immediately,
    # and let the user refresh Industry Pulse to see new profiles land.
    synth_count = 0  # always 0 in v0.21.3 — synthesis is now async
    synthesizing = False
    if auto_synthesize and new_artifacts_by_entity:
        ent_map = {c.id: c for c in competitors_q}
        project_name_snapshot = project.name
        project_desc_snapshot = project.description or ""
        target_ids = list(new_artifacts_by_entity.keys())
        synthesizing = len(target_ids) > 0

        def _synth_one_detached(entity_id: int) -> None:
            """Run in a detached daemon thread — owns its own DB session."""
            entity = ent_map.get(entity_id)
            if not entity:
                return
            from webapp.api.db import SessionLocal
            db_local = SessionLocal()
            try:
                arts = (
                    db_local.query(KnowledgeArtifact)
                    .filter(
                        KnowledgeArtifact.project_id == project_id,
                        KnowledgeArtifact.artifact_type.in_(("annual_report", "quarterly_report")),
                    )
                    .order_by(KnowledgeArtifact.generated_at.desc())
                    .all()
                )
                relevant = [a for a in arts if a.entity_ids_json and entity_id in a.entity_ids_json]
                sources = [
                    {"title": a.title, "text": a.content_md or "", "year": ""}
                    for a in relevant[:6]
                ]
                if not sources:
                    return
                profile = synthesize_business_profile(
                    competitor=entity.name,
                    project_name=project_name_snapshot,
                    project_description=project_desc_snapshot,
                    sources=sources,
                )
                if not profile.market_thesis and not profile.contrarian_insights:
                    return
                prof_art = KnowledgeArtifact(
                    project_id=project_id,
                    artifact_type="business_history",
                    title=f"Business history · {entity.name}",
                    content_md=profile.to_markdown(),
                    entity_ids_json=[entity_id],
                    generated_by_agent="business_history_synth_bulk_async",
                )
                db_local.add(prof_art)
                db_local.commit()
            except Exception as exc:
                logger.warning("[bulk_upload:async] synth failed for entity %s: %s", entity_id, exc)
            finally:
                db_local.close()

        # Cap concurrent synthesis at 3 to avoid Groq rate-limit spikes when
        # many competitors land at once. Threads are daemon so they don't
        # block container shutdown if a deploy lands mid-synthesis.
        import threading
        from concurrent.futures import ThreadPoolExecutor as _TPool

        def _runner():
            with _TPool(max_workers=3) as ex:
                list(ex.map(_synth_one_detached, target_ids))

        threading.Thread(target=_runner, daemon=True).start()

    return {
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "failed_count": len(failed),
        "synthesized_profiles": synth_count,
        "synthesizing": synthesizing,
        "synthesizing_count": len(new_artifacts_by_entity) if synthesizing else 0,
        "matched": matched,
        "unmatched": unmatched,
        "failed": failed,
    }


@router.post("/artifacts/{artifact_id}/reassign")
def reassign_artifact(
    artifact_id: int,
    entity_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """v0.21.1: manually reassign an unmatched / mis-matched bulk-uploaded
    report to a specific competitor. Used by the manifest UI for the user
    to fix LLM/regex misses without re-uploading.
    """
    art = db.get(KnowledgeArtifact, artifact_id)
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity or entity.project_id != art.project_id:
        raise HTTPException(status_code=400, detail="Entity not in this project")
    art.entity_ids_json = [entity_id]
    art.generated_by_agent = (art.generated_by_agent or "") + " | reassigned"
    db.commit()
    return {"reassigned": True, "artifact_id": artifact_id, "entity_id": entity_id}


@router.get("/competitors/{entity_id}/business-history")
def list_business_history(
    entity_id: int,
    db: Session = Depends(get_db),
):
    """List uploaded annual reports + synthesized business-history artifacts
    for a single competitor."""
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    arts = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == entity.project_id,
            KnowledgeArtifact.artifact_type.in_(("annual_report", "business_history")),
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .all()
    )
    relevant = [a for a in arts if a.entity_ids_json and entity_id in a.entity_ids_json]
    reports = [a for a in relevant if a.artifact_type == "annual_report"]
    profiles = [a for a in relevant if a.artifact_type == "business_history"]
    return {
        "reports": [
            {
                "id": a.id,
                "title": a.title,
                "generated_at": a.generated_at.isoformat() if a.generated_at else None,
                "generated_by_agent": a.generated_by_agent,
                "char_count": len(a.content_md or ""),
            }
            for a in reports
        ],
        "profiles": [
            {
                "id": a.id,
                "title": a.title,
                "generated_at": a.generated_at.isoformat() if a.generated_at else None,
                "content_md": a.content_md,
            }
            for a in profiles
        ],
    }


@router.get("/industry-pulse")
def industry_pulse(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """v0.21.1: synthesize an industry pulse from all business-history
    artifacts in the project. Identifies common business models, margin
    patterns, contrarian themes across the competitive set.

    Cached as KnowledgeArtifact(artifact_type='industry_pulse'). Re-runs
    only when called — caller can invalidate by calling refresh=true.
    """
    profiles = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == "business_history",
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .all()
    )
    if not profiles:
        return {
            "competitor_count": 0,
            "synthesis": "",
            "message": "No business-history profiles yet. Upload at least one annual report on a competitor.",
        }

    # Cache hit: return the most recent industry_pulse if newer than every profile.
    pulse_existing = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == "industry_pulse",
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .first()
    )
    if pulse_existing and profiles and pulse_existing.generated_at > profiles[0].generated_at:
        return {
            "competitor_count": len(profiles),
            "synthesis": pulse_existing.content_md,
            "cached": True,
            "generated_at": pulse_existing.generated_at.isoformat(),
        }

    # Build the cross-cut prompt.
    from agent.business_history import _call_llm  # type: ignore[attr-defined]

    snippets: list[str] = []
    for p in profiles[:30]:  # cap aggregate input size
        snippets.append(f"=== {p.title} ===\n{(p.content_md or '')[:6000]}")

    prompt = f"""You are a senior industry analyst synthesizing the competitive landscape \
for **project_id={project_id}**.

Below are business-history briefs on {len(profiles)} competitors. Identify the structural \
patterns across them — the things a sharp investor or operator would notice that no \
single competitor's brief surfaces alone.

Produce a markdown report with these sections:

# Industry Pulse — {len(profiles)} competitors profiled

## Dominant business models
<which models recur. e.g. "7 of 12 are take-rate marketplaces; 3 are SaaS-on-top; 2 are mixed">

## Margin patterns
<gross / operating margin distribution + qualitative read>

## Cross-cutting contrarian themes
<3-5 non-obvious patterns SPANNING competitors. e.g. "Most companies disclose ARR but 4 of them carry significant one-time fees in that line">

## Where the real money is made
<which lever in the value chain captures the margin: distribution, supply, brand, software, network effects>

## Risk concentrations across the set
<common red flags — customer concentration, regulatory exposure, related-party deals>

Hard rules:
- EVERY claim must be grounded in the briefs below. If you can't point at specific competitors, omit it.
- Reference competitors by name when making a claim ("e.g. Acme, Globex").
- Sharp, specific, opinionated. No "growing fast" or "differentiated platform" filler.

BRIEFS:
---
{chr(10).join(snippets)}
---

Return ONLY the markdown report."""

    md = _call_llm(prompt, max_tokens=4096) or ""
    if not md.strip():
        return {
            "competitor_count": len(profiles),
            "synthesis": "",
            "message": "LLM call failed. Try again later.",
        }

    pulse_art = KnowledgeArtifact(
        project_id=project_id,
        artifact_type="industry_pulse",
        title=f"Industry pulse · {len(profiles)} competitors",
        content_md=md,
        entity_ids_json=None,
        generated_by_agent="industry_pulse_synth",
    )
    db.add(pulse_art)
    db.commit()
    db.refresh(pulse_art)

    return {
        "competitor_count": len(profiles),
        "synthesis": md,
        "generated_at": pulse_art.generated_at.isoformat() if pulse_art.generated_at else None,
        "artifact_id": pulse_art.id,
        "cached": False,
    }


@router.post("/competitors/{entity_id}/deepen")
def deepen_competitor(
    entity_id: int,
    n_questions: int = Query(10, ge=3, le=20),
    db: Session = Depends(get_db),
):
    """v0.20.2: enqueue a `competitor_deep_profile` work item for a single
    competitor. The intel agent picks it up and runs the LLM-deep-profile
    flow — generating probing prompts and extracting structured facts.

    Idempotent: if a pending deep-profile item already exists for this
    entity, returns 200 with `created=false`.
    """
    entity = db.get(KnowledgeEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    if entity.entity_type != "company":
        raise HTTPException(status_code=400, detail=f"Entity is {entity.entity_type}, not company")

    # Skip if a pending deepen job for this entity already exists — prevents
    # the user clicking Deepen twice from doubling cost.
    pending = (
        db.query(WorkItem)
        .filter(
            WorkItem.project_id == entity.project_id,
            WorkItem.agent_type == "competitive_intel",
            WorkItem.category == "competitor_deep_profile",
            WorkItem.status == "pending",
        )
        .all()
    )
    for w in pending:
        ctx = w.context_json or {}
        if ctx.get("entity_id") == entity_id or ctx.get("competitor_name") == entity.name:
            return {"created": False, "work_item_id": w.id, "reason": "already pending"}

    item = WorkItem(
        project_id=entity.project_id,
        agent_type="competitive_intel",
        priority=10,
        category="competitor_deep_profile",
        description=f"Deepen profile of {entity.name} via LLM probing prompts",
        context_json={
            "competitor_name": entity.name,
            "entity_id": entity_id,
            "n_questions": n_questions,
        },
        status="pending",
    )
    db.add(item)
    db.commit()
    return {"created": True, "work_item_id": item.id, "competitor": entity.name}


@router.post("/work-items/reseed-discovery")
def reseed_discovery(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """v0.19.1: Re-inject industry_identification + contrarian_discovery work
    items so existing projects pick up the v0.19.0 LLM-as-search path on the
    next intel run. Skips re-creation if pending items already exist for that
    category."""
    from agent.competitive_intel_agent import CompetitiveIntelAgent

    agent = CompetitiveIntelAgent(project_id=project_id, db=db)
    seeds = agent.seed_backlog()
    created = 0
    skipped = 0
    for seed in seeds:
        existing = (
            db.query(WorkItem)
            .filter(
                WorkItem.project_id == project_id,
                WorkItem.agent_type == agent.agent_type,
                WorkItem.category == seed["category"],
                WorkItem.status == "pending",
            )
            .first()
        )
        if existing:
            skipped += 1
            continue
        db.add(WorkItem(
            project_id=project_id,
            agent_type=agent.agent_type,
            priority=seed.get("priority", 8),
            category=seed["category"],
            description=seed["description"],
            context_json=seed.get("context_json"),
            status="pending",
        ))
        created += 1
    db.commit()
    return {"created": created, "skipped_existing_pending": skipped}
