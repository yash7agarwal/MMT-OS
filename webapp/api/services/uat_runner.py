"""UAT Runner — APK-driven E2E execution + Figma comparison.

This is the primary execution flow. It:

1. Installs (optional) the candidate APK on a connected device
2. Parses the Figma file → list of substantive frames with image URLs
3. Launches the app cleanly
4. For EACH Figma frame:
   a. Uses VisionNavigator to autonomously drive the app to that screen
   b. Takes a screenshot
   c. Compares the screenshot to the Figma frame via FigmaComparator
   d. Persists a UatFrameResult row
   e. Navigates back to home (or relaunches) for the next frame
5. Aggregates matched / mismatched / unreachable counts
6. Writes a markdown report under reports/uat_runs/
7. Updates the UatRun row to `completed`

Reuses existing infrastructure — no LLM calls beyond what VisionNavigator and
FigmaComparator already do. All persistence via SQLAlchemy.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy.orm import Session

# Ensure repo root is importable so we can reach agent/, tools/, utils/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from webapp.api import models
from webapp.api.services.figma_test_planner import _filter_substantive_frames

logger = logging.getLogger(__name__)

_UAT_RUNS_DIR = _REPO_ROOT / "webapp" / "data" / "uat_runs"
_REPORTS_DIR = _REPO_ROOT / "reports" / "uat_runs"
_FIGMA_CACHE_DIR = _REPO_ROOT / "webapp" / "data" / "figma_cache"
_UAT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
_FIGMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# How long a cached Figma parse is considered fresh (seconds).
# The Figma design changes rarely during an active dev session — 1 hour is generous.
_FIGMA_PARSE_TTL_SECONDS = 3600


def _cached_figma_parse(figma_file_id: str, token: str) -> dict:
    """Load the Figma file's parsed journey, using an on-disk cache to avoid
    hammering the Figma API (which has a strict per-hour rate limit).

    - Cache file: webapp/data/figma_cache/{file_id}.json
    - TTL: 1 hour
    - On 429 (rate limit): if a stale cache exists, use it with a warning instead
      of failing the whole run.
    """
    from agent.figma_journey_parser import FigmaJourneyParser

    cache_path = _FIGMA_CACHE_DIR / f"{figma_file_id}.json"
    now = time.time()

    # Fresh cache hit — skip the Figma API call entirely
    if cache_path.exists():
        age = now - cache_path.stat().st_mtime
        if age < _FIGMA_PARSE_TTL_SECONDS:
            logger.info(
                f"[UatRunner] Using cached Figma parse "
                f"(age={int(age)}s, file={figma_file_id})"
            )
            return json.loads(cache_path.read_text())

    # Cache miss or stale — try to fetch fresh
    try:
        parser = FigmaJourneyParser(figma_file_id, token=token)
        journey = parser.parse(enrich=False)
        cache_path.write_text(json.dumps(journey, default=str))
        logger.info(f"[UatRunner] Cached fresh Figma parse ({figma_file_id})")
        return journey
    except Exception as exc:
        msg = str(exc)
        # Fall back to stale cache if Figma is rate-limiting us
        if "429" in msg and cache_path.exists():
            logger.warning(
                f"[UatRunner] Figma 429 — using stale cache "
                f"(age={int(now - cache_path.stat().st_mtime)}s)"
            )
            return json.loads(cache_path.read_text())
        if "429" in msg:
            raise RuntimeError(
                "Figma API quota exhausted (429). Figma's free tier bills by "
                "'compute cost' and resets monthly. Options:\n"
                "  1. Wait a few hours and retry — the short burst cap may clear\n"
                "  2. Generate a new Figma personal access token from a different "
                "Figma account and update FIGMA_ACCESS_TOKEN in .env\n"
                "  3. Pre-populate the cache at webapp/data/figma_cache/{file_id}.json "
                "with a previously saved parse result"
            ) from exc
        raise


def run_uat(
    project_id: int,
    apk_path: Optional[str],
    figma_file_id: str,
    feature_description: Optional[str],
    db: Session,
    skip_install: bool = False,
) -> models.UatRun:
    """Execute a full UAT run and return the persisted UatRun.

    Args:
        project_id: project to attach the run to
        apk_path: path to candidate APK. If None or skip_install=True, uses
                  whatever is already installed on the device.
        figma_file_id: Figma file ID (e.g. rid4WC0zcs0yt3RjpST0dx)
        feature_description: optional free-text feature context (used in hints)
        db: SQLAlchemy session
        skip_install: if True, skip APK install even when apk_path is provided
    """
    # Create the run row up-front so the caller sees "running" state immediately
    run = models.UatRun(
        project_id=project_id,
        apk_path=apk_path,
        figma_file_id=figma_file_id,
        feature_description=feature_description,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id
    run_dir = _UAT_RUNS_DIR / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Lazy imports so the module can be loaded without these heavy deps in tests
        from agent.figma_comparator import FigmaComparator
        from tools.android_device import AndroidDevice
        from tools.apk_manager import (
            force_stop_app,
            get_apk_version,
            get_package_name,
            install_apk,
            launch_app,
        )
        from tools.vision_navigator import VisionNavigator

        figma_token = os.environ.get("FIGMA_ACCESS_TOKEN") or os.environ.get("FIGMA_API_TOKEN")
        if not figma_token:
            raise RuntimeError("FIGMA_ACCESS_TOKEN not set in .env")

        # ── Step 1: Connect to device ──────────────────────────────────────
        device = AndroidDevice()
        device.d.screen_on()
        logger.info(f"[UatRunner#{run_id}] Device connected: {device.serial}")

        # ── Step 2: Resolve + install APK ──────────────────────────────────
        package_name: Optional[str] = None
        if apk_path and not skip_install:
            logger.info(f"[UatRunner#{run_id}] Installing APK: {apk_path}")
            package_name = get_package_name(apk_path)
            install_apk(apk_path, serial=device.serial)
            apk_meta = get_apk_version(apk_path)
            run.apk_version = apk_meta.get("version_name")
            run.package_name = package_name
            db.commit()
        elif apk_path:
            # APK path given but user asked to skip install — still record metadata
            package_name = get_package_name(apk_path)
            apk_meta = get_apk_version(apk_path)
            run.apk_version = apk_meta.get("version_name")
            run.package_name = package_name
            db.commit()

        # ── Step 3: Load Figma data from local import (ZERO Figma API calls) ─
        logger.info(
            f"[UatRunner#{run_id}] Looking up local Figma import for {figma_file_id}"
        )
        figma_import = (
            db.query(models.FigmaImport)
            .filter(
                models.FigmaImport.project_id == project_id,
                models.FigmaImport.figma_file_id == figma_file_id,
                models.FigmaImport.status == "ready",
            )
            .order_by(models.FigmaImport.completed_at.desc())
            .first()
        )
        if not figma_import:
            raise RuntimeError(
                f"No ready Figma import found for file_id={figma_file_id} in this project. "
                f"Import the Figma file once via POST /api/projects/{project_id}/figma/imports "
                f"— after that, every UAT run sources data locally without hitting Figma."
            )
        logger.info(
            f"[UatRunner#{run_id}] Using FigmaImport #{figma_import.id} "
            f"('{figma_import.file_name}', {figma_import.total_frames} frames)"
        )

        # Filter to substantive frames with a local image on disk
        all_frame_rows = (
            db.query(models.FigmaFrame)
            .filter(models.FigmaFrame.import_id == figma_import.id)
            .all()
        )
        substantive = [
            f for f in all_frame_rows
            if f.image_path and os.path.exists(f.image_path) and f.frame_type == "main_screen"
        ]
        # Fallback: if no main_screen frames, accept any frame with an image
        if not substantive:
            substantive = [
                f for f in all_frame_rows
                if f.image_path and os.path.exists(f.image_path)
            ]
        logger.info(
            f"[UatRunner#{run_id}] {len(all_frame_rows)} imported frames, "
            f"{len(substantive)} substantive with local images"
        )
        run.total_frames = len(substantive)
        db.commit()

        if not substantive:
            raise RuntimeError(
                "No frames with local images in this Figma import. "
                "Re-run the import to fetch missing images."
            )

        # ── Step 4: Launch app fresh ───────────────────────────────────────
        if package_name:
            force_stop_app(package_name, serial=device.serial)
            time.sleep(0.5)
            launch_app(package_name, serial=device.serial)
            logger.info(f"[UatRunner#{run_id}] App launched: {package_name}")
            time.sleep(3)  # let the app settle

        # ── Step 5: Per-frame loop ─────────────────────────────────────────
        comparator = FigmaComparator(
            figma_file_id=figma_file_id,
            figma_token=figma_token,
            run_id=f"uat_run_{run_id}",
        )
        navigator = VisionNavigator(device, package_name=package_name)

        for i, frame in enumerate(substantive, start=1):
            frame_name = frame.name
            node_id = frame.node_id
            purpose = frame.frame_type or ""

            logger.info(
                f"[UatRunner#{run_id}] Frame {i}/{len(substantive)}: {frame_name}"
            )

            frame_result = models.UatFrameResult(
                run_id=run_id,
                figma_frame_name=frame_name,
                figma_node_id=node_id,
                figma_image_path=frame.image_path,  # already on disk from the import
                verdict="ERROR",
                issues=[],
            )
            db.add(frame_result)
            db.flush()

            t_start = time.time()
            try:
                # Figma image is already on disk thanks to the import — no download needed
                pass

                # Autonomous navigation to the target screen
                hints = f"Screen purpose: {purpose}. Feature: {feature_description or ''}"
                nav_result = navigator.navigate(
                    goal=f"Navigate to the {frame_name} screen",
                    hints=hints,
                )
                frame_result.navigation_steps = nav_result.steps_taken

                if not nav_result.success:
                    frame_result.verdict = "UNREACHABLE"
                    frame_result.issues = [
                        f"Navigator could not reach this screen: {nav_result.error or 'max steps exceeded'}"
                    ]
                    run.unreachable += 1
                    logger.warning(
                        f"[UatRunner#{run_id}]   UNREACHABLE ({nav_result.steps_taken} steps)"
                    )
                else:
                    # Screenshot and compare
                    app_shot = run_dir / f"app_{node_id.replace(':', '_')}.png"
                    device.screenshot(save_path=str(app_shot))
                    frame_result.app_screenshot_path = str(app_shot)

                    cmp_result = comparator.compare_screenshot_to_frame(
                        screenshot_path=str(app_shot),
                        figma_node_id=node_id,
                        screen_name=frame_name,
                        figma_image_path=frame_result.figma_image_path,
                    )
                    frame_result.match_score = cmp_result.get("match_score")
                    frame_result.diff_image_path = cmp_result.get("diff_image_path")
                    frame_result.issues = cmp_result.get("issues", [])

                    verdict = cmp_result.get("verdict", "UNKNOWN").upper()
                    frame_result.verdict = verdict
                    if verdict == "MATCHES":
                        run.matched += 1
                    else:
                        run.mismatched += 1
                    logger.info(
                        f"[UatRunner#{run_id}]   {verdict} ({frame_result.match_score:.2f})"
                    )
            except Exception as exc:
                logger.exception(f"[UatRunner#{run_id}] Frame {frame_name} raised")
                frame_result.verdict = "ERROR"
                frame_result.issues = [f"Runner error: {exc}"]
                run.mismatched += 1

            frame_result.elapsed_s = round(time.time() - t_start, 1)
            db.commit()

            # Reset for next frame — press back a few times, then relaunch if still far
            try:
                for _ in range(3):
                    device.press_back()
                    time.sleep(0.4)
                if package_name:
                    force_stop_app(package_name, serial=device.serial)
                    time.sleep(0.3)
                    launch_app(package_name, serial=device.serial)
                    time.sleep(2)
            except Exception as exc:
                logger.warning(f"[UatRunner#{run_id}] Reset failed: {exc}")

        # ── Step 6: Aggregate + markdown report ────────────────────────────
        if run.total_frames > 0:
            # Average match_score over the frames that actually have one
            scores = [
                fr.match_score
                for fr in run.frame_results
                if fr.match_score is not None
            ]
            run.overall_match_score = round(sum(scores) / len(scores), 3) if scores else 0.0
        report_md = _write_report_md(run)
        run.report_md_path = str(report_md)

        run.status = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        logger.info(
            f"[UatRunner#{run_id}] COMPLETED — matched={run.matched} "
            f"mismatched={run.mismatched} unreachable={run.unreachable} "
            f"score={run.overall_match_score}"
        )
        return run

    except Exception as exc:
        logger.exception(f"[UatRunner#{run_id}] Run failed")
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[:2000]}"
        run.completed_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return run


def _write_report_md(run: models.UatRun) -> Path:
    """Write a human-readable markdown report for this run.

    Does NOT embed images (paths are referenced instead). The web UI serves
    images via `/api/uat/runs/{id}/frames/{fid}/{kind}_image` endpoints.
    """
    out = _REPORTS_DIR / f"run_{run.id}.md"
    lines: list[str] = []
    lines.append(f"# UAT Run #{run.id}")
    lines.append("")
    if run.apk_version:
        lines.append(f"**APK version:** {run.apk_version}")
    if run.package_name:
        lines.append(f"**Package:** `{run.package_name}`")
    if run.figma_file_id:
        lines.append(f"**Figma:** `{run.figma_file_id}`")
    if run.feature_description:
        lines.append(f"**Feature:** {run.feature_description}")
    lines.append(f"**Started:** {run.started_at}")
    if run.completed_at:
        lines.append(f"**Completed:** {run.completed_at}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Overall match score:** {run.overall_match_score or 0:.1%}")
    lines.append(f"- **Total frames:** {run.total_frames}")
    lines.append(f"- ✅ **Matched:** {run.matched}")
    lines.append(f"- ⚠️ **Differs:** {run.mismatched}")
    lines.append(f"- ❌ **Unreachable:** {run.unreachable}")
    lines.append("")
    lines.append("## Per-frame results")
    lines.append("")
    for fr in run.frame_results:
        score_str = f"{fr.match_score:.1%}" if fr.match_score is not None else "n/a"
        lines.append(f"### {fr.figma_frame_name} · {fr.verdict} · {score_str}")
        lines.append("")
        lines.append(f"- Figma node: `{fr.figma_node_id}`")
        lines.append(f"- Navigation steps: {fr.navigation_steps}")
        lines.append(f"- Elapsed: {fr.elapsed_s or 0:.1f}s")
        if fr.figma_image_path:
            lines.append(f"- Figma image: `{fr.figma_image_path}`")
        if fr.app_screenshot_path:
            lines.append(f"- App screenshot: `{fr.app_screenshot_path}`")
        if fr.diff_image_path:
            lines.append(f"- Diff image: `{fr.diff_image_path}`")
        if fr.issues:
            lines.append("- Issues:")
            for issue in fr.issues:
                lines.append(f"  - {issue}")
        lines.append("")
    out.write_text("\n".join(lines))
    return out
