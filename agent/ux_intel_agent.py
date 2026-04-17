"""UX Intelligence Agent — deep maps app flows and curates user journeys.

Responsibilities:
- Map ALL UX flows in the user's app in extreme depth
- Map equivalent flows in competitor apps
- Curate named journeys (e.g., "hotel booking flow", "special requests")
- Generate cross-competitor UX comparisons
- Track flow changes across app versions
- Suggest UX improvements based on competitive analysis
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING

from sqlalchemy.orm import Session

from agent.base_autonomous_agent import AutonomousAgent
from agent.knowledge_store import KnowledgeStore
from tools.web_research import WebResearcher
from utils.claude_client import ask, ask_vision
from webapp.api.models import KnowledgeEntity, Project, WorkItem

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)

# Evidence directory for flow screenshots
_EVIDENCE_DIR = Path(__file__).resolve().parent.parent / ".tmp" / "ux_intel"
_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)


class UXIntelAgent(AutonomousAgent):
    """Autonomous agent that deep-maps app flows and curates user journeys."""

    def __init__(self, project_id: int, db: Session, device=None):
        super().__init__("ux_intel", project_id, db, device)

        if self.device is None:
            raise ValueError(
                "UXIntelAgent requires an Android device. Pass device= to constructor."
            )

        self.web = WebResearcher()

        # Load project info
        project = self.db.query(Project).filter(Project.id == project_id).first()
        self.project_name = project.name if project else "Unknown Project"
        self.project_description = project.description or "" if project else ""
        self.app_package = project.app_package or "" if project else ""

        # Flow session tracking
        self._flow_session_id: str | None = None
        self._step_counter: int = 0

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def seed_backlog(self) -> list[dict]:
        """Return initial work items for UX flow mapping."""
        return [
            {
                "priority": 10,
                "category": "app_overview",
                "description": (
                    "Map the app's home screen, identify all major feature "
                    "areas and navigation entry points"
                ),
                "context_json": {
                    "project_name": self.project_name,
                    "app_package": self.app_package,
                },
            },
            {
                "priority": 8,
                "category": "feature_mapping",
                "description": (
                    "Identify and list all distinct feature areas in the app "
                    "that need deep flow mapping"
                ),
                "context_json": None,
            },
        ]

    def generate_next_work(self) -> list[dict]:
        """Use Claude to reason about what flows haven't been mapped yet."""
        summary = self.knowledge.get_knowledge_summary()

        # Gather known flows
        flows = self.knowledge.find_entities(entity_type="flow")
        flow_info = []
        for f in flows:
            screenshots = self.knowledge.find_screenshots(entity_id=f["id"])
            flow_info.append({
                "name": f["name"],
                "screenshot_count": len(screenshots),
                "id": f["id"],
            })

        # Look for competitor apps discovered by competitive intel agent
        competitor_apps = self.knowledge.find_entities(entity_type="app")
        competitor_names = [a["name"] for a in competitor_apps]

        # Gather completed work
        completed = (
            self.db.query(WorkItem)
            .filter(
                WorkItem.agent_type == self.agent_type,
                WorkItem.project_id == self.project_id,
                WorkItem.status == "completed",
            )
            .order_by(WorkItem.completed_at.desc())
            .limit(20)
            .all()
        )
        completed_descriptions = [
            f"[{w.category}] {w.description} -> {w.result_summary or 'done'}"
            for w in completed
        ]

        prompt = f"""You are a UX intelligence analyst planning next flow mapping work.

Project: {self.project_name}
Description: {self.project_description}
App package: {self.app_package}

Current knowledge state:
{json.dumps(summary, indent=2, default=str)}

Known flows (with screenshot counts):
{json.dumps(flow_info, indent=2, default=str)}

Competitor apps discovered: {json.dumps(competitor_names)}

Completed work items:
{chr(10).join(completed_descriptions) or "(none yet)"}

Based on this state, suggest 2-4 high-value next work items. Consider:
- Feature areas that haven't been mapped yet
- Flows that need deeper exploration (few screenshots)
- Competitor flows that should be mapped for comparison
- Named user journeys that can be curated from existing data
- Flows that should be re-mapped to detect changes
- UX analysis and improvement suggestions

Return a JSON array of work items. Each item must have:
- "priority": int 1-10 (higher = more important)
- "category": one of "feature_mapping", "competitor_flow", "journey_curation", "flow_comparison", "change_detection", "ux_analysis"
- "description": what to map or analyze
- "context_json": optional dict with feature_name, competitor_name, flow_name, etc.

Return ONLY the JSON array, no other text."""

        try:
            response = ask(prompt, max_tokens=2048)
            text = response.strip()
            # Handle markdown code blocks
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            items = json.loads(text)
            if isinstance(items, list) and len(items) > 0:
                return items
        except (json.JSONDecodeError, IndexError, Exception) as exc:
            logger.warning("Failed to parse Claude's work suggestions: %s", exc)

        # Fallback: generate sensible defaults
        fallback: list[dict] = []
        for f in flow_info[:3]:
            if f["screenshot_count"] < 5:
                fallback.append({
                    "priority": 7,
                    "category": "feature_mapping",
                    "description": f"Deep-map the '{f['name']}' flow with more detail",
                    "context_json": {"flow_name": f["name"]},
                })
        for comp in competitor_names[:2]:
            fallback.append({
                "priority": 6,
                "category": "competitor_flow",
                "description": f"Map equivalent flows in {comp}",
                "context_json": {"competitor_name": comp},
            })
        if not fallback:
            fallback.append({
                "priority": 8,
                "category": "feature_mapping",
                "description": "Explore and map unmapped feature areas in the app",
                "context_json": None,
            })
        return fallback

    def get_tools(self) -> list[dict]:
        """Return Anthropic-format tool schemas for the tool-use loop."""
        return [
            {
                "name": "take_screenshot",
                "description": "Capture a screenshot of the current screen.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": (
                                "Short descriptive label for this screen "
                                "(e.g. 'hotel_search_results')"
                            ),
                        },
                    },
                    "required": ["label"],
                },
            },
            {
                "name": "get_ui_elements",
                "description": (
                    "Get the UI hierarchy XML of the current screen to understand "
                    "what elements are interactive."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "tap_element",
                "description": (
                    "Tap an element by its visible text label, or by normalized "
                    "0-1 screen coordinates if text is unavailable."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "element_text": {
                            "type": "string",
                            "description": "Visible text of the element to tap (preferred).",
                        },
                        "x": {
                            "type": "number",
                            "description": "Normalized X coordinate (0-1) to tap.",
                        },
                        "y": {
                            "type": "number",
                            "description": "Normalized Y coordinate (0-1) to tap.",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "swipe_screen",
                "description": "Swipe the screen in a direction to reveal more content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                            "description": "Direction to swipe.",
                        },
                    },
                    "required": ["direction"],
                },
            },
            {
                "name": "press_back",
                "description": "Press the Android back button to navigate to the previous screen.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "type_text",
                "description": "Type text into the currently focused input field.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The text to type.",
                        },
                    },
                    "required": ["text"],
                },
            },
            {
                "name": "save_flow_step",
                "description": (
                    "Save the current screen as a numbered step in the active flow "
                    "session with metadata."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Descriptive label for this step.",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Optional notes about this step.",
                        },
                    },
                    "required": ["label"],
                },
            },
            {
                "name": "start_flow_session",
                "description": (
                    "Start a new flow mapping session. All subsequent screenshots "
                    "and steps will be grouped under this session."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "flow_name": {
                            "type": "string",
                            "description": "Name for this flow (e.g. 'hotel_booking_flow').",
                        },
                        "app_package": {
                            "type": "string",
                            "description": (
                                "App package to map (defaults to project app). "
                                "Use for competitor flows."
                            ),
                        },
                    },
                    "required": ["flow_name"],
                },
            },
            {
                "name": "end_flow_session",
                "description": (
                    "End the current flow mapping session and save the journey "
                    "with a summary."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Summary of the flow that was mapped.",
                        },
                    },
                    "required": ["summary"],
                },
            },
            {
                "name": "query_knowledge",
                "description": (
                    "Query existing flows and knowledge to check what has "
                    "already been mapped."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                        "entity_type": {
                            "type": "string",
                            "description": "Optional entity type filter (e.g. flow, app).",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "finish_work",
                "description": (
                    "Signal that the current work item is complete. "
                    "Call this when you have finished mapping the current task."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Summary of what was accomplished.",
                        },
                        "entities_created": {
                            "type": "integer",
                            "description": "Number of new entities created.",
                        },
                        "observations_added": {
                            "type": "integer",
                            "description": "Number of observations added.",
                        },
                    },
                    "required": ["summary", "entities_created", "observations_added"],
                },
            },
        ]

    def get_system_prompt(self) -> str:
        """Return the system prompt for the Claude tool-use loop."""
        summary = self.knowledge.get_knowledge_summary()
        return (
            f'You are a UX Intelligence Analyst agent. Your job is to '
            f'systematically map and analyze app user interfaces for '
            f'"{self.project_name}".\n\n'
            f'Project description: {self.project_description}\n\n'
            f'You control an Android device. Navigate the app methodically:\n\n'
            f'1. Start each exploration with start_flow_session to group screenshots\n'
            f'2. Take screenshots at every significant screen state\n'
            f'3. Use save_flow_step to document each step with descriptive labels\n'
            f'4. Tap through every interactive element, exploring all branches\n'
            f'5. Use get_ui_elements to understand what\'s clickable before tapping\n'
            f'6. After fully exploring a flow, use end_flow_session with a summary\n'
            f'7. Use query_knowledge to check what flows have already been mapped\n\n'
            f'Navigation guidelines:\n'
            f'- Use tap_element with element_text (preferred) or normalized 0-1 coordinates\n'
            f'- Navigate systematically: don\'t skip screens, document everything\n'
            f'- Press back to return to previous screens and explore alternate paths\n'
            f'- If stuck, try swipe_screen or press_back\n\n'
            f'Current knowledge state:\n'
            f'{json.dumps(summary, indent=2, default=str)}\n\n'
            f'Work systematically. Document every screen. '
            f'Call finish_work when the current task is complete.'
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch tool calls to their implementations."""
        try:
            if tool_name == "take_screenshot":
                return self._tool_take_screenshot(tool_input)

            elif tool_name == "get_ui_elements":
                return self._tool_get_ui_elements(tool_input)

            elif tool_name == "tap_element":
                return self._tool_tap_element(tool_input)

            elif tool_name == "swipe_screen":
                return self._tool_swipe_screen(tool_input)

            elif tool_name == "press_back":
                return self._tool_press_back(tool_input)

            elif tool_name == "type_text":
                return self._tool_type_text(tool_input)

            elif tool_name == "save_flow_step":
                return self._tool_save_flow_step(tool_input)

            elif tool_name == "start_flow_session":
                return self._tool_start_flow_session(tool_input)

            elif tool_name == "end_flow_session":
                return self._tool_end_flow_session(tool_input)

            elif tool_name == "query_knowledge":
                return self._tool_query_knowledge(tool_input)

            elif tool_name == "finish_work":
                return self._tool_finish_work(tool_input)

            else:
                return f"ERROR: Unknown tool '{tool_name}'"

        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
            return f"ERROR: {exc}"

    def execute_work_item(self, item: WorkItem) -> dict:
        """Execute a work item by building a targeted prompt and running the tool loop."""
        self._current_result: dict = {
            "status": "completed",
            "summary": "Work item processed",
            "entities_created": 0,
            "observations_added": 0,
        }

        # Build a category-specific prompt
        context = item.context_json or {}
        prompt = self._build_work_prompt(item.category, item.description, context)

        logger.info(
            "[%s] Running tool loop for %s: %s",
            self.agent_type,
            item.category,
            item.description[:80],
        )

        self.run_tool_loop(prompt, max_iterations=20)

        return self._current_result

    # ------------------------------------------------------------------
    # Tool implementations (private)
    # ------------------------------------------------------------------

    def _tool_take_screenshot(self, inp: dict) -> str:
        """Capture the current screen and describe it with vision."""
        label = inp.get("label", "screen")
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        session_prefix = self._flow_session_id or "misc"
        filename = f"{session_prefix}_{label}_{timestamp}.png"
        filepath = _EVIDENCE_DIR / filename

        try:
            screenshot_bytes = self.device.screenshot(save_path=str(filepath))
        except Exception as exc:
            return f"ERROR: Failed to take screenshot — {exc}"

        # Compute visual hash
        visual_hash = hashlib.md5(screenshot_bytes).hexdigest()

        # Use vision to describe the screen
        try:
            description = ask_vision(
                image_bytes=screenshot_bytes,
                prompt=(
                    "Briefly describe what is shown on this mobile app screen. "
                    "List the key UI elements, buttons, and content visible."
                ),
                max_tokens=300,
            )
        except Exception as exc:
            logger.warning("Vision description failed: %s", exc)
            description = f"Screenshot captured (vision unavailable: {exc})"

        return json.dumps({
            "description": description,
            "file_path": str(filepath),
            "visual_hash": visual_hash,
            "label": label,
        })

    def _tool_get_ui_elements(self, inp: dict) -> str:
        """Get the UI hierarchy XML, truncated to fit context."""
        try:
            hierarchy = self.device.get_ui_tree()
        except Exception as exc:
            return f"ERROR: Failed to get UI hierarchy — {exc}"

        # Truncate to 3000 chars to avoid overwhelming context
        if len(hierarchy) > 3000:
            hierarchy = hierarchy[:3000] + "\n... [truncated]"
        return hierarchy

    def _tool_tap_element(self, inp: dict) -> str:
        """Tap an element by text or normalized coordinates."""
        element_text = inp.get("element_text")
        x = inp.get("x")
        y = inp.get("y")

        try:
            if element_text:
                success = self.device.tap_text(element_text)
                if success:
                    return f"Tapped element with text '{element_text}'"
                return f"Element with text '{element_text}' not found on screen"

            if x is not None and y is not None:
                # Convert normalized 0-1 coords to pixel coords
                w, h = self.device.get_screen_size()
                px = int(x * w)
                py = int(y * h)
                self.device.tap(px, py)
                return f"Tapped at normalized ({x:.2f}, {y:.2f}) → pixel ({px}, {py})"

            return "ERROR: Provide either element_text or both x and y coordinates"

        except Exception as exc:
            return f"ERROR: Tap failed — {exc}"

    def _tool_swipe_screen(self, inp: dict) -> str:
        """Swipe in a direction."""
        direction = inp.get("direction", "up")
        try:
            self.device.swipe(direction)
            return f"Swiped {direction}"
        except Exception as exc:
            return f"ERROR: Swipe failed — {exc}"

    def _tool_press_back(self, inp: dict) -> str:
        """Press the Android back button."""
        try:
            self.device.press_back()
            return "Pressed back"
        except Exception as exc:
            return f"ERROR: Press back failed — {exc}"

    def _tool_type_text(self, inp: dict) -> str:
        """Type text into the focused field."""
        text = inp.get("text", "")
        try:
            self.device.type_text(text)
            return f"Typed: '{text}'"
        except Exception as exc:
            return f"ERROR: Type text failed — {exc}"

    def _tool_save_flow_step(self, inp: dict) -> str:
        """Save the current screen as a numbered flow step."""
        label = inp.get("label", "step")
        notes = inp.get("notes", "")

        if not self._flow_session_id:
            return (
                "ERROR: No active flow session. "
                "Call start_flow_session first."
            )

        # Take screenshot
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = (
            f"{self._flow_session_id}_step{self._step_counter:03d}"
            f"_{label}_{timestamp}.png"
        )
        filepath = _EVIDENCE_DIR / filename

        try:
            screenshot_bytes = self.device.screenshot(save_path=str(filepath))
        except Exception as exc:
            return f"ERROR: Failed to capture flow step — {exc}"

        visual_hash = hashlib.md5(screenshot_bytes).hexdigest()

        # Find the flow entity for this session
        flows = self.knowledge.find_entities(entity_type="flow")
        flow_entity_id = None
        for f in flows:
            meta = f.get("metadata") or {}
            if meta.get("session_id") == self._flow_session_id:
                flow_entity_id = f["id"]
                break

        # Save screenshot to knowledge store
        self.knowledge.save_screenshot(
            file_path=str(filepath),
            entity_id=flow_entity_id,
            label=label,
            app_package=self.app_package or None,
            visual_hash=visual_hash,
            flow_session_id=self._flow_session_id,
            sequence_order=self._step_counter,
        )

        step_num = self._step_counter
        self._step_counter += 1

        result = {
            "status": "saved",
            "step_number": step_num,
            "label": label,
            "file_path": str(filepath),
            "visual_hash": visual_hash,
        }
        if notes:
            result["notes"] = notes

        return json.dumps(result)

    def _tool_start_flow_session(self, inp: dict) -> str:
        """Start a new flow mapping session."""
        flow_name = inp.get("flow_name", "unnamed_flow")
        app_package = inp.get("app_package") or self.app_package

        self._flow_session_id = str(uuid.uuid4())[:8]
        self._step_counter = 0

        # Create a flow entity in the knowledge graph
        flow_entity_id = self.knowledge.upsert_entity(
            entity_type="flow",
            name=flow_name,
            description=f"UX flow: {flow_name}",
            metadata={
                "session_id": self._flow_session_id,
                "app_package": app_package,
                "started_at": datetime.utcnow().isoformat(),
            },
        )

        self._current_result["entities_created"] = (
            self._current_result.get("entities_created", 0) + 1
        )

        return json.dumps({
            "status": "started",
            "session_id": self._flow_session_id,
            "flow_name": flow_name,
            "flow_entity_id": flow_entity_id,
            "app_package": app_package,
        })

    def _tool_end_flow_session(self, inp: dict) -> str:
        """End the current flow session and save a summary observation."""
        summary = inp.get("summary", "Flow session ended")

        if not self._flow_session_id:
            return "ERROR: No active flow session to end."

        # Find the flow entity for this session
        flows = self.knowledge.find_entities(entity_type="flow")
        flow_entity_id = None
        for f in flows:
            meta = f.get("metadata") or {}
            if meta.get("session_id") == self._flow_session_id:
                flow_entity_id = f["id"]
                break

        if flow_entity_id:
            # Count screenshots in this session
            screenshots = self.knowledge.find_screenshots(
                flow_session_id=self._flow_session_id,
            )
            obs_content = (
                f"Flow mapping complete. {len(screenshots)} screenshots captured. "
                f"Summary: {summary}"
            )
            self.knowledge.add_observation(
                entity_id=flow_entity_id,
                obs_type="general",
                content=obs_content,
            )
            self._current_result["observations_added"] = (
                self._current_result.get("observations_added", 0) + 1
            )

        session_id = self._flow_session_id
        steps = self._step_counter
        self._flow_session_id = None
        self._step_counter = 0

        return json.dumps({
            "status": "ended",
            "session_id": session_id,
            "total_steps": steps,
            "summary": summary,
        })

    def _tool_query_knowledge(self, inp: dict) -> str:
        """Query the knowledge graph for existing information."""
        results: dict[str, Any] = {}

        # Search entities
        entities = self.knowledge.find_entities(
            entity_type=inp.get("entity_type"),
            name_like=inp["query"],
        )
        results["entities"] = entities

        # Semantic search for related observations
        semantic = self.knowledge.semantic_search(inp["query"], top_k=5)
        results["semantic_matches"] = semantic

        # If we found entities, include their recent observations
        if entities:
            for ent in entities[:3]:
                obs = self.knowledge.get_observations(ent["id"], limit=5)
                ent["recent_observations"] = obs

        return json.dumps(results, default=str)

    def _tool_finish_work(self, inp: dict) -> str:
        """Mark the current work item as complete."""
        self._current_result = {
            "status": "completed",
            "summary": inp["summary"],
            "entities_created": inp.get("entities_created", 0),
            "observations_added": inp.get("observations_added", 0),
        }
        return json.dumps({
            "status": "completed",
            "summary": inp["summary"],
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_work_prompt(
        self, category: str, description: str, context: dict
    ) -> str:
        """Build a targeted prompt based on work item category."""
        if category == "app_overview":
            return (
                f"Navigate to the home screen of {self.project_name} "
                f"(package: {self.app_package}). Start a flow session called "
                f"'app_overview'. Take a screenshot of the home screen, then "
                f"use get_ui_elements to identify all major navigation entry "
                f"points and feature areas. Document each section with "
                f"save_flow_step. End the flow session with a summary listing "
                f"all discovered feature areas."
            )

        elif category == "feature_mapping":
            feature_name = context.get("feature_name") or description
            return (
                f"Deep-map the following feature area in {self.project_name}: "
                f"{feature_name}. Start a flow session named after the feature. "
                f"Navigate to the feature, then systematically explore every "
                f"screen, dialog, and branch within it. Document each screen "
                f"with save_flow_step. Tap every interactive element to discover "
                f"all reachable states. Press back to explore alternate paths. "
                f"End the session with a comprehensive summary."
            )

        elif category == "competitor_flow":
            competitor_name = context.get("competitor_name", "competitor")
            flow_name = context.get("flow_name", "")
            return (
                f"Map the UX flow for {competitor_name}'s app. "
                f"{'Focus on: ' + flow_name + '. ' if flow_name else ''}"
                f"Start a flow session with the competitor name. Navigate "
                f"their app and document every screen in the equivalent flow. "
                f"Pay attention to differences from {self.project_name}: "
                f"extra features, missing features, UX patterns, and friction "
                f"points. End with a detailed summary of the flow."
            )

        elif category == "journey_curation":
            journey_name = context.get("journey_name", description)
            return (
                f"Curate a named user journey: '{journey_name}'. "
                f"Use query_knowledge to find all existing flow data and "
                f"screenshots related to this journey. Synthesize them into "
                f"a coherent narrative. If any gaps exist in the flow, navigate "
                f"the app to fill them. Save the final journey as a flow entity "
                f"with a comprehensive summary."
            )

        elif category == "flow_comparison":
            flow_name = context.get("flow_name", description)
            return (
                f"Compare the '{flow_name}' flow across {self.project_name} "
                f"and its competitors. Use query_knowledge to retrieve existing "
                f"flow data. For any app not yet mapped, navigate and document "
                f"the equivalent flow. Focus on: number of steps, friction "
                f"points, unique features, and UX patterns. Save comparison "
                f"observations for each entity."
            )

        elif category == "change_detection":
            flow_name = context.get("flow_name", description)
            return (
                f"Re-map the '{flow_name}' flow to check for changes since "
                f"last mapping. Start a new flow session. Navigate through the "
                f"flow and compare each screen against previously captured "
                f"screenshots (use query_knowledge to find prior data). "
                f"Document any changes: new screens, removed screens, "
                f"UI modifications, or flow changes. End with a summary "
                f"listing all detected changes."
            )

        elif category == "ux_analysis":
            subject = context.get("subject", description)
            return (
                f"Analyze UX quality for: {subject}. Use query_knowledge to "
                f"retrieve all existing flow data and screenshots. Navigate "
                f"the app if you need to verify any observations. Evaluate: "
                f"navigation clarity, visual consistency, interaction patterns, "
                f"loading states, error handling, and accessibility. Save "
                f"observations with specific improvement suggestions."
            )

        else:
            # Default: use description directly
            return description
