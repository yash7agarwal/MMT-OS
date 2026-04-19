"""Project CRUD routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.api import models, schemas
from webapp.api.db import get_db

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[schemas.ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.query(models.Project).order_by(models.Project.created_at.desc()).all()


@router.post("", response_model=schemas.ProjectOut, status_code=201)
def create_project(payload: schemas.ProjectCreate, db: Session = Depends(get_db)):
    project = models.Project(
        name=payload.name,
        app_package=payload.app_package,
        description=payload.description,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    if payload.enable_intelligence:
        import threading
        from agent.product_os_orchestrator import get_orchestrator

        _project_id = project.id

        def _start_agents():
            orch = get_orchestrator(_project_id)
            orch.run_agent_session("intel")

        threading.Thread(target=_start_agents, daemon=True).start()

    return project


@router.get("/{project_id}", response_model=schemas.ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    entity_count = db.query(models.KnowledgeEntity).filter(models.KnowledgeEntity.project_id == project_id).count()
    observation_count = db.query(models.KnowledgeObservation).join(models.KnowledgeEntity).filter(models.KnowledgeEntity.project_id == project_id).count()
    competitor_count = db.query(models.KnowledgeEntity).filter(models.KnowledgeEntity.project_id == project_id, models.KnowledgeEntity.entity_type == "company").count()
    stats = schemas.ProjectStats(
        screen_count=db.query(models.Screen).filter(models.Screen.project_id == project_id).count(),
        edge_count=db.query(models.Edge).filter(models.Edge.project_id == project_id).count(),
        plan_count=db.query(models.TestPlan).filter(models.TestPlan.project_id == project_id).count(),
        entity_count=entity_count,
        observation_count=observation_count,
        competitor_count=competitor_count,
    )
    return schemas.ProjectDetail(
        id=project.id,
        name=project.name,
        app_package=project.app_package,
        description=project.description,
        created_at=project.created_at,
        stats=stats,
    )


@router.patch("/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: int, payload: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
