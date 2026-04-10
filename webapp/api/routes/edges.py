"""Edge CRUD routes — manual edge creation + accepting inferred edges."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.api import models, schemas
from webapp.api.db import get_db

router = APIRouter(tags=["edges"])


@router.get("/api/projects/{project_id}/edges", response_model=list[schemas.EdgeOut])
def list_edges(project_id: int, db: Session = Depends(get_db)):
    return db.query(models.Edge).filter(models.Edge.project_id == project_id).all()


@router.post("/api/projects/{project_id}/edges", response_model=schemas.EdgeOut, status_code=201)
def create_edge(project_id: int, payload: schemas.EdgeCreate, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Validate both screens belong to this project
    for sid in (payload.from_screen_id, payload.to_screen_id):
        s = db.get(models.Screen, sid)
        if not s or s.project_id != project_id:
            raise HTTPException(status_code=400, detail=f"Screen {sid} not in project {project_id}")
    edge = models.Edge(
        project_id=project_id,
        from_screen_id=payload.from_screen_id,
        to_screen_id=payload.to_screen_id,
        trigger=payload.trigger,
    )
    db.add(edge)
    db.commit()
    db.refresh(edge)
    return edge


@router.delete("/api/edges/{edge_id}", status_code=204)
def delete_edge(edge_id: int, db: Session = Depends(get_db)):
    edge = db.get(models.Edge, edge_id)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    db.delete(edge)
    db.commit()
