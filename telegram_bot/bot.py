"""
telegram_bot/bot.py — Telegram bot interface for MMT-OS UAT automation

Commands:
    /start   — welcome message + list commands
    /run     — start a UAT run (uses ./apks/candidate.apk or prompts upload)
    /status  — current run status
    /report  — send latest report
    /list    — list recent runs with pass rates
    /cases   — list registered use cases for a feature
    /help    — show all commands

APK uploads: send a .apk document; bot saves to ./apks/candidate.apk.
UAT runs execute in a background thread so the bot event loop stays responsive.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Document, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths / env
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APKS_DIR = _REPO_ROOT / "apks"
_CANDIDATE_APK = _APKS_DIR / "candidate.apk"
_REPORTS_DIR = _REPO_ROOT / "reports"

_DEFAULT_ACCOUNTS_FILE = os.getenv("UAT_ACCOUNTS_FILE", str(_REPO_ROOT / "accounts.json"))
_DEFAULT_FEATURE = os.getenv("UAT_FEATURE", "search")

# ---------------------------------------------------------------------------
# RunTracker — in-memory state
# ---------------------------------------------------------------------------


class RunTracker:
    """Thread-safe store of UAT run state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}   # run_id -> {status, result, error, start_time, chat_id}
        self._latest_run_id: Optional[str] = None

    def start(self, run_id: str, chat_id: int) -> None:
        with self._lock:
            self._runs[run_id] = {
                "status": "running",
                "result": None,
                "error": None,
                "start_time": time.time(),
                "chat_id": chat_id,
            }
            self._latest_run_id = run_id

    def complete(self, run_id: str, result: dict) -> None:
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id]["status"] = "completed"
                self._runs[run_id]["result"] = result

    def fail(self, run_id: str, error: str) -> None:
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id]["status"] = "failed"
                self._runs[run_id]["error"] = error

    def get(self, run_id: str) -> Optional[dict]:
        with self._lock:
            return self._runs.get(run_id)

    def latest(self) -> Optional[dict]:
        with self._lock:
            if self._latest_run_id:
                return self._runs.get(self._latest_run_id)
            return None

    def latest_run_id(self) -> Optional[str]:
        with self._lock:
            return self._latest_run_id

    def all_runs(self) -> list[dict]:
        with self._lock:
            return [
                {"run_id": rid, **info}
                for rid, info in self._runs.items()
            ]

    def is_running(self) -> bool:
        with self._lock:
            return any(
                info["status"] == "running" for info in self._runs.values()
            )


_tracker = RunTracker()

# ---------------------------------------------------------------------------
# Figma UAT state — tracks chats awaiting a Figma URL after APK upload
# ---------------------------------------------------------------------------

# {chat_id: {"apk_path": str, "state": "waiting_figma_url"}}
_pending_figma: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Background UAT runner
# ---------------------------------------------------------------------------


def _run_uat_in_background(
    run_id: str,
    feature: str,
    accounts_file: str,
    chat_id: int,
    app,          # The Application instance — used to queue send_message callbacks
) -> None:
    """Execute UAT in a worker thread and notify the user when done."""
    try:
        # Lazy import to avoid circular deps and slow startup
        from agent.orchestrator import Orchestrator  # noqa: PLC0415

        # Load accounts from JSON file
        accounts_path = Path(accounts_file)
        if accounts_path.exists():
            with open(accounts_path) as f:
                accounts = json.load(f)
        else:
            accounts = []

        orchestrator = Orchestrator(
            candidate_apk=str(_CANDIDATE_APK),
            feature_description=feature,
            accounts=accounts,
            run_id=run_id,
        )
        result = orchestrator.run()
        _tracker.complete(run_id, result)

        # Build notification message
        passed = result.get("passed", 0)
        failed = result.get("failed", 0)
        total = result.get("total", passed + failed)
        pass_rate = f"{round(passed / total * 100)}%" if total else "N/A"

        msg = (
            f"*UAT Run Complete* ✓\n"
            f"Run ID: `{run_id}`\n"
            f"Feature: {feature}\n"
            f"Result: {passed}/{total} passed ({pass_rate})\n\n"
            f"Use /report to fetch the full report."
        )

        # Also try to generate the report file
        try:
            report_path = orchestrator.generate_report()
            if report_path:
                msg += f"\nReport saved: `{Path(report_path).name}`"
        except Exception as rep_err:
            logger.warning(f"[bot] generate_report failed: {rep_err}")

    except Exception as exc:
        _tracker.fail(run_id, str(exc))
        msg = (
            f"*UAT Run Failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Error: {exc}"
        )
        logger.exception(f"[bot] UAT run {run_id} raised an exception")

    # Queue the notification back onto the event loop
    import asyncio  # noqa: PLC0415

    async def _notify() -> None:
        await app.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN,
        )

    asyncio.run_coroutine_threadsafe(_notify(), app.loop)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*Prism — Product Intelligence Bot*\n\n"
        "Track competitors, research your industry, and run UAT — all from here.\n\n"
        "*Get started:*\n"
        "/new `Name — description` — create a new product and start research\n\n"
        "*Intelligence:*\n"
        "/intel status — check agent progress\n"
        "/intel competitors — see discovered competitors\n"
        "/intel ask `<question>` — query the knowledge base\n"
        "/intel run `<agent>` — run an agent manually\n"
        "/intel digest — get a daily summary\n\n"
        "*UAT:*\n"
        "/run `<feature>` — start a UAT run\n"
        "/run\\_figma `<figma_url>` — Figma-first UAT\n"
        "/builds — list APK builds\n"
        "/status — current run status\n"
        "/report — latest report\n\n"
        "Send a `.apk` file to upload a build."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if _tracker.is_running():
        await update.message.reply_text(
            "A UAT run is already in progress. Use /status to check it."
        )
        return

    # Feature from args or env default
    feature = " ".join(context.args).strip() if context.args else _DEFAULT_FEATURE

    # Check APK exists
    if not _CANDIDATE_APK.exists():
        await update.message.reply_text(
            f"No candidate APK found at `{_CANDIDATE_APK}`.\n"
            "Please upload a `.apk` file as a document first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    run_id = f"run_{uuid.uuid4().hex[:8]}"
    _tracker.start(run_id, chat_id)

    await update.message.reply_text(
        f"*Starting UAT run*\n"
        f"Run ID: `{run_id}`\n"
        f"Feature: {feature}\n\n"
        "I'll notify you when it's done.",
        parse_mode=ParseMode.MARKDOWN,
    )

    thread = threading.Thread(
        target=_run_uat_in_background,
        args=(run_id, feature, _DEFAULT_ACCOUNTS_FILE, chat_id, context.application),
        daemon=True,
        name=f"uat-{run_id}",
    )
    thread.start()


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    latest = _tracker.latest()

    if not latest:
        await update.message.reply_text("No UAT runs recorded yet. Use /run to start one.")
        return

    run_id = _tracker.latest_run_id()
    status = latest["status"]
    elapsed = round(time.time() - latest["start_time"])

    if status == "running":
        msg = (
            f"*Run in progress*\n"
            f"Run ID: `{run_id}`\n"
            f"Elapsed: {elapsed}s"
        )
    elif status == "completed":
        result = latest.get("result") or {}
        passed = result.get("passed", 0)
        total = result.get("total", 0)
        pass_rate = f"{round(passed / total * 100)}%" if total else "N/A"
        msg = (
            f"*Last run completed*\n"
            f"Run ID: `{run_id}`\n"
            f"Pass rate: {pass_rate} ({passed}/{total})\n"
            f"Duration: {elapsed}s"
        )
    else:  # failed
        error = latest.get("error", "unknown error")
        msg = (
            f"*Last run failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Error: {error}"
        )

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    run_id = _tracker.latest_run_id()

    if not run_id:
        await update.message.reply_text("No runs yet. Use /run to start one.")
        return

    # Look for markdown report file first
    report_path = _REPORTS_DIR / f"uat_report_{run_id}.md"
    summary_path = _REPORTS_DIR / f"run_summary_{run_id}.json"

    if report_path.exists():
        content = report_path.read_text(encoding="utf-8")
        if len(content) <= 4000:
            await update.message.reply_text(
                f"```\n{content}\n```", parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Send truncated preview + full file as document
            preview = content[:3800]
            await update.message.reply_text(
                f"*Report preview (truncated):*\n```\n{preview}\n...\n```",
                parse_mode=ParseMode.MARKDOWN,
            )
            with open(report_path, "rb") as fh:
                await update.message.reply_document(
                    document=fh,
                    filename=report_path.name,
                    caption=f"Full report for run `{run_id}`",
                    parse_mode=ParseMode.MARKDOWN,
                )

    elif summary_path.exists():
        # Fall back to JSON summary
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        content = json.dumps(data, indent=2)
        if len(content) <= 4000:
            await update.message.reply_text(
                f"*Run Summary (`{run_id}`):*\n```json\n{content}\n```",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"Summary too large. Key stats:\n"
                f"Passed: {data.get('passed', 'N/A')}\n"
                f"Failed: {data.get('failed', 'N/A')}\n"
                f"Total: {data.get('total', 'N/A')}",
            )
    else:
        latest = _tracker.latest()
        if latest and latest["status"] == "running":
            await update.message.reply_text("Run still in progress. Check back with /report once done.")
        else:
            await update.message.reply_text(
                f"No report file found for run `{run_id}`.\n"
                "The orchestrator may not have saved one yet.",
                parse_mode=ParseMode.MARKDOWN,
            )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runs = _tracker.all_runs()
    if not runs:
        await update.message.reply_text("No runs recorded this session.")
        return

    lines = ["*Recent UAT Runs:*\n"]
    for run in sorted(runs, key=lambda r: r["start_time"], reverse=True)[:10]:
        rid = run["run_id"]
        status = run["status"]
        result = run.get("result") or {}
        passed = result.get("passed", 0)
        total = result.get("total", 0)
        pass_rate = f"{round(passed / total * 100)}%" if total else "—"

        status_icon = {"running": "🔄", "completed": "✅", "failed": "❌"}.get(status, "?")
        lines.append(f"{status_icon} `{rid}` — {status} | pass {pass_rate}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_cases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    feature = " ".join(context.args).strip() if context.args else _DEFAULT_FEATURE

    # Attempt to load use cases from the project's use-case registry
    use_cases_path = _REPO_ROOT / "config" / "use_cases.json"
    if not use_cases_path.exists():
        await update.message.reply_text(
            f"No use-case registry found at `{use_cases_path}`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        registry = json.loads(use_cases_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        await update.message.reply_text(f"Could not parse use-case registry: {exc}")
        return

    # Registry can be a dict keyed by feature, or a list
    if isinstance(registry, dict):
        cases = registry.get(feature, registry.get(feature.lower(), []))
    elif isinstance(registry, list):
        cases = [c for c in registry if c.get("feature", "").lower() == feature.lower()]
    else:
        cases = []

    if not cases:
        await update.message.reply_text(
            f"No use cases found for feature `{feature}`.\n"
            "Check /list for available features.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [f"*Use cases for `{feature}`:*\n"]
    for i, case in enumerate(cases, 1):
        if isinstance(case, dict):
            name = case.get("name", case.get("id", f"case_{i}"))
            desc = case.get("description", "")
            lines.append(f"{i}. *{name}*" + (f" — {desc}" if desc else ""))
        else:
            lines.append(f"{i}. {case}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# APK build management commands
# ---------------------------------------------------------------------------

_BUILDS_DIR = _REPO_ROOT / ".tmp" / "builds"

# Cached numbered list from last /builds call, keyed by chat_id
_builds_cache: dict[int, list[Path]] = {}


async def cmd_builds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/builds — List available APK builds from .tmp/builds/."""
    if not _BUILDS_DIR.exists():
        await update.message.reply_text(
            "No builds directory found. Place APKs in `.tmp/builds/`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    apks = sorted(_BUILDS_DIR.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not apks:
        await update.message.reply_text("No APK files found in `.tmp/builds/`.", parse_mode=ParseMode.MARKDOWN)
        return

    chat_id = update.effective_chat.id
    _builds_cache[chat_id] = apks

    lines = ["*Available APK Builds:*\n"]
    for i, apk in enumerate(apks, 1):
        size_mb = apk.stat().st_size / (1024 * 1024)
        lines.append(f"`{i}.` {apk.name} ({size_mb:.0f} MB)")

    lines.append(f"\nUse /use\\_build `<number>` to select one.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_use_build(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/use_build <number> — Select an APK from the /builds list."""
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("Usage: /use_build `<number>`\nRun /builds first to see the list.", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        idx = int(context.args[0]) - 1
    except ValueError:
        await update.message.reply_text("Please provide a valid number.")
        return

    cached = _builds_cache.get(chat_id, [])
    if not cached:
        await update.message.reply_text("Run /builds first to list available APKs.")
        return

    if idx < 0 or idx >= len(cached):
        await update.message.reply_text(f"Invalid selection. Choose 1–{len(cached)}.")
        return

    selected = cached[idx]
    _APKS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(selected), str(_CANDIDATE_APK))
    size_mb = selected.stat().st_size / (1024 * 1024)

    await update.message.reply_text(
        f"Selected: `{selected.name}` ({size_mb:.0f} MB)\n"
        f"Copied to `candidate.apk`. Use /run to start UAT.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_upload_local(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/upload_local <path> — Load an APK from a local filesystem path."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /upload\\_local `<path>`\n"
            "Example: /upload\\_local /path/to/app.apk",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    path = Path(" ".join(context.args).strip())
    if not path.exists():
        await update.message.reply_text(f"File not found: `{path}`", parse_mode=ParseMode.MARKDOWN)
        return

    if not str(path).lower().endswith(".apk"):
        await update.message.reply_text("Only `.apk` files are accepted.", parse_mode=ParseMode.MARKDOWN)
        return

    _APKS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(path), str(_CANDIDATE_APK))
    size_mb = path.stat().st_size / (1024 * 1024)

    await update.message.reply_text(
        f"APK loaded from local path ({size_mb:.0f} MB).\n"
        f"Saved as `candidate.apk`. Use /run to start UAT.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# APK upload handler
# ---------------------------------------------------------------------------

_TELEGRAM_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024  # 20 MB


# ---------------------------------------------------------------------------
# AppUAT photo upload — forward Telegram photos to webapp screen-mapping API
# ---------------------------------------------------------------------------

import httpx

_APPUAT_API = os.getenv("APPUAT_API_URL", "http://localhost:8000")
_APPUAT_STATE_FILE = _REPO_ROOT / "webapp" / "data" / "telegram_state.json"


def _load_telegram_state() -> dict:
    if _APPUAT_STATE_FILE.exists():
        try:
            return json.loads(_APPUAT_STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_telegram_state(state: dict) -> None:
    _APPUAT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _APPUAT_STATE_FILE.write_text(json.dumps(state, indent=2))


def _get_active_project(chat_id: int) -> int | None:
    state = _load_telegram_state()
    return state.get(str(chat_id), {}).get("active_project_id")


def _set_active_project(chat_id: int, project_id: int) -> None:
    state = _load_telegram_state()
    state.setdefault(str(chat_id), {})["active_project_id"] = project_id
    _save_telegram_state(state)


async def cmd_appuat_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List AppUAT projects from the webapp."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_APPUAT_API}/api/projects")
            r.raise_for_status()
            projects = r.json()
    except Exception as exc:
        await update.message.reply_text(
            f"Couldn't reach AppUAT API at {_APPUAT_API}: {exc}\n\n"
            f"Make sure the backend is running."
        )
        return

    if not projects:
        await update.message.reply_text(
            "No projects yet. Create one at http://localhost:3000"
        )
        return

    active = _get_active_project(update.effective_chat.id)
    lines = ["*Your AppUAT projects:*", ""]
    for p in projects:
        marker = " ← active" if p["id"] == active else ""
        lines.append(f"`{p['id']}` *{p['name']}*{marker}")
        if p.get("app_package"):
            lines.append(f"   `{p['app_package']}`")
    lines.append("")
    lines.append("Use `/setproject <id>` to choose where photos go.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_appuat_setproject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set the active project for photo uploads in this chat."""
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setproject <project_id>`\n\nUse /projects to list IDs.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Project id must be a number.")
        return

    # Verify it exists
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_APPUAT_API}/api/projects/{pid}")
            if r.status_code == 404:
                await update.message.reply_text(f"Project {pid} not found.")
                return
            r.raise_for_status()
            project = r.json()
    except Exception as exc:
        await update.message.reply_text(f"Couldn't reach AppUAT API: {exc}")
        return

    _set_active_project(update.effective_chat.id, pid)
    await update.message.reply_text(
        f"✓ Active project set to *{project['name']}* (id `{pid}`).\n\n"
        f"Now send me screenshots — I'll auto-upload them and Claude will analyze each one.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_appuat_uat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a draft test plan for the active project from a feature description.

    Usage: /uat <feature description>
    Example: /uat hotel details page launched with photos, amenities, price, and Book Now button
    """
    chat_id = update.effective_chat.id
    project_id = _get_active_project(chat_id)
    if project_id is None:
        await update.message.reply_text(
            "No active project. Use /projects to list and /setproject `<id>`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/uat <feature description>`\n\n"
            "Example:\n"
            "`/uat hotel details page launched with photos, amenities, price, and Book Now button`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    feature_description = " ".join(context.args)
    await update.message.reply_text(
        f"Generating UAT plan for:\n_{feature_description}_\n\nThinking…",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{_APPUAT_API}/api/projects/{project_id}/plans",
                json={"feature_description": feature_description},
            )
            if r.status_code == 400:
                await update.message.reply_text(
                    "Couldn't generate plan: no screens uploaded for this project yet.\n"
                    "Send some screenshots first!"
                )
                return
            r.raise_for_status()
            plan = r.json()
    except Exception as exc:
        await update.message.reply_text(f"Plan generation failed: {exc}")
        return

    cases = plan.get("cases", [])
    if not cases:
        await update.message.reply_text(
            "Plan generated but no cases were produced. The screen graph may be too sparse — upload more screens and try again."
        )
        return

    # Group cases by branch_label for readability
    by_branch: dict[str, list[dict]] = {}
    for c in cases:
        by_branch.setdefault(c.get("branch_label") or "default", []).append(c)

    lines = [
        f"*Test plan #{plan['id']}* — {len(cases)} case{'s' if len(cases) != 1 else ''}",
        "",
    ]
    for branch, branch_cases in by_branch.items():
        lines.append(f"_{branch}_")
        for c in branch_cases:
            lines.append(f"  • {c['title']}")
        lines.append("")
    lines.append(f"Review & approve at:")
    lines.append(f"http://localhost:3000/projects/{project_id}/plans/{plan['id']}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_appuat_uat_suite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a comprehensive UAT suite (all plan types) for the active project.

    Usage:
        /uatsuite <feature description>
        /uatsuite <feature description> figma=<file_id>
    """
    chat_id = update.effective_chat.id
    project_id = _get_active_project(chat_id)
    if project_id is None:
        await update.message.reply_text(
            "No active project. Use /projects and /setproject `<id>`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/uatsuite <feature description>`\n\n"
            "Optional: append `figma=<file_id>` to include design-fidelity plan.\n\n"
            "Example:\n"
            "`/uatsuite hotel details page with new design figma=rid4WC0zcs0yt3RjpST0dx`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Parse optional figma=<id> suffix
    figma_file_id: str | None = None
    parts = list(context.args)
    for p in list(parts):
        if p.startswith("figma="):
            figma_file_id = p.split("=", 1)[1]
            parts.remove(p)
    feature_description = " ".join(parts).strip()
    if not feature_description:
        await update.message.reply_text("Please provide a feature description.")
        return

    figma_hint = f" with Figma {figma_file_id}" if figma_file_id else ""
    await update.message.reply_text(
        f"Generating UAT suite{figma_hint} for:\n_{feature_description}_\n\n"
        f"This runs 3-4 specialized planners (functional flow, deeplink integrity, edge cases"
        f"{', design fidelity' if figma_file_id else ''}). Thinking…",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        async with httpx.AsyncClient(timeout=600) as client:
            body: dict = {"feature_description": feature_description}
            if figma_file_id:
                body["figma_file_id"] = figma_file_id
            r = await client.post(
                f"{_APPUAT_API}/api/projects/{project_id}/plans/suite",
                json=body,
            )
            if r.status_code == 400:
                await update.message.reply_text("No screens uploaded for this project yet. Send screenshots first.")
                return
            r.raise_for_status()
            plans = r.json()
    except Exception as exc:
        await update.message.reply_text(f"Suite generation failed: {exc}")
        return

    if not plans:
        await update.message.reply_text("Suite ran but no plans were generated. Check backend logs.")
        return

    total_cases = sum(len(p.get("cases") or []) for p in plans)
    lines = [
        f"*UAT Suite* — {len(plans)} plans, {total_cases} cases total",
        "",
    ]
    for p in plans:
        n = len(p.get("cases") or [])
        lines.append(f"• *Plan #{p['id']}* — `{p['plan_type']}` ({n} cases)")
        lines.append(f"  http://localhost:3000/projects/{project_id}/plans/{p['id']}")
    lines.append("")
    lines.append(f"Review all: http://localhost:3000/projects/{project_id}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a photo from Telegram and upload it to the active AppUAT project."""
    if not update.message or not update.message.photo:
        return

    chat_id = update.effective_chat.id
    project_id = _get_active_project(chat_id)
    if project_id is None:
        await update.message.reply_text(
            "No active project set. Use /projects to list and /setproject `<id>` to choose one.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Telegram photos arrive as multiple sizes; pick the largest
    photo = update.message.photo[-1]
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await tg_file.download_to_memory(photo_bytes_io)
        photo_bytes = photo_bytes_io.getvalue()
    except Exception as exc:
        await update.message.reply_text(f"Failed to download photo: {exc}")
        return

    filename = f"telegram_{photo.file_unique_id}.jpg"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{_APPUAT_API}/api/projects/{project_id}/screens/bulk",
                files={"files": (filename, photo_bytes, "image/jpeg")},
            )
            r.raise_for_status()
            screens = r.json()
    except Exception as exc:
        await update.message.reply_text(f"Upload failed: {exc}")
        return

    if not screens:
        await update.message.reply_text("Upload succeeded but no screens returned.")
        return

    s = screens[0]
    elements_count = len(s.get("elements") or [])
    await update.message.reply_text(
        f"✓ *{s.get('display_name') or s['name']}*\n"
        f"_{(s.get('purpose') or '')[:120]}_\n"
        f"`{elements_count}` interactive element{'s' if elements_count != 1 else ''} detected\n\n"
        f"Open: http://localhost:3000/projects/{project_id}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept .apk file uploads and save to ./apks/candidate.apk."""
    doc: Document = update.message.document
    if not doc:
        return

    filename = doc.file_name or ""
    if not filename.lower().endswith(".apk"):
        await update.message.reply_text(
            "Only `.apk` files are accepted. Please send a valid APK.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Check file size before attempting download
    if doc.file_size and doc.file_size > _TELEGRAM_DOWNLOAD_LIMIT_BYTES:
        size_mb = doc.file_size / (1024 * 1024)
        await update.message.reply_text(
            f"APK is too large for Telegram download ({size_mb:.0f} MB).\n"
            f"Telegram limits bot file downloads to 20 MB.\n\n"
            f"*Alternatives:*\n"
            f"• /builds — list APKs from local builds directory\n"
            f"• /upload\\_local `<path>` — load from a local path\n\n"
            f"Place your APK in `.tmp/builds/` and use /builds.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        f"Downloading `{filename}` ({doc.file_size // 1024} KB)...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        _APKS_DIR.mkdir(parents=True, exist_ok=True)
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(_CANDIDATE_APK))

        await update.message.reply_text(
            f"APK saved as `candidate.apk`.\n"
            f"Use /run to start a UAT run.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        logger.exception("[bot] APK download failed")
        await update.message.reply_text(f"Failed to save APK: {exc}")
        return

    # Offer Figma-first UAT mode
    chat_id = update.effective_chat.id
    _pending_figma[chat_id] = {
        "apk_path": str(_CANDIDATE_APK),
        "state": "waiting_figma_url",
    }
    await update.message.reply_text(
        f"*APK saved!* To test against a Figma design:\n\n"
        f"• Send me the Figma file URL (figma.com/...)\n"
        f"• Or use /run `<feature>` for standard UAT",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# Text message handler — Figma URL detection
# ---------------------------------------------------------------------------


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect Figma URLs in plain text messages and trigger Figma UAT if pending."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Check if this looks like a Figma URL
    if "figma.com" not in text.lower():
        return  # not a Figma URL — ignore (let other handlers deal with it)

    pending = _pending_figma.get(chat_id)
    if not pending or pending.get("state") != "waiting_figma_url":
        await update.message.reply_text(
            "Figma URL detected, but no APK is pending for this chat.\n"
            "Please upload an APK first, then send the Figma URL.",
        )
        return

    # Clear pending state and trigger Figma UAT
    apk_path = pending["apk_path"]
    del _pending_figma[chat_id]

    await _start_figma_uat(
        update=update,
        context=context,
        figma_url=text,
        apk_path=apk_path,
        chat_id=chat_id,
    )


# ---------------------------------------------------------------------------
# /run_figma command handler
# ---------------------------------------------------------------------------


async def cmd_run_figma(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/run_figma <figma_url> — Run Figma-first UAT with the current candidate APK."""
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "Usage: /run_figma `<figma_url>`\n"
            "Example: /run_figma https://www.figma.com/design/ABC123/MyApp",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    figma_url = context.args[0].strip()
    if "figma.com" not in figma_url.lower():
        await update.message.reply_text(
            "That doesn't look like a valid Figma URL. "
            "Expected: https://www.figma.com/design/<FILE_ID>/name"
        )
        return

    if not _CANDIDATE_APK.exists():
        await update.message.reply_text(
            f"No candidate APK found at `{_CANDIDATE_APK}`.\n"
            "Please upload a `.apk` file as a document first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Clear any lingering pending state
    _pending_figma.pop(chat_id, None)

    await _start_figma_uat(
        update=update,
        context=context,
        figma_url=figma_url,
        apk_path=str(_CANDIDATE_APK),
        chat_id=chat_id,
    )


async def _start_figma_uat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    figma_url: str,
    apk_path: str,
    chat_id: int,
) -> None:
    """Shared helper: validate URL, show confirmation, then kick off background run."""
    if _tracker.is_running():
        await update.message.reply_text(
            "A UAT run is already in progress. Use /status to check it."
        )
        return

    run_id = f"figma_{uuid.uuid4().hex[:8]}"
    _tracker.start(run_id, chat_id)

    await update.message.reply_text(
        f"*Starting Figma UAT*\n"
        f"Run ID: `{run_id}`\n"
        f"Figma URL: {figma_url}\n\n"
        "Parsing Figma file... I'll update you with progress.",
        parse_mode=ParseMode.MARKDOWN,
    )

    thread = threading.Thread(
        target=_run_figma_uat_in_background,
        args=(run_id, figma_url, apk_path, chat_id, context.application),
        daemon=True,
        name=f"figma-uat-{run_id}",
    )
    thread.start()


# ---------------------------------------------------------------------------
# Background Figma UAT runner
# ---------------------------------------------------------------------------


def _run_figma_uat_in_background(
    run_id: str,
    figma_url: str,
    apk_path: str,
    chat_id: int,
    app,
) -> None:
    """Execute Figma-first UAT in a worker thread and notify when done."""
    import asyncio  # noqa: PLC0415

    async def _send(text: str) -> None:
        await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )

    def _notify(text: str) -> None:
        asyncio.run_coroutine_threadsafe(_send(text), app.loop)

    try:
        from agent.orchestrator import Orchestrator  # noqa: PLC0415

        result = Orchestrator.run_figma_uat(
            figma_url=figma_url,
            apk_path=apk_path,
            accounts=[],
            run_id=run_id,
            notify=_notify,
        )
        _tracker.complete(run_id, result)

        compliance_pct = round(result.get("compliance_rate", 0) * 100, 1)
        verdict = result.get("overall_verdict", "UNKNOWN")
        tested = result.get("tested_screens", 0)
        compliant = result.get("compliant", 0)
        report_path = result.get("report_path", "")

        msg = (
            f"*Figma UAT Complete*\n"
            f"Run ID: `{run_id}`\n"
            f"Verdict: *{verdict}*\n"
            f"Compliance: {compliance_pct}% ({compliant}/{tested} screens)\n"
        )
        if report_path:
            msg += f"Report: `{Path(report_path).name}`\n"
        msg += "\nUse /report to fetch the full report."

    except Exception as exc:
        _tracker.fail(run_id, str(exc))
        msg = (
            f"*Figma UAT Failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Error: {exc}"
        )
        logger.exception(f"[bot] Figma UAT run {run_id} raised an exception")

    _notify(msg)


# ---------------------------------------------------------------------------
# Prism — /new command (create product + auto-start agents from phone)
# ---------------------------------------------------------------------------


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new product and start intelligence agents.

    Usage: /new ProductName — description of the product
    Example: /new Swiggy — India's largest food delivery and quick commerce platform
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Create a new product*\n\n"
            "Usage: `/new Name — description`\n\n"
            "Example:\n"
            "`/new Swiggy — India's largest food delivery and quick commerce platform`\n\n"
            "The agents will automatically start researching competitors and the industry.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw = " ".join(args)
    if " — " in raw:
        name, description = raw.split(" — ", 1)
    elif " - " in raw:
        name, description = raw.split(" - ", 1)
    else:
        name = raw
        description = ""

    name = name.strip()
    description = description.strip()

    if not name:
        await update.message.reply_text("Product name is required.")
        return

    await update.message.reply_text(f"Creating *{name}*...", parse_mode=ParseMode.MARKDOWN)

    try:
        import httpx

        # Create project via API
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "http://localhost:8000/api/projects",
                json={
                    "name": name,
                    "description": description or None,
                    "enable_intelligence": True,
                },
            )
            r.raise_for_status()
            project = r.json()
            pid = project["id"]

        # Set as active project for /intel commands
        _intel_project[update.effective_chat.id] = pid

        # Start agents in background
        def _start():
            try:
                import sys
                from pathlib import Path
                _root = Path(__file__).resolve().parent.parent
                if str(_root) not in sys.path:
                    sys.path.insert(0, str(_root))
                from agent.product_os_orchestrator import get_orchestrator
                orch = get_orchestrator(pid)
                orch.run_agent_session("competitive_intel")
            except Exception as e:
                logger.error(f"[bot] Auto-start agents failed for project {pid}: {e}")

        thread = threading.Thread(target=_start, daemon=True)
        thread.start()

        msg = (
            f"*{name}* created (project #{pid})\n\n"
            f"Agents are starting up — they'll discover competitors and research the industry.\n\n"
            f"Use these commands:\n"
            f"`/intel status` — check progress\n"
            f"`/intel competitors` — see discovered competitors\n"
            f"`/intel ask <question>` — query the knowledge base\n"
            f"`/intel digest` — get a summary\n"
        )
        if description:
            msg += f"\nDescription: _{description}_"

        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"Failed to create product: {e}")


# ---------------------------------------------------------------------------
# Prism — /intel commands
# ---------------------------------------------------------------------------

# Track the active project for intel commands per chat
_intel_project: dict[int, int] = {}  # chat_id -> project_id


async def cmd_intel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route /intel subcommands: start, stop, status, run, ask, competitors, trends, digest."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Product OS Intelligence*\n\n"
            "/intel start — start autonomous agents\n"
            "/intel stop — stop agents\n"
            "/intel status — agent status & knowledge stats\n"
            "/intel run `<agent>` — run a single agent session\n"
            "/intel ask `<question>` — query the knowledge base\n"
            "/intel competitors — list discovered competitors\n"
            "/intel trends — latest industry trends\n"
            "/intel digest — generate daily digest\n"
            "/intel setproject `<id>` — set active project\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    subcmd = args[0].lower()
    rest = " ".join(args[1:])

    if subcmd == "setproject":
        await _intel_setproject(update, rest)
    elif subcmd == "start":
        await _intel_start(update)
    elif subcmd == "stop":
        await _intel_stop(update)
    elif subcmd == "status":
        await _intel_status(update)
    elif subcmd == "run":
        await _intel_run(update, rest, context)
    elif subcmd == "ask":
        await _intel_ask(update, rest)
    elif subcmd == "competitors":
        await _intel_competitors(update)
    elif subcmd == "trends":
        await _intel_trends(update)
    elif subcmd == "digest":
        await _intel_digest(update)
    else:
        await update.message.reply_text(f"Unknown subcommand: {subcmd}. Use /intel for help.")


async def _intel_setproject(update: Update, project_id_str: str) -> None:
    if not project_id_str.strip():
        await update.message.reply_text("Usage: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        pid = int(project_id_str.strip())
    except ValueError:
        await update.message.reply_text("Project ID must be a number.")
        return
    _intel_project[update.effective_chat.id] = pid
    await update.message.reply_text(f"Intel project set to {pid}")


def _get_intel_project(chat_id: int) -> int | None:
    return _intel_project.get(chat_id)


async def _intel_start(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        orch.start_daemon()
        await update.message.reply_text(f"Product OS agents started for project {pid}")
    except Exception as e:
        await update.message.reply_text(f"Failed to start: {e}")


async def _intel_stop(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("No active intel project.")
        return
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        orch.stop_daemon()
        await update.message.reply_text("Product OS agents stopped.")
    except Exception as e:
        await update.message.reply_text(f"Failed to stop: {e}")


async def _intel_status(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        status = orch.get_status()

        lines = [f"*Product OS Status* (project {pid})\n"]
        lines.append(f"Running: {'Yes' if status['is_running'] else 'No'}\n")

        for agent_type, info in status.get("agents", {}).items():
            last = info.get("last_session")
            pending = info.get("pending_work_items", 0)
            total = info.get("total_sessions", 0)
            last_str = last.get("completed_at", "never")[:19] if last else "never"
            lines.append(f"*{agent_type}*: {total} sessions, {pending} pending, last: {last_str}")

        kb = status.get("knowledge", {})
        lines.append(f"\nKnowledge: {kb.get('total_entities', 0)} entities, "
                      f"{kb.get('total_observations', 0)} observations, "
                      f"{kb.get('total_artifacts', 0)} artifacts")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_run(update: Update, agent_type: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    agent_type = agent_type.strip()
    if not agent_type:
        await update.message.reply_text("Usage: /intel run `<agent_type>`\nTypes: competitive_intel, industry_research, ux_intel", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text(f"Starting {agent_type} session for project {pid}...")

    chat_id = update.effective_chat.id
    app_ref = context.application

    def _run_in_thread():
        try:
            from agent.product_os_orchestrator import get_orchestrator
            orch = get_orchestrator(pid)
            result = orch.run_agent_session(agent_type)
            msg = (
                f"*{agent_type} session complete*\n"
                f"Items: {result.get('items_completed', 0)} done, {result.get('items_failed', 0)} failed\n"
                f"Knowledge added: {result.get('knowledge_added', 0)}\n"
                f"Duration: {result.get('elapsed_s', 0)}s"
            )
        except Exception as e:
            msg = f"Agent session failed: {e}"
            logger.exception(f"[intel] {agent_type} session error")

        import asyncio
        asyncio.run_coroutine_threadsafe(
            app_ref.bot.send_message(chat_id, msg, parse_mode=ParseMode.MARKDOWN),
            app_ref.loop if hasattr(app_ref, 'loop') else asyncio.get_event_loop(),
        )

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()


async def _intel_ask(update: Update, question: str) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    if not question.strip():
        await update.message.reply_text("Usage: /intel ask `<question>`", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text("Searching knowledge base...")
    try:
        from agent.knowledge_store import KnowledgeStore
        from webapp.api.db import SessionLocal
        db = SessionLocal()
        try:
            ks = KnowledgeStore(db, "query", pid)
            # Simple: search entities + observations, synthesize with Claude
            results = ks.semantic_search(question, top_k=10)
            entities = ks.find_entities(name_like=question.split()[0] if question.split() else None, limit=5)

            from utils.claude_client import ask as claude_ask
            context_parts = []
            for e in entities:
                context_parts.append(f"Entity: {e['name']} ({e['entity_type']}): {e.get('description', '')}")
            for r in results:
                context_parts.append(f"Knowledge: {r.get('text_chunk', '')[:200]}")

            if not context_parts:
                await update.message.reply_text("No relevant knowledge found yet. Run some agent sessions first.")
                return

            answer = claude_ask(
                f"Based on this knowledge, answer the question concisely:\n\n"
                f"Knowledge:\n" + "\n".join(context_parts) + f"\n\nQuestion: {question}",
                max_tokens=1024,
            )
            await update.message.reply_text(answer)
        finally:
            db.close()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_competitors(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from agent.knowledge_store import KnowledgeStore
        from webapp.api.db import SessionLocal
        db = SessionLocal()
        try:
            ks = KnowledgeStore(db, "query", pid)
            competitors = ks.find_entities(entity_type="company", limit=20)
            if not competitors:
                await update.message.reply_text("No competitors discovered yet. Run: /intel run competitive\\_intel", parse_mode=ParseMode.MARKDOWN)
                return
            lines = ["*Discovered Competitors*\n"]
            for c in competitors:
                desc = (c.get("description") or "")[:80]
                lines.append(f"• *{c['name']}* — {desc}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        finally:
            db.close()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_trends(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from webapp.api.db import SessionLocal
        from webapp.api.models import KnowledgeObservation, KnowledgeEntity
        from datetime import datetime, timedelta
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=7)
            recent = (
                db.query(KnowledgeObservation)
                .join(KnowledgeEntity)
                .filter(
                    KnowledgeEntity.project_id == pid,
                    KnowledgeObservation.recorded_at >= cutoff,
                    KnowledgeObservation.observation_type.in_(["news", "regulatory", "metric"]),
                )
                .order_by(KnowledgeObservation.recorded_at.desc())
                .limit(10)
                .all()
            )
            if not recent:
                await update.message.reply_text("No recent trends found. Run: /intel run industry\\_research", parse_mode=ParseMode.MARKDOWN)
                return
            lines = ["*Recent Industry Trends*\n"]
            for obs in recent:
                date_str = obs.recorded_at.strftime("%m/%d")
                lines.append(f"• [{date_str}] {obs.content[:100]}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        finally:
            db.close()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_digest(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text("Generating digest...")
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        digest = orch.generate_daily_digest()
        await update.message.reply_text(digest)
    except Exception as e:
        await update.message.reply_text(f"Error generating digest: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("run_figma", cmd_run_figma))
    app.add_handler(CommandHandler("builds", cmd_builds))
    app.add_handler(CommandHandler("use_build", cmd_use_build))
    app.add_handler(CommandHandler("upload_local", cmd_upload_local))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cases", cmd_cases))
    # AppUAT screen mapping commands
    app.add_handler(CommandHandler("projects", cmd_appuat_projects))
    app.add_handler(CommandHandler("setproject", cmd_appuat_setproject))
    app.add_handler(CommandHandler("uat", cmd_appuat_uat))
    app.add_handler(CommandHandler("uatsuite", cmd_appuat_uat_suite))
    # Product OS intelligence commands
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("intel", cmd_intel))
    # Photo handler — uploads to active AppUAT project
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # Must be registered AFTER document handler so APK uploads are handled first
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("[bot] Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
