"""Outbound research-digest sender for Telegram.

Posts one message per new/high-confidence trend after a research run, each
with an inline keyboard [Keep] [Dismiss] [Star] so the PM can feed the
compounding loop from their phone instead of opening the web app.

This module is intentionally dependency-light: it uses raw httpx to hit the
Telegram Bot API so any process — orchestrator daemon, ad-hoc script, cron
job — can import and call `send_digest()` without loading python-telegram-bot.

The inbound side (callback handlers that translate button taps into
`user_signal` writes) lives in telegram_bot/bot.py.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from webapp.api.models import (
    AgentSession,
    KnowledgeEntity,
    KnowledgeObservation,
    Project,
)

load_dotenv()

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE_THRESHOLD = 0.7
DIGEST_MAX_ITEMS = 8  # cap per session to avoid flooding the chat
TELEGRAM_API = "https://api.telegram.org"


def _chat_id() -> str | None:
    return os.environ.get("TELEGRAM_PM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")


def _bot_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN")


def _find_new_or_updated_trends(
    db: Session, session: AgentSession, limit: int = DIGEST_MAX_ITEMS,
) -> list[KnowledgeEntity]:
    """Trends updated during this session (conservatively: within the session window).

    We filter by last_updated_at falling inside [session.started_at, now],
    entity_type='trend', project_id matches, confidence ≥ threshold, and the
    user hasn't already signaled (dismissed items stay hidden).
    """
    since = session.started_at
    until = session.completed_at or datetime.utcnow()
    # Pad the window by 60s on either side to absorb timestamp jitter.
    since -= timedelta(seconds=60)
    until += timedelta(seconds=60)

    return (
        db.query(KnowledgeEntity)
        .filter(
            KnowledgeEntity.project_id == session.project_id,
            KnowledgeEntity.entity_type == "trend",
            KnowledgeEntity.last_updated_at >= since,
            KnowledgeEntity.last_updated_at <= until,
            KnowledgeEntity.confidence >= HIGH_CONFIDENCE_THRESHOLD,
            KnowledgeEntity.user_signal.is_(None),
        )
        .order_by(KnowledgeEntity.confidence.desc(), KnowledgeEntity.last_updated_at.desc())
        .limit(limit)
        .all()
    )


def _format_trend_message(
    project: Project, entity: KnowledgeEntity, latest_obs: KnowledgeObservation | None,
) -> str:
    meta = entity.metadata_json or {}
    timeline = meta.get("timeline", "present")
    category = meta.get("category", "general")

    quant_bits = []
    for k in ("growth_rate", "search_volume", "market_size", "user_demand"):
        v = meta.get(k)
        if v:
            quant_bits.append(f"{k.replace('_',' ')}: {v}")
    quant = " · ".join(quant_bits)

    desc = (entity.description or "").strip()
    if len(desc) > 480:
        desc = desc[:480].rsplit(" ", 1)[0] + "…"

    src = latest_obs.source_url if latest_obs and latest_obs.source_url else ""
    src_line = f"\n🔗 {_md_escape(src)}" if src else ""

    body = (
        f"🔍 *{_md_escape(project.name)}* · {_md_escape(category)} · _{_md_escape(timeline)}_\n\n"
        f"*{_md_escape(entity.name)}*\n"
        f"{_md_escape(desc)}"
    )
    if quant:
        body += f"\n\n📊 {_md_escape(quant)}"
    body += src_line
    return body


_MD_V2_SPECIALS = r"_*[]()~`>#+-=|{}.!"


def _md_escape(text: str) -> str:
    """Escape text for Telegram MarkdownV2 mode.

    Escapes backslashes first, then the reserved characters, so we don't
    double-escape the backslashes we introduce.
    """
    if text is None:
        return ""
    text = text.replace("\\", "\\\\")
    for ch in _MD_V2_SPECIALS:
        text = text.replace(ch, f"\\{ch}")
    return text


def _keyboard(entity_id: int) -> dict[str, Any]:
    """Inline keyboard — keep/dismiss/star. Callback data encodes entity_id."""
    return {
        "inline_keyboard": [
            [
                {"text": "👍 Keep", "callback_data": f"sig:kept:{entity_id}"},
                {"text": "✖ Dismiss", "callback_data": f"sig:dismissed:{entity_id}"},
                {"text": "⭐ Star", "callback_data": f"sig:starred:{entity_id}"},
            ]
        ]
    }


def _send_message(
    chat_id: str, text: str, reply_markup: dict | None = None,
) -> bool:
    token = _bot_token()
    if not token:
        logger.warning("[digest] TELEGRAM_BOT_TOKEN not set — skipping send")
        return False
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        r = httpx.post(f"{TELEGRAM_API}/bot{token}/sendMessage", json=payload, timeout=30)
        if r.status_code != 200:
            logger.warning("[digest] Telegram sendMessage %d: %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        logger.warning("[digest] Telegram send failed: %s", exc)
        return False


def send_digest(db: Session, session_id: int) -> dict[str, Any]:
    """Send the post-run digest for a completed AgentSession.

    Returns {sent, skipped, reason}. Safe to call without TELEGRAM_PM_CHAT_ID
    configured — it will log and no-op rather than raise, so callers (the
    orchestrator session-end hook) never fail a run because of digest issues.
    """
    chat_id = _chat_id()
    if not chat_id:
        logger.info("[digest] TELEGRAM_PM_CHAT_ID not set — skipping")
        return {"sent": 0, "skipped": 0, "reason": "no_chat_id"}

    session = db.get(AgentSession, session_id)
    if session is None:
        return {"sent": 0, "skipped": 0, "reason": "no_session"}
    project = db.get(Project, session.project_id)
    if project is None:
        return {"sent": 0, "skipped": 0, "reason": "no_project"}

    entities = _find_new_or_updated_trends(db, session)
    if not entities:
        return {"sent": 0, "skipped": 0, "reason": "no_trends"}

    sent = skipped = 0
    for entity in entities:
        latest_obs = (
            db.query(KnowledgeObservation)
            .filter(KnowledgeObservation.entity_id == entity.id)
            .order_by(KnowledgeObservation.observed_at.desc())
            .first()
        )
        msg = _format_trend_message(project, entity, latest_obs)
        ok = _send_message(chat_id, msg, reply_markup=_keyboard(entity.id))
        if ok:
            sent += 1
        else:
            skipped += 1
    logger.info(
        "[digest] session=%d project=%s: sent=%d skipped=%d",
        session_id, project.name, sent, skipped,
    )
    return {"sent": sent, "skipped": skipped, "reason": "ok"}
