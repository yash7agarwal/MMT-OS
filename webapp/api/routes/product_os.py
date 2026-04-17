"""Product OS API routes — orchestrator control and query engine."""
from __future__ import annotations

import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.api.db import get_db
from webapp.api.schemas import ProductOSStatus, QueryRequest, QueryResponse

router = APIRouter(prefix="/api/product-os", tags=["product-os"])


@router.post("/start")
def start_orchestrator(project_id: int, db: Session = Depends(get_db)):
    """Start the Product OS orchestrator daemon."""
    from agent.product_os_orchestrator import get_orchestrator

    orch = get_orchestrator(project_id)
    orch.start_daemon()
    return {"status": "started", "project_id": project_id}


@router.post("/stop")
def stop_orchestrator(project_id: int, db: Session = Depends(get_db)):
    """Stop the Product OS orchestrator daemon."""
    from agent.product_os_orchestrator import get_orchestrator

    orch = get_orchestrator(project_id)
    orch.stop_daemon()
    return {"status": "stopped", "project_id": project_id}


@router.get("/status")
def get_status(project_id: int, db: Session = Depends(get_db)):
    """Get the current Product OS status."""
    from agent.product_os_orchestrator import get_orchestrator

    orch = get_orchestrator(project_id)
    return orch.get_status()


@router.post("/run/{agent_type}")
def run_agent(agent_type: str, project_id: int, db: Session = Depends(get_db)):
    """Trigger a single agent session in a background thread."""
    from agent.product_os_orchestrator import get_orchestrator

    orch = get_orchestrator(project_id)

    def _run():
        try:
            orch.run_agent_session(agent_type)
        except Exception:
            pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {
        "status": "started",
        "agent_type": agent_type,
        "project_id": project_id,
        "message": f"Agent session started in background.",
    }


@router.post("/run-all")
def run_all_agents(project_id: int, db: Session = Depends(get_db)):
    """Run all non-device agents in parallel."""
    from agent.product_os_orchestrator import get_orchestrator

    orch = get_orchestrator(project_id)
    started = []

    for agent_type in ["competitive_intel", "industry_research"]:
        def _run(at=agent_type):
            try:
                orch.run_agent_session(at)
            except Exception:
                pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        started.append(agent_type)

    return {
        "status": "started",
        "agents": started,
        "project_id": project_id,
        "message": f"Running {', '.join(started)} in parallel.",
    }


@router.post("/query", response_model=QueryResponse)
def query_knowledge(payload: QueryRequest, db: Session = Depends(get_db)):
    """Query the Product OS knowledge engine."""
    from agent.query_engine import QueryEngine

    engine = QueryEngine(payload.project_id, db)
    result = engine.query(payload.question)
    return QueryResponse(**result)


@router.post("/digest")
def generate_digest(project_id: int, db: Session = Depends(get_db)):
    """Generate a daily digest of agent findings."""
    from agent.product_os_orchestrator import get_orchestrator

    orch = get_orchestrator(project_id)
    digest = orch.generate_daily_digest()
    return {"digest": digest, "project_id": project_id}
