"""AppUAT FastAPI backend.

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
from webapp.api.routes import edges, plans, projects, screens

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(
    title="AppUAT API",
    description="Generic UAT planning tool — map any app, generate test plans",
    version="0.1.0",
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


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    logging.getLogger(__name__).info("AppUAT API started — DB initialized")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
