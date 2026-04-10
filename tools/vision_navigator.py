"""tools/vision_navigator.py — Generic vision-guided navigation engine.

Uses a screenshot → Claude vision → action loop to navigate any app to any screen.
No hardcoded steps, no LOB-specific knowledge. Works for any feature.

Architecture: screenshot → Haiku vision (2s) → ADB tap → repeat until goal reached.
Typical: 3-6 steps, 10-18s total navigation time.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tools.android_device import AndroidDevice
from utils.claude_client import FAST_MODEL, ask_vision

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a mobile UI navigation agent. You see a screenshot of a phone screen.
Your job is to output ONE JSON action to progress toward the navigation goal.

COORDINATE SYSTEM (critical):
- Use NORMALIZED coordinates as fractions of the image width and height.
- x_pct: 0.0 = leftmost edge, 1.0 = rightmost edge
- y_pct: 0.0 = topmost edge, 1.0 = bottommost edge
- Example: a button in the top-left quarter would be around x_pct=0.25, y_pct=0.25
- Example: a centered button at the bottom would be around x_pct=0.5, y_pct=0.9
- This is RESOLUTION-INDEPENDENT — same number works for any phone.

LOCATE-THEN-TAP DISCIPLINE (critical):
- Step 1: Visually scan the screenshot. Find the EXACT element matching the goal by reading its label/icon.
- Step 2: Estimate that element's center as fractions of the image dimensions.
- Step 3: Before responding, sanity-check: "What would actually be at (x_pct, y_pct)? Is it the element I want?"
- The reasoning field MUST quote the exact visible label at the tap location.
- Example reasoning: "Hotels tile at center-left of LOB grid, building icon with text 'Hotels' below"
- If you can't clearly see the target element on this screen, do not tap — use wait, press_back, or relaunch_app.

WRONG-SCREEN RECOVERY (critical):
- First ask: "Is this screen on the path to the goal?"
- If you are inside an unrelated funnel (e.g., goal is Hotels but you're in Flights search), \
DO NOT try to navigate sideways. Use press_back repeatedly to return to the home screen, then proceed.
- If press_back doesn't reach home after a few tries, use relaunch_app.
- The home screen typically shows multiple LOB tiles (Flights, Hotels, Trains, Bus, Holidays etc.) in a grid.

Other rules:
- If a modal/popup/bottom sheet is blocking the screen, dismiss it first (tap its X, or press_back).
- If you see a splash screen or loading spinner with no actionable content, respond with wait.
- If the goal screen is showing AND you can verify the expected content, respond with done.

Available actions (respond with ONLY a JSON object, no markdown, no prose):
- Tap: {{"action":"tap","x_pct":<0.0-1.0>,"y_pct":<0.0-1.0>,"reasoning":"<exact visible label here>","done":false}}
- Swipe: {{"action":"swipe","direction":"up|down|left|right","reasoning":"<why>","done":false}}
- Press back: {{"action":"press_back","reasoning":"<why>","done":false}}
- Relaunch app: {{"action":"relaunch_app","reasoning":"<why reset is needed>","done":false}}
- Wait: {{"action":"wait","seconds":<int 1-5>,"reasoning":"<why>","done":false}}
- Done: {{"action":"done","reasoning":"<what you see that confirms the goal>","done":true}}"""

_USER_PROMPT = """\
GOAL: {goal}
{hints_line}
Step {step} of {max_steps}

What single action should I take next? Respond with JSON only."""


@dataclass
class VisionNavResult:
    success: bool
    steps_taken: int = 0
    elapsed_s: float = 0.0
    screenshots: list[str] = field(default_factory=list)
    error: str | None = None


class VisionNavigator:
    """Generic vision-guided navigator. Works for any app and any target screen."""

    def __init__(
        self,
        device: AndroidDevice,
        max_steps: int = 10,
        step_wait_s: float = 1.5,
        evidence_dir: str = ".tmp/evidence/vision_nav",
        package_name: str | None = None,
    ):
        self.device = device
        self.max_steps = max_steps
        self.step_wait_s = step_wait_s
        self.package_name = package_name
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        # Query device screen size
        info = device.d.info
        self.screen_width = info.get("displayWidth", 1080)
        self.screen_height = info.get("displayHeight", 2400)

        self._system_prompt = _SYSTEM_PROMPT.format(
            width=self.screen_width, height=self.screen_height
        )

    def navigate(self, goal: str, hints: str = "") -> VisionNavResult:
        """
        Vision-guided navigation loop.

        Args:
            goal: Natural language description of the target screen,
                  e.g. "Navigate to the Hotel Details Page"
            hints: Optional navigation tips to reduce steps,
                   e.g. "Tap Hotels tile, then SEARCH, then first hotel card"

        Returns:
            VisionNavResult with success=True if goal screen was reached.
        """
        t0 = time.time()
        screenshots: list[str] = []
        hints_line = f"HINTS: {hints}" if hints else ""

        logger.info(f"[VisionNav] Goal: {goal} | max_steps={self.max_steps}")

        for step in range(1, self.max_steps + 1):
            # 1. Screenshot
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sc_path = str(self.evidence_dir / f"step_{step}_{ts}.png")
            sc_bytes = self.device.screenshot(save_path=sc_path)
            screenshots.append(sc_path)

            # 2. Ask Claude vision
            user_prompt = _USER_PROMPT.format(
                goal=goal,
                hints_line=hints_line,
                step=step,
                max_steps=self.max_steps,
            )
            try:
                raw = ask_vision(
                    prompt=user_prompt,
                    image_bytes=sc_bytes,
                    system=self._system_prompt,
                    model=FAST_MODEL,
                    max_tokens=256,
                )
            except Exception as exc:
                logger.error(f"[VisionNav] Vision API call failed: {exc}")
                return VisionNavResult(
                    success=False,
                    steps_taken=step,
                    elapsed_s=round(time.time() - t0, 1),
                    screenshots=screenshots,
                    error=f"Vision API error: {exc}",
                )

            # 3. Parse response
            try:
                action = self._parse_response(raw)
            except Exception as exc:
                logger.warning(f"[VisionNav] Failed to parse response: {raw[:200]}")
                # Non-fatal — skip this step and retry
                time.sleep(self.step_wait_s)
                continue

            reasoning = action.get("reasoning", "")
            act = action.get("action", "")
            logger.info(f"[VisionNav] Step {step}/{self.max_steps}: {act} — {reasoning}")

            # 4. Check if done
            if action.get("done", False):
                elapsed = round(time.time() - t0, 1)
                logger.info(f"[VisionNav] Goal reached in {step} steps ({elapsed}s)")
                return VisionNavResult(
                    success=True,
                    steps_taken=step,
                    elapsed_s=elapsed,
                    screenshots=screenshots,
                )

            # 5. Execute action
            self._execute(action)

            # 6. Wait for UI to settle
            time.sleep(self.step_wait_s)

        # Exhausted max steps
        elapsed = round(time.time() - t0, 1)
        logger.warning(f"[VisionNav] Max steps ({self.max_steps}) reached without reaching goal")
        return VisionNavResult(
            success=False,
            steps_taken=self.max_steps,
            elapsed_s=elapsed,
            screenshots=screenshots,
            error=f"Goal not reached after {self.max_steps} steps",
        )

    def _parse_response(self, raw: str) -> dict:
        """Parse Claude's JSON response, tolerating markdown fences."""
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract first JSON object from text
            match = re.search(r"\{[^{}]+\}", text)
            if match:
                return json.loads(match.group())
            raise

    def _execute(self, action: dict) -> None:
        """Execute a single navigation action. Tolerates missing/malformed fields."""
        act = action.get("action", "")

        if act == "tap":
            # Prefer percentage coords (resolution-independent); fall back to absolute pixels
            x_pct = action.get("x_pct")
            y_pct = action.get("y_pct")
            if x_pct is not None and y_pct is not None:
                try:
                    x = int(float(x_pct) * self.screen_width)
                    y = int(float(y_pct) * self.screen_height)
                except (TypeError, ValueError) as e:
                    logger.warning(f"[VisionNav] tap with invalid pct coords ({x_pct},{y_pct}): {e}")
                    return
            else:
                # Legacy absolute coords
                x = action.get("x")
                y = action.get("y")
                if x is None or y is None:
                    logger.warning(f"[VisionNav] tap action missing coordinates: {action}")
                    return
                try:
                    x = int(x)
                    y = int(y)
                except (TypeError, ValueError) as e:
                    logger.warning(f"[VisionNav] tap with invalid coords ({x},{y}): {e}")
                    return
            logger.info(f"[VisionNav]   → tap pixel ({x},{y})")
            self.device.tap(x, y)

        elif act == "swipe":
            direction = action.get("direction", "up")
            if direction not in {"up", "down", "left", "right"}:
                logger.warning(f"[VisionNav] invalid swipe direction: {direction}")
                return
            self.device.swipe(direction)

        elif act == "press_back":
            self.device.press_back()

        elif act == "relaunch_app":
            if not self.package_name:
                logger.warning("[VisionNav] relaunch_app requested but no package_name set")
                return
            try:
                from tools.apk_manager import force_stop_app, launch_app
                force_stop_app(self.package_name, serial=self.device.serial)
                time.sleep(0.5)
                launch_app(self.package_name, serial=self.device.serial)
                time.sleep(3)  # cold start settle
            except Exception as e:
                logger.warning(f"[VisionNav] relaunch_app failed: {e}")

        elif act == "wait":
            try:
                seconds = min(int(action.get("seconds", 2)), 5)
            except (TypeError, ValueError):
                seconds = 2
            time.sleep(seconds)

        elif act == "done":
            pass  # Handled by caller

        else:
            logger.warning(f"[VisionNav] Unknown action '{act}', skipping")
