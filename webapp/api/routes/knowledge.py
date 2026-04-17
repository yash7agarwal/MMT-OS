"""Knowledge graph API routes — read-only access to Product OS intelligence."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
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
)

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
