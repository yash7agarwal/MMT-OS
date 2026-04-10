"""tools/quick_navigator.py — Deterministic step executor for QuickUAT.

Executes a list of simple action dicts against an AndroidDevice without any
Claude API calls. Each action maps directly to an AndroidDevice method.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)


@dataclass
class NavigationResult:
    success: bool
    last_step: str = ""
    error: str | None = None


class QuickNavigator:
    """Executes deterministic navigation steps using AndroidDevice methods."""

    def __init__(self, device: AndroidDevice):
        self.device = device

    def navigate(self, steps: list[dict]) -> NavigationResult:
        """
        Execute each step in order. Stops on first failure.

        Returns NavigationResult with success=True if all steps completed,
        or success=False with the failing step name and error message.
        """
        last_action = ""
        for i, step in enumerate(steps):
            action = step.get("action", "")
            last_action = action
            logger.debug(f"[QuickNavigator] Step {i+1}/{len(steps)}: {action}")
            try:
                self._dispatch(action, step)
            except Exception as exc:
                logger.error(f"[QuickNavigator] Step failed — {action}: {exc}")
                return NavigationResult(success=False, last_step=action, error=str(exc))
        return NavigationResult(success=True, last_step=last_action)

    def _dispatch(self, action: str, step: dict) -> None:
        d = self.device

        if action == "tap_text":
            text = step["text"]
            exact = step.get("exact", False)
            if not d.tap_text(text, exact=exact):
                raise RuntimeError(f"'{text}' not found on screen")

        elif action == "type_text":
            d.type_text(step["text"])

        elif action == "wait_ms":
            time.sleep(step["ms"] / 1000)

        elif action == "wait_for_text":
            text = step["text"]
            timeout = step.get("timeout", 8)
            # Use uiautomator2 directly — device.wait_for_element() has a known
            # bug where it ignores the bool return value of .wait(), always returning True.
            found = d.d(textContains=text).exists(timeout=timeout)
            if not found:
                raise RuntimeError(f"'{text}' not visible after {timeout}s")

        elif action == "press_back":
            d.press_back()

        elif action == "tap_if_present":
            # Like tap_text but does NOT raise if element is absent — use for optional dialogs
            text = step["text"]
            exact = step.get("exact", False)
            d.tap_text(text, exact=exact)  # returns bool, ignore False

        elif action == "scroll_to_text":
            text = step["text"]
            if not d.scroll_to_text(text):
                raise RuntimeError(f"'{text}' not found after scrolling")

        elif action == "tap_index":
            resource_id = step["resource_id"]
            idx = step.get("index", 0)
            try:
                d.d(resourceId=resource_id)[idx].click()
                time.sleep(1.5)
            except Exception as exc:
                raise RuntimeError(f"tap_index resourceId={resource_id} idx={idx}: {exc}")

        elif action == "wait_ui_idle":
            # Wait until the UI hierarchy stops changing (page fully rendered/interactive).
            # Polls every interval_ms until two consecutive dumps are identical, or timeout.
            timeout = step.get("timeout", 10)
            interval = step.get("interval_ms", 800) / 1000
            deadline = time.time() + timeout
            prev = None
            while time.time() < deadline:
                cur = d.d.dump_hierarchy()
                if prev is not None and cur == prev:
                    logger.debug("[QuickNavigator] UI idle detected")
                    return
                prev = cur
                time.sleep(interval)
            # Timeout is non-fatal — proceed with whatever state we have
            logger.warning(f"[QuickNavigator] wait_ui_idle: UI still changing after {timeout}s, proceeding anyway")

        else:
            raise RuntimeError(f"Unknown action: {action!r}")
