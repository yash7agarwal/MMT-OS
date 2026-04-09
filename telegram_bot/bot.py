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

        orchestrator = Orchestrator(
            run_id=run_id,
            feature=feature,
            accounts_file=accounts_file,
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
        "*MMT-OS UAT Bot*\n\n"
        "I run automated UAT on MakeMyTrip Android builds and report results.\n\n"
        "*Commands:*\n"
        "/run `<feature>` — start a UAT run\n"
        "/status — show current run status\n"
        "/report — get the latest report\n"
        "/list — list recent runs\n"
        "/cases `<feature>` — list use cases for a feature\n"
        "/help — show this message\n\n"
        "To upload an APK, simply send a `.apk` file as a document."
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
# APK upload handler
# ---------------------------------------------------------------------------


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
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cases", cmd_cases))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("[bot] Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
