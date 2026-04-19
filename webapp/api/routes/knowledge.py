"""Knowledge graph API routes — read-only access to Product OS intelligence."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

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
        for e in entities:
            count = obs_counts.get(e.id, 0)
            if count == 0:
                e.confidence = 0.1
            elif count <= 2:
                e.confidence = 0.3
            elif count <= 4:
                e.confidence = 0.6
            else:
                e.confidence = 0.9

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
    competitor_ids = (
        db.query(KnowledgeRelation.from_entity_id)
        .join(KnowledgeEntity, KnowledgeRelation.from_entity_id == KnowledgeEntity.id)
        .filter(
            KnowledgeRelation.relation_type == "competes_with",
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == "company",
        )
        .union(
            db.query(KnowledgeRelation.to_entity_id)
            .join(KnowledgeEntity, KnowledgeRelation.to_entity_id == KnowledgeEntity.id)
            .filter(
                KnowledgeRelation.relation_type == "competes_with",
                KnowledgeEntity.project_id == project_id,
                KnowledgeEntity.entity_type == "company",
            )
        )
        .all()
    )
    ids = [row[0] for row in competitor_ids]
    if not ids:
        return []
    entities = (
        db.query(KnowledgeEntity)
        .filter(KnowledgeEntity.id.in_(ids))
        .order_by(KnowledgeEntity.name)
        .all()
    )

    # Compute dynamic confidence from observation count
    entity_ids = [e.id for e in entities]
    if entity_ids:
        obs_counts = dict(
            db.query(KnowledgeObservation.entity_id, func.count(KnowledgeObservation.id))
            .filter(KnowledgeObservation.entity_id.in_(entity_ids))
            .group_by(KnowledgeObservation.entity_id)
            .all()
        )
        for e in entities:
            count = obs_counts.get(e.id, 0)
            if count == 0:
                e.confidence = 0.1
            elif count <= 2:
                e.confidence = 0.3
            elif count <= 4:
                e.confidence = 0.6
            else:
                e.confidence = 0.9

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
        # Use LIKE to find observations where lens_tags JSON contains the lens name
        observations = (
            db.query(KnowledgeObservation)
            .filter(
                KnowledgeObservation.entity_id == entity.id,
                KnowledgeObservation.lens_tags.isnot(None),
                KnowledgeObservation.lens_tags.like(f'%"{lens_name}"%'),
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
    """Return a structured view of industry trends with linked competitors and quantification."""
    trends_raw = db.query(KnowledgeEntity).filter(
        KnowledgeEntity.project_id == project_id,
        KnowledgeEntity.entity_type == "trend",
    ).all()

    result = []
    for t in trends_raw:
        meta = t.metadata_json or {}

        # Get observations
        obs = db.query(KnowledgeObservation).filter(
            KnowledgeObservation.entity_id == t.id
        ).order_by(KnowledgeObservation.recorded_at.desc()).limit(5).all()

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
            "observation_count": len(obs),
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

    for e in trends:
        node = {
            "id": f"trend-{e.id}",
            "type": "trend",
            "name": e.name,
            "description": e.description or "",
            "metadata": e.metadata_json or {},
        }
        nodes.append(node)
        entity_map[e.id] = node

    for e in effects:
        meta = e.metadata_json or {}
        node = {
            "id": f"effect-{e.id}",
            "type": "effect",
            "name": e.name,
            "description": e.description or "",
            "metadata": meta,
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
