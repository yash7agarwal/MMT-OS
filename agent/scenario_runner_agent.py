"""
agent/scenario_runner_agent.py — Single scenario executor

Executes one test scenario on one account using Claude + Android device tools.
Designed to be spawned in parallel by the orchestrator.

Each instance gets a fresh Claude context — no shared state between runners.
Returns a structured evidence pack for evaluation.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from agent.health_monitor import HealthMonitor, CRITICAL_STATES
from tools.screenshot import EvidenceCapture
from utils.claude_client import ask_with_tools
from utils.config import get

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format) for Claude's QA tool-use loop
# ---------------------------------------------------------------------------
_TOOLS: list[dict] = [
    {
        "name": "take_screenshot",
        "description": (
            "Take a screenshot of the current screen and save it as evidence. "
            "Returns the file path of the saved screenshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short label describing what this screenshot captures (e.g. 'after_tap_book')",
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ui_elements",
        "description": (
            "Get all interactive elements on the current screen as XML. "
            "Use this to find element texts, IDs, and coordinates before tapping."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "tap_element",
        "description": "Tap a UI element by its visible text, or by screen coordinates if text is unavailable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_text": {
                    "type": "string",
                    "description": "Visible text of the element to tap",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate to tap (fallback if element_text not found)",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate to tap (fallback if element_text not found)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "swipe_screen",
        "description": "Swipe the screen in a direction.",
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
        "name": "type_text_in_field",
        "description": "Type text into the currently focused input field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to type into the focused field",
                }
            },
            "required": ["text"],
        },
    },
    {
        "name": "press_back",
        "description": "Press the Android back button.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "mark_step_complete",
        "description": (
            "Record that a scenario step has been completed with its result. "
            "Call this after completing each step in the scenario."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "step_description": {
                    "type": "string",
                    "description": "Description of the step that was completed",
                },
                "result": {
                    "type": "string",
                    "enum": ["pass", "fail", "partial"],
                    "description": "Result of this step",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional observations or evidence notes for this step",
                },
            },
            "required": ["step_description", "result"],
        },
    },
    {
        "name": "finish_scenario",
        "description": (
            "Mark the scenario as complete. Call this when all steps have been executed "
            "or when a blocking issue prevents further progress."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome_description": {
                    "type": "string",
                    "description": "Detailed description of the final outcome and what was observed",
                },
                "passed": {
                    "type": "boolean",
                    "description": "True if the scenario passed overall, False if it failed",
                },
            },
            "required": ["outcome_description", "passed"],
        },
    },
]

_SYSTEM_PROMPT = """\
You are a senior QA tester executing a specific test scenario on MakeMyTrip Android app.

Your responsibilities:
- Execute each step in the scenario methodically and in order
- After each significant action, take a screenshot as evidence
- Mark each step as pass/fail/partial using mark_step_complete
- If a step is blocking (e.g. a required element is missing), mark it as fail and note why
- When all steps are done (or you are blocked), call finish_scenario with a clear outcome description
- Be precise: note exactly what you see vs what was expected

Evidence guidelines:
- Take a screenshot at the start of the scenario
- Take a screenshot after each major interaction
- Take a screenshot at the end showing the final state
"""


class ScenarioRunnerAgent:
    """
    Executes a single test scenario on a single account using Claude + Android tools.

    Usage:
        runner = ScenarioRunnerAgent(device, scenario, account_id, "hotel gallery", run_id)
        result = runner.run()
    """

    def __init__(
        self,
        device: "AndroidDevice",
        scenario: dict,
        account_id: str,
        feature_description: str,
        run_id: str,
        package_name: str = "",
        health_monitor: HealthMonitor | None = None,
    ):
        """
        Args:
            device: Connected AndroidDevice instance
            scenario: {"name": str, "steps": [str], "expected_outcome": str, "severity": str}
            account_id: ID of the account under test
            feature_description: Short description of the feature being tested
            run_id: Parent run identifier for evidence grouping
            package_name: Android package name of the app under test (used by HealthMonitor)
            health_monitor: Optional pre-constructed HealthMonitor; created automatically
                            if package_name is provided and this is None.
        """
        self.device = device
        self.scenario = scenario
        self.account_id = account_id
        self.feature_description = feature_description
        self.run_id = run_id
        self.package_name = package_name

        # Internal state
        self._done: bool = False
        self._result: dict | None = None
        self._steps_taken: list[dict] = []

        # Evidence capture
        self.evidence = EvidenceCapture(run_id, account_id, scenario["name"])

        # Health monitor — use provided one, or create if package_name given
        if health_monitor is not None:
            self.health_monitor: HealthMonitor | None = health_monitor
        elif package_name:
            self.health_monitor = HealthMonitor(device, package_name, run_id)
        else:
            self.health_monitor = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute the scenario and return a structured result dict.

        Returns:
            {
                "scenario_name": str,
                "account_id": str,
                "status": "completed" | "failed" | "blocked",
                "steps_taken": [...],
                "final_outcome": str,
                "evidence_pack": dict,
                "error": str | None
            }
        """
        scenario_name = self.scenario["name"]
        logger.info(
            f"[ScenarioRunner] Starting: '{scenario_name}' | account: {self.account_id}"
        )

        try:
            self._run_tool_loop()
        except Exception as e:
            logger.error(
                f"[ScenarioRunner] Unexpected error in '{scenario_name}' "
                f"for {self.account_id}: {e}"
            )
            self.evidence.save_log()
            return {
                "scenario_name": scenario_name,
                "account_id": self.account_id,
                "status": "failed",
                "steps_taken": self._steps_taken,
                "final_outcome": f"Runner crashed: {e}",
                "evidence_pack": self.evidence.get_evidence_pack(),
                "error": str(e),
            }

        self.evidence.save_log()

        # Determine status from finish_scenario result
        if self._result:
            passed = self._result.get("passed", False)
            status = "completed" if passed else "failed"
            final_outcome = self._result.get("outcome_description", "No outcome recorded")
        else:
            status = "blocked"
            final_outcome = "Scenario did not reach finish_scenario — possibly blocked"

        logger.info(
            f"[ScenarioRunner] Finished: '{scenario_name}' | "
            f"account: {self.account_id} | status: {status}"
        )

        return {
            "scenario_name": scenario_name,
            "account_id": self.account_id,
            "status": status,
            "steps_taken": self._steps_taken,
            "final_outcome": final_outcome,
            "evidence_pack": self.evidence.get_evidence_pack(),
            "error": None,
        }

    # ------------------------------------------------------------------
    # Internal — tool-use loop
    # ------------------------------------------------------------------

    def _run_tool_loop(self) -> None:
        """Run the Claude tool-use loop until the scenario finishes."""
        scenario_name = self.scenario["name"]
        steps_formatted = "\n".join(
            f"  {i + 1}. {step}" for i, step in enumerate(self.scenario.get("steps", []))
        )
        initial_prompt = (
            f"Feature under test: {self.feature_description}\n"
            f"Account ID: {self.account_id}\n\n"
            f"Scenario: {scenario_name}\n"
            f"Severity: {self.scenario.get('severity', 'medium')}\n\n"
            f"Steps to execute:\n{steps_formatted}\n\n"
            f"Expected outcome:\n{self.scenario.get('expected_outcome', 'Not specified')}\n\n"
            "Begin by taking a screenshot to see the current app state, then execute each step. "
            "Call finish_scenario when all steps are done or you are blocked."
        )

        messages: list[dict] = [{"role": "user", "content": initial_prompt}]
        max_iterations = len(self.scenario.get("steps", [])) * 6 + 20  # reasonable cap

        # Reset health monitor circuit breaker for this scenario
        if self.health_monitor is not None:
            self.health_monitor.reset_attempts()

        for iteration in range(max_iterations):
            if self._done:
                break

            # --- Self-healing check before each Claude iteration ---
            if self.health_monitor is not None:
                heal_context = f"scenario={scenario_name}, iteration={iteration}"
                heal_result = self.health_monitor.check_and_heal(context=heal_context)

                if not heal_result.healed:
                    # For critical failures (e.g. DEVICE_UNRESPONSIVE), abort the loop
                    if heal_result.gap_type in CRITICAL_STATES:
                        logger.error(
                            f"[ScenarioRunner] Critical failure — aborting: "
                            f"gap_type={heal_result.gap_type} | scenario={scenario_name}"
                        )
                        self._done = True
                        self._result = None  # triggers "blocked" status in run()
                        # Inject a tool-result-style note so callers see the block reason
                        messages.append({
                            "role": "user",
                            "content": (
                                f"[HEALTH_MONITOR] CRITICAL: Device unresponsive after "
                                f"{heal_result.attempts} recovery attempts. "
                                f"Scenario blocked. gap_type={heal_result.gap_type}"
                            ),
                        })
                        break
                    else:
                        # Non-critical but unhealed: log and continue (Claude may handle it)
                        logger.warning(
                            f"[ScenarioRunner] Heal failed (non-critical): "
                            f"gap_type={heal_result.gap_type} attempts={heal_result.attempts}"
                        )

                elif heal_result.gap_type != "APP_RUNNING":
                    # A recovery happened successfully — inform Claude via a synthetic message
                    heal_note = (
                        f"[HEALTH_MONITOR] Auto-recovery performed: "
                        f"state={heal_result.gap_type} | "
                        f"action={heal_result.recovery_action} | "
                        f"state_after={heal_result.state_after} | "
                        f"attempts={heal_result.attempts}. "
                        "The app has been relaunched/restored. "
                        "Re-orient to the current screen before continuing."
                    )
                    # Append as a user message only if there are already assistant messages
                    # (avoids duplicating info in the very first iteration)
                    if len(messages) > 1:
                        messages.append({"role": "user", "content": heal_note})
                    else:
                        # Prepend to initial prompt so Claude knows from the start
                        messages[0]["content"] = heal_note + "\n\n" + messages[0]["content"]

            response = ask_with_tools(
                messages=messages,
                tools=_TOOLS,
                system=_SYSTEM_PROMPT,
                max_tokens=4096,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                logger.info(
                    f"[ScenarioRunner] Claude ended turn without finish_scenario "
                    f"(iteration {iteration})"
                )
                break

            if response.stop_reason != "tool_use":
                logger.warning(
                    f"[ScenarioRunner] Unexpected stop_reason: {response.stop_reason}"
                )
                break

            # Execute tool calls
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

    # ------------------------------------------------------------------
    # Internal — tool dispatch
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch a tool call and return a string result for Claude."""
        logger.debug(f"[ScenarioRunner] Tool: {tool_name} | Input: {tool_input}")
        try:
            if tool_name == "take_screenshot":
                return self._tool_take_screenshot(tool_input)
            elif tool_name == "get_ui_elements":
                return self._tool_get_ui_elements()
            elif tool_name == "tap_element":
                return self._tool_tap_element(tool_input)
            elif tool_name == "swipe_screen":
                return self._tool_swipe_screen(tool_input)
            elif tool_name == "type_text_in_field":
                return self._tool_type_text(tool_input)
            elif tool_name == "press_back":
                return self._tool_press_back()
            elif tool_name == "mark_step_complete":
                return self._tool_mark_step_complete(tool_input)
            elif tool_name == "finish_scenario":
                return self._tool_finish_scenario(tool_input)
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(
                f"[ScenarioRunner] Tool '{tool_name}' raised: {e}"
            )
            return f"ERROR executing {tool_name}: {e}"

    def _tool_take_screenshot(self, tool_input: dict) -> str:
        label = tool_input.get("label", "step")
        path = self.evidence.capture(
            self.device,
            step_label=label,
            action_taken=f"screenshot:{label}",
        )
        return f"Screenshot saved: {path}"

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
                # Capture evidence after tap
                self.evidence.capture(
                    self.device,
                    step_label=f"after_tap_{element_text[:30]}",
                    action_taken=f"tap_text:{element_text}",
                )
                return f"Tapped '{element_text}' successfully"
            else:
                if x is not None and y is not None:
                    self.device.tap(int(x), int(y))
                    self.evidence.capture(
                        self.device,
                        step_label=f"after_tap_coords_{x}_{y}",
                        action_taken=f"tap_coords:({x},{y})",
                    )
                    return f"Text '{element_text}' not found; tapped coordinates ({x}, {y})"
                return f"Element '{element_text}' not found — no coordinates provided as fallback"
        elif x is not None and y is not None:
            self.device.tap(int(x), int(y))
            self.evidence.capture(
                self.device,
                step_label=f"after_tap_coords_{x}_{y}",
                action_taken=f"tap_coords:({x},{y})",
            )
            return f"Tapped coordinates ({x}, {y})"
        else:
            return "ERROR: tap_element requires element_text or (x, y)"

    def _tool_swipe_screen(self, tool_input: dict) -> str:
        direction = tool_input.get("direction", "up")
        self.device.swipe(direction)
        return f"Swiped {direction}"

    def _tool_type_text(self, tool_input: dict) -> str:
        text = tool_input.get("text", "")
        self.device.type_text(text)
        return f"Typed: '{text}'"

    def _tool_press_back(self) -> str:
        self.device.press_back()
        return "Pressed back"

    def _tool_mark_step_complete(self, tool_input: dict) -> str:
        step_description = tool_input.get("step_description", "Unnamed step")
        result = tool_input.get("result", "partial")
        notes = tool_input.get("notes", "")
        entry = {
            "step": step_description,
            "result": result,
            "notes": notes,
            "timestamp": datetime.now().isoformat(),
        }
        self._steps_taken.append(entry)
        logger.info(
            f"[ScenarioRunner] Step '{step_description[:60]}' -> {result}"
        )
        return f"Step recorded: {result}"

    def _tool_finish_scenario(self, tool_input: dict) -> str:
        outcome_description = tool_input.get("outcome_description", "No outcome provided")
        passed = bool(tool_input.get("passed", False))
        self._result = {
            "outcome_description": outcome_description,
            "passed": passed,
        }
        self._done = True
        # Capture final state screenshot
        try:
            self.evidence.capture(
                self.device,
                step_label="scenario_final_state",
                action_taken="finish_scenario",
                notes=outcome_description[:200],
            )
        except Exception as e:
            logger.warning(f"[ScenarioRunner] Final screenshot failed: {e}")
        logger.info(
            f"[ScenarioRunner] finish_scenario: passed={passed} | {outcome_description[:100]}"
        )
        return "Scenario marked complete"
