"""Figma import routes — one-shot fetch + local storage of Figma design files.

After a successful import, all downstream UAT runs and planners read from the
DB + local disk rather than hitting Figma's API. This eliminates Figma quota
burn on repeat runs.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from webapp.api import models, schemas
from webapp.api.db import get_db
from webapp.api.services.figma_importer import import_figma_file

logger = logging.getLogger(__name__)

router = APIRouter(tags=["figma"])


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "/api/projects/{project_id}/figma/imports",
    response_model=list[schemas.FigmaImportSummary],
)
def list_imports(project_id: int, db: Session = Depends(get_db)):
    return (
        db.query(models.FigmaImport)
        .filter(models.FigmaImport.project_id == project_id)
        .order_by(models.FigmaImport.imported_at.desc())
        .all()
    )


@router.get("/api/figma/imports/{import_id}", response_model=schemas.FigmaImportOut)
def get_import(import_id: int, db: Session = Depends(get_db)):
    imp = db.get(models.FigmaImport, import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Import not found")
    return imp


@router.get("/api/figma/imports/{import_id}/frames/{frame_id}/image")
def get_frame_image(
    import_id: int,
    frame_id: int,
    db: Session = Depends(get_db),
):
    frame = db.get(models.FigmaFrame, frame_id)
    if not frame or frame.import_id != import_id:
        raise HTTPException(status_code=404, detail="Frame not found")
    if not frame.image_path or not Path(frame.image_path).exists():
        raise HTTPException(status_code=404, detail="Image not on disk")
    return FileResponse(frame.image_path, media_type="image/png")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post(
    "/api/projects/{project_id}/figma/imports",
    response_model=schemas.FigmaImportOut,
    status_code=201,
)
def create_import(
    project_id: int,
    payload: schemas.FigmaImportCreate,
    db: Session = Depends(get_db),
):
    """Synchronously fetch the Figma file and persist it locally.

    Takes 30-90s depending on the number of frames. Returns the FigmaImport row
    with status=ready on success or status=failed on error (row still persisted
    so the caller can inspect the error).
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    imp = import_figma_file(
        project_id=project_id,
        figma_file_id=payload.figma_file_id,
        db=db,
    )
    return imp


@router.delete("/api/figma/imports/{import_id}", status_code=204)
def delete_import(import_id: int, db: Session = Depends(get_db)):
    imp = db.get(models.FigmaImport, import_id)
    if not imp:
        raise HTTPException(status_code=404, detail="Import not found")
    # Best-effort on-disk cleanup
    if imp.raw_json_path:
        try:
            import_dir = Path(imp.raw_json_path).parent
            if import_dir.exists() and import_dir.is_dir():
                import shutil
                shutil.rmtree(import_dir, ignore_errors=True)
        except Exception as exc:
            logger.warning(f"Failed to clean up import dir: {exc}")
    db.delete(imp)
    db.commit()
