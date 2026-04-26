"""Executive report endpoints (v0.17.0).

Four endpoints:
  POST /api/reports/generate              — kick off generation, returns job_id
  GET  /api/reports/jobs/{job_id}         — poll status
  GET  /api/reports/{artifact_id}/download — stream PDF or xlsx
  GET  /api/reports/recent                — list past reports for the Reports tab

In-process job queue: same pattern as `product_os.run_agent` (a thread
runs the work, a module-level dict holds JobState). Acceptable because
Railway runs single-replica today; multi-replica needs Redis-backed jobs
(documented as Phase 3).
"""
from __future__ import annotations

import io
import json
import logging
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from agent.report_generator import (
    JobState,
    generate_report,
    get_job,
    list_jobs_for_project,
    render_from_manifest,
    ARTIFACT_TYPE,
    _jobs as _report_jobs,  # touched directly from the thread runner below
)
from webapp.api.db import SessionLocal, get_db
from webapp.api.models import KnowledgeArtifact, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------

@router.post("/generate")
def generate(
    project_id: int = Query(...),
    formats: str = Query("pdf,xlsx"),  # comma-separated
    include_loupe: bool = Query(True),
    db: Session = Depends(get_db),
):
    """Kick off generation in a background thread. Returns a job_id."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    fmts = [f.strip() for f in formats.split(",") if f.strip() in ("pdf", "xlsx")]
    if not fmts:
        raise HTTPException(400, f"formats must include pdf or xlsx; got {formats!r}")

    job_id = uuid.uuid4().hex
    job = JobState(
        job_id=job_id,
        project_id=project_id,
        status="queued",
        progress="Queued",
        started_at=datetime.utcnow().isoformat() + "Z",
    )
    _report_jobs[job_id] = job

    def _run():
        # New session per thread — never share with the request-scoped one
        thread_db = SessionLocal()
        try:
            job.status = "running"
            generate_report(
                db=thread_db,
                project_id=project_id,
                formats=fmts,
                include_loupe=include_loupe,
                job=job,
            )
            # status / artifact_id set by generate_report on success
        except Exception as exc:
            logger.error(f"[reports] job {job_id} failed: {exc}", exc_info=True)
            job.status = "failed"
            job.error = str(exc)
        finally:
            thread_db.close()

    threading.Thread(target=_run, daemon=True).start()

    return {
        "job_id": job_id,
        "status": "queued",
        "project_id": project_id,
        "formats": fmts,
        "include_loupe": include_loupe,
    }


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return {
        "job_id": job.job_id,
        "project_id": job.project_id,
        "status": job.status,
        "progress": job.progress,
        "artifact_id": job.artifact_id,
        "error": job.error,
        "started_at": job.started_at,
    }


# ---------------------------------------------------------------------------
# GET /{artifact_id}/download
# ---------------------------------------------------------------------------

@router.get("/{artifact_id}/download")
def download(
    artifact_id: int,
    format: str = Query("pdf"),
    db: Session = Depends(get_db),
):
    """Stream the PDF or xlsx for a previously-generated report.

    Re-renders from the cached manifest — no LLM cost, no DB writes.
    """
    if format not in ("pdf", "xlsx"):
        raise HTTPException(400, f"format must be pdf or xlsx, got {format!r}")

    try:
        binary = render_from_manifest(db, artifact_id, fmt=format)
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    artifact = db.get(KnowledgeArtifact, artifact_id)
    project_name = "report"
    if artifact:
        # Look up the project for a friendlier filename
        proj = db.get(Project, artifact.project_id)
        if proj:
            project_name = proj.name.replace(" ", "_").lower()

    date_stamp = datetime.utcnow().strftime("%Y%m%d")
    if format == "pdf":
        filename = f"{project_name}_executive_report_{date_stamp}.pdf"
        media_type = "application/pdf"
    else:
        filename = f"{project_name}_data_{date_stamp}.xlsx"
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return StreamingResponse(
        io.BytesIO(binary),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# GET /recent
# ---------------------------------------------------------------------------

@router.get("/recent")
def recent(
    project_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """List past executive reports for the Reports tab."""
    rows = (
        db.query(KnowledgeArtifact)
        .filter(
            KnowledgeArtifact.project_id == project_id,
            KnowledgeArtifact.artifact_type == ARTIFACT_TYPE,
        )
        .order_by(KnowledgeArtifact.generated_at.desc())
        .limit(20)
        .all()
    )
    out = []
    for r in rows:
        try:
            m = json.loads(r.content_md or "{}")
        except json.JSONDecodeError:
            m = {}
        out.append({
            "artifact_id": r.id,
            "title": r.title,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "content_hash": m.get("content_hash"),
            "stats": m.get("stats", {}),
            "rec_count": len(m.get("recommendations", []) or []),
            "loupe_runs_included": m.get("loupe_run_count", 0),
        })
    return out
