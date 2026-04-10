"""
agent/health_monitor.py — Self-healing engine for UAT runs

Detects failure states on the Android device and attempts automatic recovery.
All gaps (failures, recovery attempts, outcomes) are logged to memory/gaps_log.jsonl.

Failure states detected:
  APP_RUNNING           — target package is in foreground, no issues
  APP_NOT_OPEN          — a different package is in foreground
  APP_CRASHED           — crash dialog detected ("has stopped", "keeps stopping")
  DEVICE_UNRESPONSIVE   — UI dump / package query fails or times out
  NAVIGATION_STUCK      — two consecutive UI dumps are identical (app frozen)
  WRONG_SCREEN          — login screen detected when test should be past login

Recovery playbooks per state:
  APP_NOT_OPEN          → relaunch app
  APP_CRASHED           → dismiss dialog → force-stop → relaunch
  DEVICE_UNRESPONSIVE   → restart adb server → reconnect → relaunch
  NAVIGATION_STUCK      → swipe up → press back → relaunch if still stuck
  WRONG_SCREEN          → press back (return caller-provided re-login is out of scope)

Circuit breaker: max 3 attempts per scenario before giving up.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from utils.config import get

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GAP_LOG_PATH = Path("memory/gaps_log.jsonl")

# Crash-dialog phrases (lowercased for matching)
_CRASH_PHRASES = [
    "has stopped",
    "keeps stopping",
    "unfortunately",
    "app isn't responding",
    "isn't responding",
]

# Login screen indicator phrases (lowercased) — heuristic
_LOGIN_PHRASES = [
    "sign in",
    "log in",
    "login",
    "enter password",
    "enter email",
    "mobile number",
    "enter mobile",
    "forgot password",
    "create account",
    "new user",
]

# Maximum recovery attempts before giving up
MAX_RECOVERY_ATTEMPTS = 3

# Seconds to wait between UI snapshots for stuck-detection
STUCK_SNAPSHOT_GAP = 2.0

# Seconds to wait after relaunch before re-checking
POST_LAUNCH_WAIT = 3.0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class AppState:
    APP_RUNNING = "APP_RUNNING"
    APP_NOT_OPEN = "APP_NOT_OPEN"
    APP_CRASHED = "APP_CRASHED"
    DEVICE_UNRESPONSIVE = "DEVICE_UNRESPONSIVE"
    NAVIGATION_STUCK = "NAVIGATION_STUCK"
    WRONG_SCREEN = "WRONG_SCREEN"


CRITICAL_STATES = {AppState.DEVICE_UNRESPONSIVE}


@dataclass
class HealResult:
    healed: bool
    gap_type: str
    recovery_action: str
    attempts: int
    state_before: str = ""
    state_after: str = ""


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------

class HealthMonitor:
    """
    Self-healing engine tied to one UAT run.

    Usage:
        monitor = HealthMonitor(device, "com.example.app", "run_20240101_120000")
        result = monitor.check_and_heal(context="scenario=Login, iteration=3")
        if not result.healed and result.gap_type in CRITICAL_STATES:
            # abort scenario
    """

    def __init__(
        self,
        device: "AndroidDevice",
        package_name: str,
        run_id: str,
    ):
        self.device = device
        self.package_name = package_name
        self.run_id = run_id

        # Per-call attempt counter (reset by caller between scenarios via reset_attempts)
        self._attempt_count: int = 0

        # Ensure gaps log file exists
        _GAP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _GAP_LOG_PATH.exists():
            _GAP_LOG_PATH.touch()

        logger.debug(
            f"[HealthMonitor] Initialized for package={package_name} run_id={run_id}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_attempts(self) -> None:
        """Reset the circuit-breaker counter. Call at the start of each new scenario."""
        self._attempt_count = 0

    def check_and_heal(self, context: str = "") -> HealResult:
        """
        Detect the current device/app state and attempt recovery if unhealthy.

        Args:
            context: Free-form string describing where in the run this is called
                     (e.g. "scenario=Login, iteration=3"). Used only for logging.

        Returns:
            HealResult with healed=True if the app is running normally after this call.
        """
        state = self._detect_state()
        logger.debug(f"[HealthMonitor] Detected state={state} | context={context!r}")

        if state == AppState.APP_RUNNING:
            return HealResult(
                healed=True,
                gap_type=AppState.APP_RUNNING,
                recovery_action="none",
                attempts=0,
                state_before=state,
                state_after=state,
            )

        # Circuit breaker
        if self._attempt_count >= MAX_RECOVERY_ATTEMPTS:
            logger.warning(
                f"[HealthMonitor] Circuit breaker open: "
                f"{self._attempt_count} attempts already made. State={state}"
            )
            self._log_gap(
                gap_type=state,
                context=context,
                recovery_action="circuit_breaker_open",
                success=False,
            )
            return HealResult(
                healed=False,
                gap_type=state,
                recovery_action="circuit_breaker_open",
                attempts=self._attempt_count,
                state_before=state,
                state_after=state,
            )

        self._attempt_count += 1
        recovery_action, success = self._execute_recovery(state)

        # Re-detect state after recovery
        time.sleep(POST_LAUNCH_WAIT)
        state_after = self._detect_state()
        healed = state_after == AppState.APP_RUNNING

        self._log_gap(
            gap_type=state,
            context=context,
            recovery_action=recovery_action,
            success=healed,
        )

        logger.info(
            f"[HealthMonitor] Recovery: state={state} -> {state_after} | "
            f"action={recovery_action} | healed={healed} | attempts={self._attempt_count}"
        )

        return HealResult(
            healed=healed,
            gap_type=state,
            recovery_action=recovery_action,
            attempts=self._attempt_count,
            state_before=state,
            state_after=state_after,
        )

    # ------------------------------------------------------------------
    # State detection
    # ------------------------------------------------------------------

    def _detect_state(self) -> str:
        """
        Inspect the device and return one of the AppState constants.

        Order of checks:
          1. Can we talk to the device at all?  → DEVICE_UNRESPONSIVE
          2. Is there a crash dialog on screen?  → APP_CRASHED
          3. Is the correct package in foreground? → APP_NOT_OPEN
          4. Is the UI frozen (stuck)?           → NAVIGATION_STUCK
          5. Is there a login form visible?      → WRONG_SCREEN
          6. Otherwise                           → APP_RUNNING
        """
        # --- 1. Device responsiveness ---
        try:
            current_package = self.device.get_current_package()
        except Exception as e:
            logger.warning(f"[HealthMonitor] Device unresponsive: {e}")
            return AppState.DEVICE_UNRESPONSIVE

        # --- 2. Crash dialog ---
        try:
            ui_xml = self.device.get_ui_tree()
        except Exception as e:
            logger.warning(f"[HealthMonitor] UI dump failed: {e}")
            return AppState.DEVICE_UNRESPONSIVE

        ui_lower = ui_xml.lower()
        if any(phrase in ui_lower for phrase in _CRASH_PHRASES):
            return AppState.APP_CRASHED

        # --- 3. Correct package ---
        if self.package_name and current_package != self.package_name:
            return AppState.APP_NOT_OPEN

        # --- 4. Navigation stuck — compare two snapshots ---
        try:
            time.sleep(STUCK_SNAPSHOT_GAP)
            ui_xml2 = self.device.get_ui_tree()
            if ui_xml.strip() == ui_xml2.strip() and len(ui_xml.strip()) > 100:
                # A completely static UI for 2+ seconds while the correct app is
                # foreground is suspicious. Extra heuristic: check for a progress
                # indicator keyword that would mean loading is legit.
                loading_hints = ["progress", "loading", "spinner", "circular"]
                # Rich content pages (hotel/flight details, search results) are
                # legitimately static — large UI trees (>3000 chars) are almost
                # never frozen; they're just content-heavy screens.
                if len(ui_xml.strip()) > 3000:
                    pass  # rich content page — not stuck
                elif not any(h in ui_lower for h in loading_hints):
                    return AppState.NAVIGATION_STUCK
        except Exception:
            pass  # non-fatal; skip stuck check

        # --- 5. Login screen ---
        if any(phrase in ui_lower for phrase in _LOGIN_PHRASES):
            return AppState.WRONG_SCREEN

        return AppState.APP_RUNNING

    # ------------------------------------------------------------------
    # Recovery playbooks
    # ------------------------------------------------------------------

    def _execute_recovery(self, state: str) -> tuple[str, bool]:
        """
        Run the recovery playbook for the given state.

        Returns:
            (recovery_action_label, immediate_success_bool)
        """
        try:
            if state == AppState.APP_CRASHED:
                return self._recover_crashed()
            elif state == AppState.APP_NOT_OPEN:
                return self._recover_not_open()
            elif state == AppState.DEVICE_UNRESPONSIVE:
                return self._recover_unresponsive()
            elif state == AppState.NAVIGATION_STUCK:
                return self._recover_stuck()
            elif state == AppState.WRONG_SCREEN:
                return self._recover_wrong_screen()
            else:
                return "no_recovery_needed", True
        except Exception as e:
            logger.error(f"[HealthMonitor] Recovery playbook raised: {e}")
            return f"recovery_error:{e}", False

    def _recover_crashed(self) -> tuple[str, bool]:
        """Dismiss crash dialog → force-stop → relaunch."""
        logger.info("[HealthMonitor] Recovery: APP_CRASHED — dismissing + relaunching")
        # Try tapping "OK" / "Close" buttons in the crash dialog
        for btn in ["OK", "Close", "Got it", "Wait"]:
            try:
                found = self.device.tap_text(btn, exact=True)
                if found:
                    time.sleep(0.5)
                    break
            except Exception:
                pass
        # Press back as a fallback dismiss
        try:
            self.device.press_back()
        except Exception:
            pass
        # Force-stop then relaunch
        self._force_stop_and_relaunch()
        return "dismiss_crash_dialog_relaunch", True

    def _recover_not_open(self) -> tuple[str, bool]:
        """Just relaunch the app."""
        logger.info("[HealthMonitor] Recovery: APP_NOT_OPEN — relaunching")
        success = self._relaunch_app()
        return "relaunch_app", success

    def _recover_unresponsive(self) -> tuple[str, bool]:
        """Restart ADB server and reconnect, then relaunch."""
        logger.info("[HealthMonitor] Recovery: DEVICE_UNRESPONSIVE — restarting adb")
        try:
            subprocess.run(["adb", "kill-server"], capture_output=True, timeout=15)
            time.sleep(1)
            subprocess.run(["adb", "start-server"], capture_output=True, timeout=15)
            time.sleep(2)
            # Re-establish the uiautomator2 connection
            self.device.d.healthcheck()
        except Exception as e:
            logger.error(f"[HealthMonitor] ADB restart failed: {e}")
            return "adb_restart_failed", False
        success = self._relaunch_app()
        return "adb_restart_relaunch", success

    def _recover_stuck(self) -> tuple[str, bool]:
        """Swipe up → press back → relaunch if still stuck."""
        logger.info("[HealthMonitor] Recovery: NAVIGATION_STUCK — swiping + back")
        try:
            self.device.swipe("up")
            time.sleep(0.5)
            self.device.press_back()
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"[HealthMonitor] Unstick gestures failed: {e}")
        # Check if still stuck
        try:
            ui1 = self.device.get_ui_tree()
            time.sleep(STUCK_SNAPSHOT_GAP)
            ui2 = self.device.get_ui_tree()
            if ui1.strip() == ui2.strip():
                # Still stuck — relaunch
                self._force_stop_and_relaunch()
                return "stuck_gestures_relaunch", True
        except Exception:
            pass
        return "stuck_gestures", True

    def _recover_wrong_screen(self) -> tuple[str, bool]:
        """Press back to attempt to navigate away from login screen."""
        logger.info("[HealthMonitor] Recovery: WRONG_SCREEN — pressing back")
        try:
            self.device.press_back()
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"[HealthMonitor] Back press failed: {e}")
            return "back_press_failed", False
        return "press_back_login_screen", True

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _relaunch_app(self) -> bool:
        """Launch the target app. Returns True if launch command succeeded."""
        if not self.package_name:
            logger.warning("[HealthMonitor] No package_name set — cannot relaunch")
            return False
        try:
            from tools.apk_manager import launch_app
            launch_app(self.package_name, serial=self.device.serial)
            time.sleep(POST_LAUNCH_WAIT)
            return True
        except Exception as e:
            logger.error(f"[HealthMonitor] Relaunch failed: {e}")
            return False

    def _force_stop_and_relaunch(self) -> None:
        """Force-stop the app then relaunch it."""
        if not self.package_name:
            return
        try:
            from tools.apk_manager import force_stop_app
            force_stop_app(self.package_name, serial=self.device.serial)
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"[HealthMonitor] Force-stop failed: {e}")
        self._relaunch_app()

    # ------------------------------------------------------------------
    # Gap logging
    # ------------------------------------------------------------------

    def _log_gap(
        self,
        gap_type: str,
        context: str,
        recovery_action: str,
        success: bool,
    ) -> None:
        """
        Append one JSONL entry to memory/gaps_log.jsonl.

        Format:
            {
                "timestamp": "2024-01-01T12:00:00+00:00",
                "run_id": "...",
                "gap_type": "APP_CRASHED",
                "context": "scenario=Login, iteration=3",
                "recovery_action": "dismiss_crash_dialog_relaunch",
                "success": true,
                "attempts": 1
            }
        """
        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "run_id": self.run_id,
            "gap_type": gap_type,
            "context": context,
            "recovery_action": recovery_action,
            "success": success,
            "attempts": self._attempt_count,
        }
        try:
            with open(_GAP_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"[HealthMonitor] Failed to write gap log: {e}")
