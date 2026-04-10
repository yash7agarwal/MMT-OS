"""Screen routes — bulk upload, list, edit, delete, flow inference.

Bulk upload: PMs drag-drop multiple screenshots at once. Each is analyzed
independently with Claude vision in parallel. After upload, the user can
trigger flow inference to propose edges.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from webapp.api import models, schemas
from webapp.api.db import SCREENSHOTS_DIR, get_db
from webapp.api.services.flow_inferrer import infer_flow
from webapp.api.services.screen_analyzer import analyze_screen

logger = logging.getLogger(__name__)

router = APIRouter(tags=["screens"])

# Thread pool for parallel Claude vision calls (network-bound, releases GIL)
_EXECUTOR = ThreadPoolExecutor(max_workers=6)


@router.get("/api/projects/{project_id}/screens", response_model=list[schemas.ScreenOut])
def list_screens(project_id: int, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return (
        db.query(models.Screen)
        .filter(models.Screen.project_id == project_id)
        .order_by(models.Screen.discovered_at.asc())
        .all()
    )


@router.post(
    "/api/projects/{project_id}/screens/bulk",
    response_model=list[schemas.ScreenOut],
    status_code=201,
)
async def upload_screens_bulk(
    project_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """Upload multiple screenshots at once. Each is analyzed in parallel by Claude."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    project_dir = SCREENSHOTS_DIR / str(project_id)
    project_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Save all files to disk and read bytes
    saved: list[tuple[str, bytes, str]] = []  # (path, bytes, original_filename)
    for f in files:
        if not f.filename:
            continue
        content = await f.read()
        unique_id = uuid.uuid4().hex[:12]
        ext = Path(f.filename).suffix or ".png"
        path = project_dir / f"{unique_id}{ext}"
        path.write_bytes(content)
        saved.append((str(path), content, f.filename))

    # Step 2: Analyze each screen in parallel using a thread pool
    loop = asyncio.get_event_loop()
    analyses = await asyncio.gather(
        *[loop.run_in_executor(_EXECUTOR, analyze_screen, content) for _, content, _ in saved]
    )

    # Step 3: Persist Screen rows (incl. context_hints from analyzer)
    created: list[models.Screen] = []
    for (path, _content, original), result in zip(saved, analyses):
        screen = models.Screen(
            project_id=project_id,
            name=result.get("name") or Path(original).stem,
            display_name=result.get("display_name"),
            purpose=result.get("purpose"),
            screenshot_path=path,
            elements=result.get("elements", []),
            context_hints=result.get("context_hints"),
        )
        db.add(screen)
        created.append(screen)

    db.commit()
    for s in created:
        db.refresh(s)

    # Step 4: Auto-run flow inference if there are now ≥2 screens.
    # Edges with confidence ≥0.85 are auto-created. Lower-confidence proposals
    # are still available via the explicit /infer-flow endpoint for review.
    all_screens = (
        db.query(models.Screen).filter(models.Screen.project_id == project_id).all()
    )
    if len(all_screens) >= 2:
        loop = asyncio.get_event_loop()
        screens_data = [
            {
                "id": s.id,
                "name": s.name,
                "display_name": s.display_name,
                "purpose": s.purpose,
                "context_hints": s.context_hints,
                "elements": s.elements or [],
            }
            for s in all_screens
        ]
        try:
            inference = await loop.run_in_executor(_EXECUTOR, infer_flow, screens_data)
            existing_edges = {
                (e.from_screen_id, e.to_screen_id)
                for e in db.query(models.Edge)
                .filter(models.Edge.project_id == project_id)
                .all()
            }
            valid_ids = {s.id for s in all_screens}
            auto_created = 0
            for e in inference.get("proposed_edges", []):
                if e.get("confidence", 0) < 0.85:
                    continue
                pair = (e.get("from_screen_id"), e.get("to_screen_id"))
                if pair[0] not in valid_ids or pair[1] not in valid_ids:
                    continue
                if pair in existing_edges:
                    continue
                db.add(
                    models.Edge(
                        project_id=project_id,
                        from_screen_id=pair[0],
                        to_screen_id=pair[1],
                        trigger=e.get("trigger", "auto-inferred"),
                    )
                )
                auto_created += 1
            if auto_created:
                db.commit()
                logger.info(
                    f"[upload_bulk] Auto-created {auto_created} high-confidence edge(s) for project {project_id}"
                )
        except Exception as exc:
            logger.warning(f"[upload_bulk] Auto-inference failed (non-fatal): {exc}")

    return created


@router.get("/api/screens/{screen_id}/image")
def get_screen_image(screen_id: int, db: Session = Depends(get_db)):
    """Serve the raw screenshot file."""
    screen = db.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    if not Path(screen.screenshot_path).exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing")
    return FileResponse(screen.screenshot_path, media_type="image/png")


@router.patch("/api/screens/{screen_id}", response_model=schemas.ScreenOut)
def update_screen(
    screen_id: int,
    payload: schemas.ScreenUpdate,
    db: Session = Depends(get_db),
):
    screen = db.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(screen, field, value)
    db.commit()
    db.refresh(screen)
    return screen


@router.post("/api/screens/{screen_id}/reanalyze", response_model=schemas.ScreenOut)
def reanalyze_screen(screen_id: int, db: Session = Depends(get_db)):
    """Re-run Claude vision analysis on an existing screen.

    Useful after fixing the analyzer (e.g., adding JPG support) — re-analyzes
    the screen using its already-saved file on disk. No re-upload needed.
    """
    screen = db.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    if not Path(screen.screenshot_path).exists():
        raise HTTPException(status_code=404, detail="Screenshot file missing on disk")

    image_bytes = Path(screen.screenshot_path).read_bytes()
    result = analyze_screen(image_bytes)

    screen.name = result.get("name") or screen.name
    screen.display_name = result.get("display_name") or screen.display_name
    screen.purpose = result.get("purpose") or screen.purpose
    screen.elements = result.get("elements", [])
    screen.context_hints = result.get("context_hints")
    db.commit()
    db.refresh(screen)
    return screen


@router.post("/api/projects/{project_id}/screens/reanalyze-failed", response_model=list[schemas.ScreenOut])
def reanalyze_failed_screens(project_id: int, db: Session = Depends(get_db)):
    """Bulk re-analyze any screens in this project that previously failed analysis.

    Identifies broken screens by name == 'unknown_screen' OR display_name == 'Unknown Screen'.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    broken = (
        db.query(models.Screen)
        .filter(
            models.Screen.project_id == project_id,
            models.Screen.name == "unknown_screen",
        )
        .all()
    )
    fixed: list[models.Screen] = []
    for s in broken:
        try:
            if not Path(s.screenshot_path).exists():
                continue
            image_bytes = Path(s.screenshot_path).read_bytes()
            result = analyze_screen(image_bytes)
            if result.get("name") and result["name"] != "unknown_screen":
                s.name = result["name"]
                s.display_name = result.get("display_name") or s.display_name
                s.purpose = result.get("purpose") or s.purpose
                s.elements = result.get("elements", [])
                s.context_hints = result.get("context_hints")
                fixed.append(s)
        except Exception as exc:
            logger.warning(f"Re-analyze failed for screen {s.id}: {exc}")
    db.commit()
    for s in fixed:
        db.refresh(s)
    return fixed


@router.delete("/api/screens/{screen_id}", status_code=204)
def delete_screen(screen_id: int, db: Session = Depends(get_db)):
    screen = db.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    # Best-effort delete the file
    try:
        Path(screen.screenshot_path).unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to unlink screenshot: {e}")
    db.delete(screen)
    db.commit()


@router.post(
    "/api/projects/{project_id}/infer-flow",
    response_model=schemas.FlowInferenceResult,
)
def infer_project_flow(project_id: int, db: Session = Depends(get_db)):
    """Run flow inference: ask Claude to propose edges between all uploaded screens.

    Returns proposed edges (with confidence + reasoning) for the user to review.
    The edges are NOT auto-saved — the user must accept them.
    """
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    screens = (
        db.query(models.Screen)
        .filter(models.Screen.project_id == project_id)
        .all()
    )
    if not screens:
        return schemas.FlowInferenceResult(
            proposed_edges=[], home_screen_id=None, branches=[]
        )

    screens_data = [
        {
            "id": s.id,
            "name": s.name,
            "display_name": s.display_name,
            "purpose": s.purpose,
            "elements": s.elements or [],
        }
        for s in screens
    ]
    result = infer_flow(screens_data)

    # Validate that screen ids exist (Claude might hallucinate)
    valid_ids = {s.id for s in screens}
    proposed_edges = [
        schemas.InferredEdge(**e)
        for e in result.get("proposed_edges", [])
        if e.get("from_screen_id") in valid_ids and e.get("to_screen_id") in valid_ids
    ]
    home = result.get("home_screen_id")
    if home not in valid_ids:
        home = None

    return schemas.FlowInferenceResult(
        proposed_edges=proposed_edges,
        home_screen_id=home,
        branches=result.get("branches", []),
    )
