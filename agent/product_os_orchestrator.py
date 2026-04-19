"""Product OS Orchestrator — schedules and coordinates autonomous agents.

Manages agent sessions, device locking, token budgets, and cross-agent
coordination. Can run as a daemon or be triggered for single sessions.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from webapp.api.db import SessionLocal
from webapp.api.models import (
    AgentSession,
    KnowledgeArtifact,
    KnowledgeEntity,
    KnowledgeObservation,
    KnowledgeScreenshot,
    Project,
    WorkItem,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default agent configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    # v0.10.3 — competitive_intel + industry_research merged into "intel".
    # Budget is the sum of what the two had: 8 items / 900s per session.
    # Direct-invocation API paths (/api/product-os/run/competitive_intel) still
    # work via _create_agent dispatch.
    "intel": {
        "interval_hours": 6,
        "max_session_duration_s": 900,
        "max_items_per_session": 8,
        "requires_device": False,
    },
    "ux_intel": {
        "interval_hours": 8,
        "max_session_duration_s": 900,
        "max_items_per_session": 3,
        "requires_device": True,
    },
    "impact_analysis": {
        "interval_hours": 24,
        "max_session_duration_s": 300,
        "max_items_per_session": 2,
        "requires_device": False,
    },
}


class ProductOSOrchestrator:
    """Schedules and coordinates Product OS autonomous agents."""

    def __init__(self, project_id: int, config: dict | None = None):
        self.project_id = project_id
        self.config = config or dict(DEFAULT_CONFIG)
        self._running = False
        self._daemon_thread: threading.Thread | None = None
        self._device_lock = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {
            agent_type: threading.Lock() for agent_type in self.config
        }

    # ------------------------------------------------------------------
    # Core: run a single agent session
    # ------------------------------------------------------------------

    def run_agent_session(self, agent_type: str) -> dict:
        """Run a single bounded session for the given agent type.

        Acquires session and device locks as needed, instantiates the
        appropriate agent, and delegates to its ``run_session`` method.

        Returns a dict with session results or a status explaining why
        the session could not run.
        """
        if agent_type not in self.config:
            return {"status": "unknown_agent", "message": f"No config for '{agent_type}'"}

        agent_cfg = self.config[agent_type]

        # Non-blocking session lock — only one session per agent type at a time
        if not self._session_locks[agent_type].acquire(blocking=False):
            logger.info(f"[orchestrator] {agent_type} session already running, skipping")
            return {"status": "already_running", "agent_type": agent_type}

        db: Session | None = None
        device_acquired = False

        try:
            db = SessionLocal()

            # Acquire device lock if this agent needs a device
            if agent_cfg.get("requires_device", False):
                if not self._device_lock.acquire(blocking=False):
                    logger.info(f"[orchestrator] Device busy, cannot run {agent_type}")
                    return {"status": "device_busy", "agent_type": agent_type}
                device_acquired = True

            # Instantiate the right agent
            agent = self._create_agent(agent_type, db)
            if agent is None:
                return {"status": "not_implemented", "message": f"Agent '{agent_type}' not yet built"}

            logger.info(
                f"[orchestrator] Starting {agent_type} session "
                f"(max_items={agent_cfg['max_items_per_session']}, "
                f"max_duration={agent_cfg['max_session_duration_s']}s)"
            )

            result = agent.run_session(
                max_items=agent_cfg["max_items_per_session"],
                max_duration_s=agent_cfg["max_session_duration_s"],
            )
            result["agent_type"] = agent_type
            result["status"] = "completed"

            # v0.10.4 — post-session quality gate for synthesis agents.
            # ux_intel produces screenshots not observations; skip it.
            if agent_type in ("intel", "impact_analysis"):
                try:
                    from agent.quality_review_agent import QualityReviewAgent
                    # Review everything written in the session window + a small
                    # buffer. The agent skips observations it's already flagged.
                    since_minutes = max(
                        5,
                        int(agent_cfg["max_session_duration_s"] / 60) + 2,
                    )
                    qr = QualityReviewAgent(self.project_id, db)
                    qr_result = qr.review_recent(since_minutes=since_minutes)
                    result["quality_review"] = qr_result
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[orchestrator] quality_review raised for %s: %s",
                        agent_type, exc,
                    )

            return result

        except Exception as e:
            logger.error(f"[orchestrator] {agent_type} session failed: {e}", exc_info=True)
            return {"status": "error", "agent_type": agent_type, "message": str(e)}

        finally:
            if device_acquired:
                self._device_lock.release()
            self._session_locks[agent_type].release()
            if db is not None:
                db.close()

    # ------------------------------------------------------------------
    # Agent factory
    # ------------------------------------------------------------------

    def _create_agent(self, agent_type: str, db: Session):
        """Instantiate the agent for the given type.

        Returns the agent instance, or None if the module is not yet built.
        """
        if agent_type == "intel":
            try:
                from agent.intel_agent import IntelAgent
            except ImportError:
                logger.warning("[orchestrator] IntelAgent not available")
                return None
            return IntelAgent(self.project_id, db)

        if agent_type == "competitive_intel":
            try:
                from agent.competitive_intel_agent import CompetitiveIntelAgent
            except ImportError:
                logger.warning("[orchestrator] CompetitiveIntelAgent not available")
                return None
            return CompetitiveIntelAgent(self.project_id, db)

        if agent_type == "industry_research":
            try:
                from agent.industry_research_agent import IndustryResearchAgent
            except ImportError:
                logger.warning("[orchestrator] IndustryResearchAgent not available")
                return None
            return IndustryResearchAgent(self.project_id, db)

        if agent_type == "ux_intel":
            try:
                from agent.ux_intel_agent import UXIntelAgent
            except ImportError:
                logger.warning("[orchestrator] UXIntelAgent not available")
                return None
            # UX intel requires an Android device handle
            try:
                from tools.android_device import AndroidDevice
                device = AndroidDevice()
            except Exception:
                logger.warning("[orchestrator] No Android device available for UXIntelAgent")
                return None
            return UXIntelAgent(self.project_id, db, device)

        if agent_type == "impact_analysis":
            try:
                from agent.impact_analysis_agent import ImpactAnalysisAgent
            except ImportError:
                logger.warning("[orchestrator] ImpactAnalysisAgent not available")
                return None
            return ImpactAnalysisAgent(self.project_id, db)

        return None

    # ------------------------------------------------------------------
    # Daemon loop
    # ------------------------------------------------------------------

    def run_daemon(self, check_interval_s: int = 60) -> None:
        """Run the orchestrator as a continuous daemon loop.

        Checks each agent type on every tick and spawns a session thread
        when enough time has elapsed since the last session.
        """
        self._running = True
        logger.info(
            f"[orchestrator] Daemon started for project {self.project_id} "
            f"(check every {check_interval_s}s)"
        )

        while self._running:
            for agent_type in self.config:
                try:
                    db = SessionLocal()
                    should_run = self._should_run_agent(agent_type, db)
                    db.close()

                    if should_run:
                        logger.info(f"[orchestrator] Scheduling {agent_type} session")
                        t = threading.Thread(
                            target=self.run_agent_session,
                            args=(agent_type,),
                            name=f"agent-{agent_type}",
                            daemon=True,
                        )
                        t.start()
                except Exception as e:
                    logger.error(
                        f"[orchestrator] Error checking {agent_type}: {e}",
                        exc_info=True,
                    )

            time.sleep(check_interval_s)

        logger.info("[orchestrator] Daemon stopped")

    def start_daemon(self) -> None:
        """Start the daemon in a background thread."""
        if self._running:
            logger.warning("[orchestrator] Daemon already running")
            return

        self._daemon_thread = threading.Thread(
            target=self.run_daemon,
            name="orchestrator-daemon",
            daemon=True,
        )
        self._daemon_thread.start()
        logger.info("[orchestrator] Daemon thread started")

    def stop_daemon(self) -> None:
        """Signal the daemon loop to stop on its next tick."""
        self._running = False
        logger.info("[orchestrator] Stop signal sent")

    # ------------------------------------------------------------------
    # Status & reporting
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current orchestrator status with per-agent and knowledge summaries."""
        db = SessionLocal()
        try:
            agents_status: dict[str, Any] = {}

            for agent_type, cfg in self.config.items():
                last_session = (
                    db.query(AgentSession)
                    .filter(
                        AgentSession.project_id == self.project_id,
                        AgentSession.agent_type == agent_type,
                    )
                    .order_by(AgentSession.started_at.desc())
                    .first()
                )

                pending_count = (
                    db.query(func.count(WorkItem.id))
                    .filter(
                        WorkItem.project_id == self.project_id,
                        WorkItem.agent_type == agent_type,
                        WorkItem.status == "pending",
                    )
                    .scalar()
                ) or 0

                total_sessions = (
                    db.query(func.count(AgentSession.id))
                    .filter(
                        AgentSession.project_id == self.project_id,
                        AgentSession.agent_type == agent_type,
                    )
                    .scalar()
                ) or 0

                last_session_dict = None
                if last_session:
                    last_session_dict = {
                        "id": last_session.id,
                        "started_at": last_session.started_at.isoformat() if last_session.started_at else None,
                        "completed_at": last_session.completed_at.isoformat() if last_session.completed_at else None,
                        "items_completed": last_session.items_completed,
                        "items_failed": last_session.items_failed,
                        "summary": last_session.session_summary,
                    }

                agents_status[agent_type] = {
                    "last_session": last_session_dict,
                    "pending_work_items": pending_count,
                    "total_sessions": total_sessions,
                    "config": cfg,
                }

            # Knowledge summary
            total_entities = (
                db.query(func.count(KnowledgeEntity.id))
                .filter(KnowledgeEntity.project_id == self.project_id)
                .scalar()
            ) or 0

            total_observations = (
                db.query(func.count(KnowledgeObservation.id))
                .join(KnowledgeEntity)
                .filter(KnowledgeEntity.project_id == self.project_id)
                .scalar()
            ) or 0

            total_artifacts = (
                db.query(func.count(KnowledgeArtifact.id))
                .filter(KnowledgeArtifact.project_id == self.project_id)
                .scalar()
            ) or 0

            total_screenshots = (
                db.query(func.count(KnowledgeScreenshot.id))
                .join(KnowledgeEntity)
                .filter(KnowledgeEntity.project_id == self.project_id)
                .scalar()
            ) or 0

            return {
                "is_running": self._running,
                "project_id": self.project_id,
                "agents": agents_status,
                "knowledge": {
                    "total_entities": total_entities,
                    "total_observations": total_observations,
                    "total_artifacts": total_artifacts,
                    "total_screenshots": total_screenshots,
                },
            }
        finally:
            db.close()

    def generate_daily_digest(self, telegram_chat_id: int | None = None) -> str:
        """Generate a daily summary of what agents have learned.

        Synthesizes the last 24 hours of observations, entities, and
        artifacts into an actionable digest using Claude.

        Args:
            telegram_chat_id: Optional — included in context but sending
                is handled by the caller.

        Returns:
            A human-readable summary string.
        """
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(hours=24)

            # Recent observations
            recent_observations = (
                db.query(KnowledgeObservation)
                .join(KnowledgeEntity)
                .filter(
                    KnowledgeEntity.project_id == self.project_id,
                    KnowledgeObservation.observed_at >= cutoff,
                )
                .all()
            )

            # New entities
            new_entities = (
                db.query(KnowledgeEntity)
                .filter(
                    KnowledgeEntity.project_id == self.project_id,
                    KnowledgeEntity.created_at >= cutoff,
                )
                .all()
            )

            # New artifacts
            new_artifacts = (
                db.query(KnowledgeArtifact)
                .filter(
                    KnowledgeArtifact.project_id == self.project_id,
                    KnowledgeArtifact.created_at >= cutoff,
                )
                .all()
            )

            if not recent_observations and not new_entities and not new_artifacts:
                return "No new intelligence gathered in the last 24 hours."

            # Build context for Claude synthesis
            obs_lines = [
                f"- [{o.observation_type}] {o.content[:200]}"
                for o in recent_observations[:30]
            ]
            entity_lines = [
                f"- [{e.entity_type}] {e.name}"
                for e in new_entities[:20]
            ]
            artifact_lines = [
                f"- [{a.artifact_type}] {a.title}"
                for a in new_artifacts[:10]
            ]

            prompt = (
                "You are a Product Intelligence assistant. Summarise the last 24 hours "
                "of autonomous agent activity into a brief, actionable digest (3-8 bullet "
                "points). Focus on what is new, surprising, or requires PM attention.\n\n"
                f"**New entities ({len(new_entities)}):**\n"
                + ("\n".join(entity_lines) or "None")
                + f"\n\n**Observations ({len(recent_observations)}):**\n"
                + ("\n".join(obs_lines) or "None")
                + f"\n\n**Artifacts ({len(new_artifacts)}):**\n"
                + ("\n".join(artifact_lines) or "None")
                + "\n\nWrite the digest now."
            )

            from utils.claude_client import ask

            summary = ask(prompt, max_tokens=1024)
            return summary

        finally:
            db.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_run_agent(self, agent_type: str, db: Session) -> bool:
        """Check whether enough time has elapsed to run another session."""
        cfg = self.config.get(agent_type)
        if cfg is None:
            return False

        interval_hours = cfg.get("interval_hours", 6)

        last_session = (
            db.query(AgentSession)
            .filter(
                AgentSession.project_id == self.project_id,
                AgentSession.agent_type == agent_type,
            )
            .order_by(AgentSession.started_at.desc())
            .first()
        )

        if last_session is None:
            return True

        # Still running (no completed_at yet)
        if last_session.completed_at is None:
            return False

        elapsed = datetime.utcnow() - last_session.completed_at
        return elapsed > timedelta(hours=interval_hours)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_orchestrators: dict[int, ProductOSOrchestrator] = {}


def get_orchestrator(project_id: int) -> ProductOSOrchestrator:
    """Return (or create) an orchestrator for the given project.

    Each project gets its own orchestrator so multiple projects
    can run agents in parallel without interfering.
    """
    if project_id not in _orchestrators:
        _orchestrators[project_id] = ProductOSOrchestrator(project_id)
    return _orchestrators[project_id]
