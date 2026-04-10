"""
agent/flow_explorer_agent.py — Screen flow exploration

Explores an app feature area autonomously using Claude + Android device tools.
Builds a screen state graph for use by scenario generation.

This agent is single-session (one device connection, one app context).
It uses a tool-use loop with Claude to navigate and map screens.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from utils.claude_client import ask_with_tools
from utils.config import get

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format) for Claude's tool-use loop
# ---------------------------------------------------------------------------
_TOOLS: list[dict] = [
    {
        "name": "take_screenshot",
        "description": (
            "Take a screenshot of the current screen and get the current screen state. "
            "Returns the file path of the saved screenshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short descriptive label for this screen (e.g. 'hotel_list')",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ui_elements",
        "description": (
            "Get all interactive elements on the current screen as an XML UI hierarchy. "
            "Use this to understand what can be tapped, scrolled, or typed into."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "tap_element",
        "description": "Tap an element by its visible text label, or by screen coordinates if text is unavailable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_text": {
                    "type": "string",
                    "description": "Visible text of the element to tap (preferred over coordinates)",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate to tap (use when element_text is unavailable)",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate to tap (use when element_text is unavailable)",
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
                    "description": "Direction to swipe",
                }
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
        "name": "finish_exploration",
        "description": (
            "Mark the exploration as complete and return the screen graph. "
            "Call this when you have mapped all reachable screens in the feature, "
            "or when you have reached the depth limit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was explored and key screens found",
                }
            },
            "required": ["summary"],
        },
    },
]

_SYSTEM_PROMPT = """\
You are an expert mobile QA engineer exploring a MakeMyTrip Android app feature area.

Your goal:
- Systematically explore every reachable screen within the feature described
- Document each distinct screen you encounter
- Avoid revisiting the same screen twice (the system will tell you if a screen was already visited)
- Stay focused on the feature area — do not wander into unrelated app sections
- After each navigation action, take a screenshot to record the new screen state
- When you have fully explored the feature or reached the maximum depth, call finish_exploration

Workflow for each step:
1. Call get_ui_elements to understand the current screen
2. Call take_screenshot to record it
3. Decide the best element to tap to explore deeper
4. Call tap_element or swipe_screen
5. Repeat until all paths are explored or depth limit is reached
6. Call finish_exploration with a summary

Always be methodical — breadth-first exploration is preferred over depth-first.
"""


class FlowExplorerAgent:
    """
    Explores a feature area of the MakeMyTrip app and builds a screen state graph.

    Usage:
        agent = FlowExplorerAgent(device, "hotel detail page gallery", "com.makemytrip")
        screen_graph = agent.explore()
    """

    def __init__(
        self,
        device: "AndroidDevice",
        feature_description: str,
        entry_package: str,
        max_depth: int = 20,
        navigation_steps: list[str] | None = None,
    ):
        self.device = device
        self.feature_description = feature_description
        self.entry_package = entry_package
        self.max_depth = max_depth
        self.navigation_steps = navigation_steps  # LOB-specific pre-navigation

        # Internal state
        self.visited_hashes: set[str] = set()
        self.screen_graph: list[dict] = []
        self._current_depth: int = 0
        self._done: bool = False
        self._exploration_summary: str = ""

        # Screenshot storage
        evidence_root = Path(get("uat.evidence_dir", ".tmp/evidence"))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._screenshot_dir = evidence_root / f"explore_{ts}" / "screens"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        self._screen_counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explore(self) -> dict:
        """
        Main entry point. Runs the Claude tool-use loop until exploration is done.

        Returns a screen graph dict:
        {
            "feature": str,
            "entry_package": str,
            "total_screens": int,
            "summary": str,
            "screens": [ {screen_id, hash, screenshot_path, key_elements, from_screen, depth}, ... ]
        }
        """
        logger.info(
            f"[FlowExplorer] Starting exploration of '{self.feature_description}' "
            f"(max_depth={self.max_depth})"
        )

        if self.navigation_steps:
            nav_block = "\n".join(
                f"  {i+1}. {step}" for i, step in enumerate(self.navigation_steps)
            )
            initial_prompt = (
                f"Feature to explore: {self.feature_description}\n"
                f"App package: {self.entry_package}\n"
                f"Maximum exploration depth: {self.max_depth}\n\n"
                "IMPORTANT — Follow these navigation steps in order to reach the target screen "
                "BEFORE doing any open exploration:\n"
                f"{nav_block}\n\n"
                "Once you have reached the target screen, explore it thoroughly and call "
                "finish_exploration when you are done."
            )
        else:
            initial_prompt = (
                f"Feature to explore: {self.feature_description}\n"
                f"App package: {self.entry_package}\n"
                f"Maximum exploration depth: {self.max_depth}\n\n"
                "Begin by taking a screenshot to see the current state of the app, "
                "then systematically explore the feature area. "
                "Call finish_exploration when you are done."
            )

        messages: list[dict] = [{"role": "user", "content": initial_prompt}]

        max_iterations = self.max_depth * 4  # safety cap
        iteration = 0

        while not self._done and iteration < max_iterations:
            iteration += 1
            response = ask_with_tools(
                messages=messages,
                tools=_TOOLS,
                system=_SYSTEM_PROMPT,
                max_tokens=4096,
            )

            # Append assistant turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                logger.info("[FlowExplorer] Claude ended turn without calling finish_exploration")
                break

            if response.stop_reason != "tool_use":
                logger.warning(f"[FlowExplorer] Unexpected stop_reason: {response.stop_reason}")
                break

            # Process tool calls
            tool_results: list[dict] = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = self._execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result_text),
                        }
                    )

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        logger.info(
            f"[FlowExplorer] Exploration complete. "
            f"Screens found: {len(self.screen_graph)}, Iterations: {iteration}"
        )

        return {
            "feature": self.feature_description,
            "entry_package": self.entry_package,
            "total_screens": len(self.screen_graph),
            "summary": self._exploration_summary,
            "screens": self.screen_graph,
        }

    # ------------------------------------------------------------------
    # Internal — tool execution
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch a tool call from Claude and return a string result."""
        logger.debug(f"[FlowExplorer] Tool: {tool_name} | Input: {tool_input}")

        try:
            if tool_name == "take_screenshot":
                return self._tool_take_screenshot(tool_input)
            elif tool_name == "get_ui_elements":
                return self._tool_get_ui_elements()
            elif tool_name == "tap_element":
                return self._tool_tap_element(tool_input)
            elif tool_name == "swipe_screen":
                return self._tool_swipe_screen(tool_input)
            elif tool_name == "press_back":
                return self._tool_press_back()
            elif tool_name == "finish_exploration":
                return self._tool_finish_exploration(tool_input)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"[FlowExplorer] Tool '{tool_name}' raised an error: {e}")
            return f"ERROR: {e}"

    def _tool_take_screenshot(self, tool_input: dict) -> str:
        label = tool_input.get("label", f"screen_{self._screen_counter}")
        self._screen_counter += 1
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{self._screen_counter:03d}_{label}_{ts}.png"
        save_path = str(self._screenshot_dir / filename)
        self.device.screenshot(save_path=save_path)

        # Compute screen hash from current UI tree to detect revisits
        ui_xml = ""
        try:
            ui_xml = self.device.get_ui_tree()
        except Exception:
            pass

        screen_hash = self._hash_screen(ui_xml)
        already_visited = screen_hash in self.visited_hashes

        if not already_visited:
            self.visited_hashes.add(screen_hash)
            # Extract a brief list of key elements from the XML (text nodes)
            key_elements = self._extract_key_elements(ui_xml)
            screen_entry = {
                "screen_id": f"screen_{len(self.screen_graph) + 1:03d}",
                "hash": screen_hash,
                "screenshot_path": save_path,
                "key_elements": key_elements,
                "from_screen": (
                    self.screen_graph[-1]["screen_id"] if self.screen_graph else "entry"
                ),
                "depth": self._current_depth,
            }
            self.screen_graph.append(screen_entry)
            logger.info(
                f"[FlowExplorer] New screen recorded: {screen_entry['screen_id']} "
                f"(depth={self._current_depth}, hash={screen_hash})"
            )

        visited_note = " [ALREADY VISITED — do not re-explore]" if already_visited else " [NEW SCREEN]"
        return (
            f"Screenshot saved: {save_path}{visited_note}\n"
            f"Screen hash: {screen_hash}\n"
            f"Total unique screens so far: {len(self.screen_graph)}\n"
            f"Current depth: {self._current_depth} / {self.max_depth}"
        )

    def _tool_get_ui_elements(self) -> str:
        xml = self.device.get_ui_tree()
        if len(xml) > 3000:
            return xml[:3000] + "\n...[truncated, call again for more]"
        return xml

    def _tool_tap_element(self, tool_input: dict) -> str:
        element_text = tool_input.get("element_text", "").strip()
        x = tool_input.get("x")
        y = tool_input.get("y")

        if element_text:
            success = self.device.tap_text(element_text)
            if success:
                self._current_depth += 1
                return f"Tapped element with text: '{element_text}'"
            else:
                # Fall back to coordinates if provided
                if x is not None and y is not None:
                    self.device.tap(int(x), int(y))
                    self._current_depth += 1
                    return f"Text '{element_text}' not found; tapped coordinates ({x}, {y})"
                return f"Element with text '{element_text}' not found on screen"
        elif x is not None and y is not None:
            self.device.tap(int(x), int(y))
            self._current_depth += 1
            return f"Tapped coordinates ({x}, {y})"
        else:
            return "ERROR: tap_element requires either element_text or (x, y) coordinates"

    def _tool_swipe_screen(self, tool_input: dict) -> str:
        direction = tool_input.get("direction", "up")
        self.device.swipe(direction)
        return f"Swiped {direction}"

    def _tool_press_back(self) -> str:
        self.device.press_back()
        if self._current_depth > 0:
            self._current_depth -= 1
        return "Pressed back"

    def _tool_finish_exploration(self, tool_input: dict) -> str:
        summary = tool_input.get("summary", "Exploration complete")
        self._exploration_summary = summary
        self._done = True
        logger.info(f"[FlowExplorer] finish_exploration called. Summary: {summary}")
        return (
            f"Exploration marked as complete.\n"
            f"Total unique screens discovered: {len(self.screen_graph)}\n"
            f"Summary: {summary}"
        )

    # ------------------------------------------------------------------
    # Internal — helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_screen(ui_xml: str) -> str:
        """Produce a short stable hash of the UI tree for deduplication."""
        # Normalise whitespace to reduce noise from minor attribute reordering
        normalised = " ".join(ui_xml.split())
        return hashlib.sha256(normalised.encode()).hexdigest()[:12]

    @staticmethod
    def _extract_key_elements(ui_xml: str) -> list[str]:
        """
        Quick heuristic extraction of visible text labels from the XML.
        Returns up to 20 non-empty text values found in text="..." attributes.
        """
        import re
        texts = re.findall(r'text="([^"]{2,50})"', ui_xml)
        seen: set[str] = set()
        result: list[str] = []
        for t in texts:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
            if len(result) >= 20:
                break
        return result
