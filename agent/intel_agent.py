"""IntelAgent — one agent type, one thread, two workstreams.

v0.10.3: competitive_intel + industry_research used to run in two separate
threads with their own configs, work queues, and orchestrator locks — but
both hit the web via the same providers, both write to the same knowledge
graph, and both tune the same kinds of prompts. The only thing keeping
them split was history.

This class composes the two. One orchestrator thread runs IntelAgent;
IntelAgent runs both underlying sessions sequentially and merges their
results. The existing CompetitiveIntelAgent and IndustryResearchAgent
classes stay (so `/api/product-os/run/competitive_intel` etc. still work
for explicit direct invocation), but the default autopilot path now runs
one thread instead of two.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class IntelAgent:
    """Compound agent. Not an AutonomousAgent subclass — it exposes only
    the method surface the orchestrator needs (run_session, seed_backlog).
    Internally it delegates to CompetitiveIntelAgent + IndustryResearchAgent.
    """

    agent_type = "intel"

    def __init__(self, project_id: int, db: Session):
        from agent.competitive_intel_agent import CompetitiveIntelAgent
        from agent.industry_research_agent import IndustryResearchAgent

        self.project_id = project_id
        self.db = db
        self._competitive = CompetitiveIntelAgent(project_id, db)
        self._industry = IndustryResearchAgent(project_id, db)

    def seed_backlog(self) -> list[dict]:
        """Seed both workstreams. Returns the combined list of seeded items."""
        items: list[dict] = []
        items.extend(self._competitive.seed_backlog() or [])
        items.extend(self._industry.seed_backlog() or [])
        return items

    def run_session(self, **kwargs: Any) -> dict:
        """Run competitive + industry in sequence within a single session.

        Returns a merged result so the orchestrator's existing session-result
        shape keeps working. `items_completed` and `items_failed` are summed;
        the per-workstream detail is under `competitive` and `industry`.
        """
        # Split the per-session budget across the two workstreams so a single
        # intel session doesn't blow past the configured max.
        max_items = kwargs.get("max_items_per_session")
        max_dur = kwargs.get("max_session_duration_s")
        split_kwargs = dict(kwargs)
        if max_items is not None:
            split_kwargs["max_items_per_session"] = max(1, int(max_items) // 2)
        if max_dur is not None:
            split_kwargs["max_session_duration_s"] = int(max_dur) // 2

        logger.info("[intel] Running competitive_intel leg")
        try:
            c_result = self._competitive.run_session(**split_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[intel] competitive_intel leg raised")
            c_result = {"status": "error", "message": str(exc),
                        "items_completed": 0, "items_failed": 0}

        logger.info("[intel] Running industry_research leg")
        try:
            i_result = self._industry.run_session(**split_kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[intel] industry_research leg raised")
            i_result = {"status": "error", "message": str(exc),
                        "items_completed": 0, "items_failed": 0}

        return {
            "status": "completed",
            "items_completed": (c_result.get("items_completed", 0)
                                + i_result.get("items_completed", 0)),
            "items_failed": (c_result.get("items_failed", 0)
                             + i_result.get("items_failed", 0)),
            "knowledge_added": (c_result.get("knowledge_added", 0)
                                + i_result.get("knowledge_added", 0)),
            "competitive": c_result,
            "industry": i_result,
        }
