"""Prism FastAPI backend.

Run from repo root:
    .venv/bin/python3 -m uvicorn webapp.api.main:app --reload --port 8000

API docs available at http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path so we can import utils.* / tools.* / agent.*
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from webapp.api.db import init_db
from webapp.api.routes import cost, digest, edges, knowledge, plans, product_os, projects, screens, xproj

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="Prism API",
    description="Product intelligence platform — competitive research, trends, impacts. UAT lives in Loupe.",
    version="0.13.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(projects.router)
app.include_router(screens.router)
app.include_router(edges.router)
app.include_router(plans.router)
app.include_router(knowledge.router)
app.include_router(product_os.router)
app.include_router(cost.router)
app.include_router(digest.router)
app.include_router(xproj.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    log = logging.getLogger(__name__)
    log.info("Prism API started — DB initialized")

    # Auto-start the research orchestrator daemon per project, for production
    # deploys (Railway). Gated behind PRISM_AUTO_DAEMON=1 so local `uvicorn
    # --reload` cycles don't spam provider APIs on every restart.
    import os
    if os.environ.get("PRISM_AUTO_DAEMON", "").strip() in ("1", "true", "yes"):
        from webapp.api.db import SessionLocal
        from webapp.api.models import Project
        from agent.product_os_orchestrator import ProductOSOrchestrator

        db = SessionLocal()
        try:
            projects = db.query(Project).all()
            for p in projects:
                try:
                    ProductOSOrchestrator(project_id=p.id).start_daemon()
                    log.info(
                        "[auto-daemon] started orchestrator for project %d (%s)",
                        p.id, p.name,
                    )
                except Exception as exc:
                    log.error(
                        "[auto-daemon] failed to start for project %d: %s",
                        p.id, exc, exc_info=True,
                    )
        finally:
            db.close()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
