"""agent/quick_uat.py — QuickUAT fast verification runner.

Replaces the full Orchestrator pipeline for known LOB features.
Architecture: deterministic navigation → 1 screenshot → 1 Claude vision call → report.
Target: ~10–20 seconds end-to-end (vs 30–40 minutes for full Orchestrator).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from tools.android_device import AndroidDevice
from tools.apk_manager import force_stop_app, get_apk_version, get_installed_version, get_package_name, install_apk, launch_app
from tools.quick_navigator import QuickNavigator
from tools.vision_navigator import VisionNavigator
from utils.claude_client import DEFAULT_MODEL, ask_vision, _get_client
from utils.config import get

logger = logging.getLogger(__name__)

_LOB_CONFIG_PATH = Path(__file__).parent.parent / "config" / "lob_config.json"


def _load_lob_config() -> dict:
    try:
        return json.loads(_LOB_CONFIG_PATH.read_text())
    except Exception:
        return {}


def _resolve_lob(feature_description: str, lob_config: dict) -> tuple[str | None, dict | None]:
    """Return (lob_name, lob_entry) for the best keyword match, or (None, None)."""
    feature_lower = feature_description.lower()
    best_lob, best_score = None, 0
    for lob_name, entry in lob_config.items():
        score = sum(1 for kw in entry.get("keywords", []) if kw in feature_lower)
        if score > best_score:
            best_score, best_lob = score, lob_name
    return (best_lob, lob_config[best_lob]) if best_lob and best_score > 0 else (None, None)


class QuickUATRunner:
    """
    Fast-path UAT runner for known LOB features.

    Usage:
        runner = QuickUATRunner(
            candidate_apk="candidate.apk",
            feature_description="hotel details page",
            acceptance_criteria="Hotel Details Page shows photos, amenities, price, and Book Now button",
        )
        result = runner.run()
        # result["verdict"] == "PASS" | "FAIL" | "ERROR"
    """

    def __init__(
        self,
        feature_description: str,
        acceptance_criteria: str,
        candidate_apk: str | None = None,
        skip_install: bool = False,
        manual_nav: bool = False,
        nav_mode: str = "vision",
    ):
        self.feature_description = feature_description
        self.acceptance_criteria = acceptance_criteria
        self.candidate_apk = candidate_apk
        self.skip_install = skip_install
        self.manual_nav = manual_nav
        self.nav_mode = nav_mode  # "vision" | "deterministic" | "auto"
        self.evidence_dir = Path(get("uat.evidence_dir", ".tmp/evidence"))
        self.reports_dir = Path(get("uat.reports_dir", "reports"))

    def run(self) -> dict:
        t0 = time.time()

        # 1. Resolve LOB
        lob_config = _load_lob_config()
        lob_name, lob_entry = _resolve_lob(self.feature_description, lob_config)
        if not lob_entry:
            return {
                "verdict": "ERROR",
                "reason": f"No LOB matched '{self.feature_description}'. "
                          f"Known LOBs: {', '.join(lob_config.keys())}",
            }
        quick_steps = lob_entry.get("quick_steps", [])
        logger.info(
            f"[QuickUAT] LOB: {lob_name} | Steps: {len(quick_steps)} | "
            f"skip_install={self.skip_install}"
        )

        # 2. Connect device and ensure screen is on
        device = AndroidDevice()
        device.d.screen_on()  # wake screen before any interaction

        # 3. Install / skip install — always resolve package_name for launch
        package_name = None
        if self.candidate_apk:
            package_name = get_package_name(self.candidate_apk)
        else:
            # No APK provided — fall back to app_package from LOB config
            package_name = lob_entry.get("app_package")

        if self.skip_install or not self.candidate_apk:
            logger.info("[QuickUAT] Skipping install — using whatever is on device")
        else:
            apk_version = get_apk_version(self.candidate_apk)
            installed_ver = get_installed_version(package_name, serial=device.serial)
            if installed_ver == apk_version.get("version_name"):
                logger.info(
                    f"[QuickUAT] Same version ({installed_ver}) already installed — skipping install"
                )
            else:
                logger.info(f"[QuickUAT] Installing {self.candidate_apk}...")
                install_apk(self.candidate_apk, serial=device.serial)
                logger.info("[QuickUAT] Install complete")

        # 4. Launch app — skip force-stop for warm starts (avoids 30s+ splash freeze)
        if package_name:
            if not self.skip_install and self.candidate_apk:
                # Fresh install → force-stop for clean state
                force_stop_app(package_name, serial=device.serial)
                time.sleep(0.5)
            launch_app(package_name, serial=device.serial)
        # Brief settle — vision navigator will detect splash/loading and wait if needed
        time.sleep(1)

        # 5. Navigate to target screen
        if self.manual_nav:
            logger.info("[QuickUAT] Manual navigation mode — navigate to the target screen")
            input("[QuickUAT] Press Enter when you're on the target screen...")
        elif self.nav_mode == "vision" or self.nav_mode == "auto":
            # Vision-guided navigation (generic, works for any LOB)
            hints = lob_entry.get("vision_hints", "") if lob_entry else ""
            goal = f"Navigate to: {self.feature_description}"
            logger.info(f"[QuickUAT] Vision navigation to {self.feature_description}...")
            vnav = VisionNavigator(device, package_name=package_name)
            vnav_result = vnav.navigate(goal=goal, hints=hints)
            if not vnav_result.success:
                return {
                    "verdict": "ERROR",
                    "reason": f"Vision navigation failed after {vnav_result.steps_taken} steps: {vnav_result.error}",
                }
            logger.info(f"[QuickUAT] Navigation complete ({vnav_result.steps_taken} steps, {vnav_result.elapsed_s}s)")
        else:
            # Deterministic fallback
            logger.info(f"[QuickUAT] Deterministic navigation to {self.feature_description}...")
            navigator = QuickNavigator(device)
            nav_result = navigator.navigate(quick_steps)
            if not nav_result.success:
                return {
                    "verdict": "ERROR",
                    "reason": f"Navigation failed at step '{nav_result.last_step}': {nav_result.error}",
                }

        # 6. Screenshot
        slug = re.sub(r"[^a-z0-9]+", "_", self.feature_description.lower())[:40]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        sc_dir = self.evidence_dir / "quick_uat"
        sc_dir.mkdir(parents=True, exist_ok=True)
        sc_path = sc_dir / f"{slug}_{ts}.png"
        device.screenshot(save_path=str(sc_path))
        logger.info(f"[QuickUAT] Screenshot saved: {sc_path}")

        # 7. Claude vision verification (1 API call)
        logger.info("[QuickUAT] Verifying screenshot with Claude vision...")
        verdict_data = self._verify(str(sc_path))

        # 8. Figma benchmarking
        logger.info("[QuickUAT] Running Figma benchmarking...")
        figma_result = self._benchmark_against_figma(str(sc_path), slug)

        # 9. Write report
        elapsed = round(time.time() - t0, 1)
        report = {
            "lob": lob_name,
            "feature": self.feature_description,
            "criteria": self.acceptance_criteria,
            "verdict": verdict_data.get("verdict", "ERROR"),
            "reason": verdict_data.get("reason", ""),
            "figma": figma_result,
            "screenshot": str(sc_path),
            "elapsed_s": elapsed,
            "timestamp": ts,
        }
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.reports_dir / f"quick_uat_{slug}_{ts}.json"
        report_path.write_text(json.dumps(report, indent=2))
        report["report_path"] = str(report_path)

        verdict = report["verdict"]
        reason = report["reason"]
        figma_verdict = figma_result.get("verdict", figma_result.get("reason", "skipped"))
        logger.info(f"[QuickUAT] {verdict} — {reason}")
        logger.info(f"[QuickUAT] Figma: {figma_verdict}")
        logger.info(f"[QuickUAT] Total: {elapsed}s | Report: {report_path}")
        return report

    def _benchmark_against_figma(self, screenshot_path: str, run_slug: str) -> dict:
        """Compare screenshot against Figma design. Returns comparison result dict."""
        token = os.environ.get("FIGMA_ACCESS_TOKEN") or os.environ.get("FIGMA_API_TOKEN")
        if not token:
            return {"skipped": True, "reason": "FIGMA_ACCESS_TOKEN not set in .env"}

        figma_file_id = "rid4WC0zcs0yt3RjpST0dx"
        try:
            from agent.figma_journey_parser import FigmaJourneyParser
            parser = FigmaJourneyParser(file_id=figma_file_id, token=token)
            journey = parser.parse()

            # Find best-matching Hotel Details frame
            target_screen = None
            keywords = ["detail", "pdp", "property", "hotel"]
            for screen in journey.get("all_screens", []):
                name_lower = screen.get("name", "").lower()
                if any(kw in name_lower for kw in keywords):
                    target_screen = screen
                    break
            if not target_screen:
                screens = journey.get("main_screens") or journey.get("all_screens", [])
                target_screen = screens[0] if screens else None
            if not target_screen:
                return {"skipped": True, "reason": "No matching screen found in Figma file"}

            node_id = target_screen["node_id"]
            screen_name = target_screen.get("name", "unknown")
            logger.info(f"[QuickUAT] Figma frame: '{screen_name}' (node_id={node_id})")

            from agent.figma_comparator import FigmaComparator
            comparator = FigmaComparator(
                figma_file_id=figma_file_id,
                figma_token=token,
                run_id=run_slug,
            )
            result = comparator.compare_screenshot_to_frame(
                screenshot_path=screenshot_path,
                figma_node_id=node_id,
                screen_name=screen_name,
            )
            logger.info(
                f"[QuickUAT] Figma comparison: {result.get('verdict')} "
                f"(match_score={result.get('match_score', 0):.2f})"
            )
            return result
        except Exception as exc:
            logger.warning(f"[QuickUAT] Figma benchmarking failed: {exc}")
            return {"skipped": True, "reason": str(exc)}

    def _verify(self, screenshot_path: str) -> dict:
        """Send screenshot + criteria to Claude vision. Returns {verdict, reason}."""
        prompt = (
            f"You are a mobile QA engineer verifying a screenshot.\n\n"
            f"Feature: {self.feature_description}\n"
            f"Acceptance criteria: {self.acceptance_criteria}\n\n"
            "Look at the screenshot and answer:\n"
            "1. PASS or FAIL\n"
            "2. One sentence reason\n\n"
            'Return JSON only: {"verdict": "PASS" or "FAIL", "reason": "..."}'
        )
        try:
            with open(screenshot_path, "rb") as fh:
                image_bytes = fh.read()
            raw = ask_vision(
                prompt=prompt,
                image_bytes=image_bytes,
                model=DEFAULT_MODEL,
                max_tokens=256,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(raw)
        except Exception as exc:
            return {"verdict": "ERROR", "reason": str(exc)}
