"""QualityReviewAgent — the grounding gate Prism's been missing.

LESSONS.md §6.1 / v0.9.1 anti-hallucination work established a text-level
rule ("Do NOT invent facts…") but it had no enforcement. This agent is
the enforcement layer.

After every intel/impact session, scan the session's new observations and
flag any that look ungrounded:
  - missing source_url
  - numeric claims ("40%", "2.5x", "$50M") without a source
  - growth verbs ("increase", "doubled", "surge") without a dated metric

Each flag is persisted as a KnowledgeArtifact(artifact_type="quality_flag")
and stamped onto the observation's evidence_json so downstream readers
(feed, digests) can filter or visually mark it.

MVP v0.10.4: heuristic-only. Zero LLM calls, deterministic, free.
v0.10.5+: add optional Groq confirmation on flagged cases to reduce
false positives; integrate with the digest ranker so flagged findings
are held back from the "top 3 this week" push.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Numbers paired with units we most often see fabricated: percentages,
# multipliers, currency, user counts. Deliberately narrow to reduce noise.
_NUMBER_WITH_UNIT = re.compile(
    r"""
    (?:
        # Case 1: currency prefix + number (e.g. $50, ₹50M, €1.2B)
        (?:\$|\u20b9|\u20ac|\binr\b|\busd\b|\beur\b)
        \s*\d+(?:[.,]\d+)?
        (?:\s*[mbk]\b)?
    )
    |
    (?:
        # Case 2: number + unit suffix
        \b\d+(?:[.,]\d+)?
        \s*
        (?:%|percent|x|\u00d7|                           # 40%, 40 percent, 2x
           million|billion|thousand|                     # 50 million
           [mbk]\b|                                      # 50M, 2B, 100K (word-boundary)
           users?|customers?|downloads|installs)         # scale nouns
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

_GROWTH_WORDS = {
    "growth", "growing", "grew",
    "increase", "increased", "increasing",
    "boost", "boosted",
    "doubled", "tripled", "quadrupled",
    "spike", "spiked", "surging", "surge",
    "decline", "declined", "declining",
    "drop", "dropped", "plunge", "plunged",
    "up ", "down ",
}


def _heuristic_check(content: str, source_url: str | None) -> tuple[str | None, str]:
    """Classify content against the grounding heuristics.

    Returns (reason, severity). reason=None means the observation passed.
    Severities: "high" (fabricated-number risk), "medium" (growth claim
    without evidence), "low" (no source at all).
    """
    if not content or not content.strip():
        return None, ""

    content_lower = content.lower()
    has_number = bool(_NUMBER_WITH_UNIT.search(content))
    has_growth_word = any(w in content_lower for w in _GROWTH_WORDS)
    has_source = bool(source_url and source_url.strip())

    if has_number and not has_source:
        return "numeric_claim_no_source", "high"
    if has_growth_word and not has_source:
        return "growth_claim_no_source", "medium"
    if not has_source:
        return "no_source_url", "low"
    return None, ""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class QualityReviewAgent:
    """Post-session review. Not an AutonomousAgent subclass — it's a hook."""

    agent_type = "quality_review"

    def __init__(self, project_id: int, db: Session):
        self.project_id = project_id
        self.db = db

    def review_recent(self, since_minutes: int = 60) -> dict[str, Any]:
        """Review observations recorded in the last N minutes. Creates
        quality_flag artifacts for any that fail the heuristics, and
        stamps the flag id back onto the observation.
        """
        from webapp.api.models import (
            KnowledgeArtifact,
            KnowledgeEntity,
            KnowledgeObservation,
        )

        since = datetime.utcnow() - timedelta(minutes=since_minutes)
        obs_list = (
            self.db.query(KnowledgeObservation)
            .join(
                KnowledgeEntity,
                KnowledgeObservation.entity_id == KnowledgeEntity.id,
            )
            .filter(
                KnowledgeEntity.project_id == self.project_id,
                KnowledgeObservation.recorded_at >= since,
            )
            .all()
        )

        flagged = 0
        reasons: dict[str, int] = {}
        for obs in obs_list:
            ev = dict(obs.evidence_json or {})
            if ev.get("quality_flag_id"):
                # Already reviewed.
                continue

            reason, severity = _heuristic_check(obs.content, obs.source_url)
            if reason is None:
                continue

            artifact = KnowledgeArtifact(
                project_id=self.project_id,
                artifact_type="quality_flag",
                title=f"Quality flag [{severity}]: {reason}",
                content_md=self._render_flag(obs, reason, severity),
                entity_ids_json=[obs.entity_id],
                generated_by_agent=self.agent_type,
            )
            self.db.add(artifact)
            self.db.flush()  # populate artifact.id

            ev["quality_flag_id"] = artifact.id
            ev["quality_flag_reason"] = reason
            ev["quality_flag_severity"] = severity
            obs.evidence_json = ev

            flagged += 1
            reasons[reason] = reasons.get(reason, 0) + 1

        if flagged:
            self.db.commit()
            logger.info(
                "[quality_review] project=%s flagged=%d reasons=%s",
                self.project_id, flagged, reasons,
            )
        else:
            # Nothing to commit — harmless rollback to release the transaction.
            self.db.rollback()

        return {
            "status": "completed",
            "reviewed": len(obs_list),
            "flagged": flagged,
            "reasons": reasons,
            "since": since.isoformat(),
        }

    @staticmethod
    def _render_flag(obs: Any, reason: str, severity: str) -> str:
        snippet = (obs.content or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        src = obs.source_url or "_missing_"
        return (
            f"**Observation id={obs.id}** flagged as `{reason}` "
            f"({severity} severity).\n\n"
            f"> {snippet}\n\n"
            f"**Source:** {src}\n\n"
            f"**Observation type:** `{obs.observation_type}`  "
            f"**Recorded:** {obs.recorded_at.isoformat() if obs.recorded_at else 'n/a'}"
        )
