"""Base class for autonomous Product OS agents.

Autonomous agents differ from session agents (like FlowExplorerAgent):
- They persist knowledge across sessions via KnowledgeStore
- They maintain a work queue and self-direct what to investigate next
- They run in bounded sessions (max items, max duration)
- They use Claude's tool-use loop for execution, matching the existing pattern
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agent.knowledge_store import KnowledgeStore
from utils.claude_client import ask, ask_with_tools
from webapp.api.models import AgentSession, WorkItem

logger = logging.getLogger(__name__)


class AutonomousAgent(ABC):
    """Base class for all Product OS autonomous agents."""

    def __init__(self, agent_type: str, project_id: int, db: Session, device=None):
        self.agent_type = agent_type
        self.project_id = project_id
        self.db = db
        self.device = device  # AndroidDevice, only for device-using agents
        self.knowledge = KnowledgeStore(db, agent_type, project_id)
        self._session_start: float | None = None
        self._token_usage = {"input_tokens": 0, "output_tokens": 0}

    # ------------------------------------------------------------------
    # Abstract methods — subclasses must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def seed_backlog(self) -> list[dict]:
        """Return initial work items as dicts.

        Each dict has keys: priority (int 1-10), category (str),
        description (str), context_json (dict|None).
        """
        ...

    @abstractmethod
    def generate_next_work(self) -> list[dict]:
        """Use Claude to reason about knowledge gaps and return new work items.

        Same dict format as seed_backlog().
        """
        ...

    @abstractmethod
    def execute_work_item(self, item: WorkItem) -> dict:
        """Execute one work item.

        Returns a result dict with keys: status (str), summary (str),
        entities_created (int), observations_added (int).
        """
        ...

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """Return Anthropic-format tool schemas for the tool-use loop."""
        ...

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for the tool-use loop."""
        ...

    @abstractmethod
    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch a tool call and return the result string."""
        ...

    # ------------------------------------------------------------------
    # Concrete methods
    # ------------------------------------------------------------------

    def run_session(self, max_items: int = 5, max_duration_s: int = 600) -> dict:
        """Run a bounded agent session.

        This is the heartbeat. It picks pending work items, executes them,
        and updates session tracking. Stops when max_items or max_duration_s
        is reached.
        """
        self._session_start = time.monotonic()
        self._token_usage = {"input_tokens": 0, "output_tokens": 0}

        # Create session record
        session = AgentSession(
            project_id=self.project_id,
            agent_type=self.agent_type,
            started_at=datetime.utcnow(),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)

        items_completed = 0
        items_failed = 0
        knowledge_added = 0

        # Get pending work items
        pending = (
            self.db.query(WorkItem)
            .filter(
                WorkItem.agent_type == self.agent_type,
                WorkItem.project_id == self.project_id,
                WorkItem.status == "pending",
            )
            .order_by(WorkItem.priority.desc(), WorkItem.created_at.asc())
            .all()
        )

        if not pending:
            # Check if ANY items exist for this agent
            any_items = (
                self.db.query(WorkItem)
                .filter(
                    WorkItem.agent_type == self.agent_type,
                    WorkItem.project_id == self.project_id,
                )
                .first()
            )

            if any_items is None:
                # No items at all — seed the backlog
                logger.info(f"[{self.agent_type}] No work items found, seeding backlog")
                new_items = self.seed_backlog()
                self._create_work_items(new_items)
            else:
                # Items exist but all completed — generate new work
                logger.info(f"[{self.agent_type}] All items done, generating new work")
                new_items = self.generate_next_work()
                self._create_work_items(new_items)

            # Re-fetch pending items
            pending = (
                self.db.query(WorkItem)
                .filter(
                    WorkItem.agent_type == self.agent_type,
                    WorkItem.project_id == self.project_id,
                    WorkItem.status == "pending",
                )
                .order_by(WorkItem.priority.desc(), WorkItem.created_at.asc())
                .all()
            )

        # Execute work items
        for item in pending:
            # Check bounds
            if items_completed + items_failed >= max_items:
                logger.info(f"[{self.agent_type}] Reached max_items={max_items}, stopping")
                break
            elapsed = time.monotonic() - self._session_start
            if elapsed > max_duration_s:
                logger.info(
                    f"[{self.agent_type}] Reached max_duration_s={max_duration_s} "
                    f"({elapsed:.0f}s elapsed), stopping"
                )
                break

            # Mark in progress
            item.status = "in_progress"
            item.started_at = datetime.utcnow()
            self.db.commit()

            logger.info(
                f"[{self.agent_type}] Executing work item {item.id}: "
                f"{item.category} — {item.description[:80]}"
            )

            try:
                result = self.execute_work_item(item)
                item.status = result.get("status", "completed")
                item.result_summary = result.get("summary", "")
                item.completed_at = datetime.utcnow()
                items_completed += 1
                knowledge_added += result.get("entities_created", 0)
                knowledge_added += result.get("observations_added", 0)
            except Exception as e:
                logger.error(
                    f"[{self.agent_type}] Work item {item.id} failed: {e}",
                    exc_info=True,
                )
                item.status = "failed"
                item.result_summary = str(e)[:500]
                item.completed_at = datetime.utcnow()
                items_failed += 1

            self.db.commit()

        # Finalize session
        elapsed_total = time.monotonic() - self._session_start
        summary = (
            f"Completed {items_completed} items, {items_failed} failed, "
            f"{knowledge_added} knowledge entries added in {elapsed_total:.0f}s"
        )

        session.completed_at = datetime.utcnow()
        session.items_completed = items_completed
        session.items_failed = items_failed
        session.knowledge_added = knowledge_added
        session.token_usage_json = self._token_usage
        session.session_summary = summary
        self.db.commit()

        logger.info(f"[{self.agent_type}] Session {session.id} complete: {summary}")

        return {
            "session_id": session.id,
            "items_completed": items_completed,
            "items_failed": items_failed,
            "knowledge_added": knowledge_added,
            "token_usage": self._token_usage,
            "elapsed_s": round(elapsed_total, 1),
            "summary": summary,
        }

    def run_tool_loop(self, prompt: str, max_iterations: int = 20) -> dict:
        """Run a Claude tool-use loop, matching the FlowExplorerAgent pattern.

        Sends the prompt to Claude with available tools, processes tool calls
        iteratively until Claude ends the turn or max_iterations is reached.

        Returns:
            Dict with status, final_response, and iterations count.
        """
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tools = self.get_tools()
        system = self.get_system_prompt()
        iteration = 0
        final_text = ""

        while iteration < max_iterations:
            iteration += 1

            response = ask_with_tools(
                messages=messages,
                tools=tools,
                system=system,
                max_tokens=4096,
            )

            # Track token usage
            if hasattr(response, "usage") and response.usage is not None:
                self._token_usage["input_tokens"] += getattr(
                    response.usage, "input_tokens", 0
                )
                self._token_usage["output_tokens"] += getattr(
                    response.usage, "output_tokens", 0
                )

            # Append assistant message
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Extract final text from content blocks
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                break

            if response.stop_reason != "tool_use":
                logger.warning(
                    f"[{self.agent_type}] Unexpected stop_reason: {response.stop_reason}"
                )
                # Still try to extract text
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                break

            # Process tool calls
            tool_results: list[dict] = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.debug(
                        f"[{self.agent_type}] Tool call: {block.name} | "
                        f"Input: {json.dumps(block.input)[:200]}"
                    )
                    try:
                        result_text = self.execute_tool(block.name, block.input)
                    except Exception as e:
                        logger.error(
                            f"[{self.agent_type}] Tool '{block.name}' error: {e}",
                            exc_info=True,
                        )
                        result_text = f"ERROR: {e}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result_text),
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        status = "completed" if iteration < max_iterations else "max_iterations"
        if iteration >= max_iterations:
            logger.warning(
                f"[{self.agent_type}] Tool loop hit max_iterations={max_iterations}"
            )

        return {
            "status": status,
            "final_response": final_text,
            "iterations": iteration,
        }

    def _create_work_items(self, items: list[dict]) -> list[int]:
        """Create WorkItem rows from dicts returned by seed_backlog/generate_next_work.

        Args:
            items: List of dicts with keys: priority, category, description, context_json.

        Returns:
            List of created WorkItem ids.
        """
        ids: list[int] = []
        for item_dict in items:
            work_item = WorkItem(
                project_id=self.project_id,
                agent_type=self.agent_type,
                priority=item_dict.get("priority", 5),
                category=item_dict.get("category", "general"),
                description=item_dict.get("description", ""),
                context_json=item_dict.get("context_json"),
                status="pending",
            )
            self.db.add(work_item)
            self.db.flush()  # get the id without committing yet
            ids.append(work_item.id)

        self.db.commit()
        logger.info(
            f"[{self.agent_type}] Created {len(ids)} work items: {ids}"
        )
        return ids
