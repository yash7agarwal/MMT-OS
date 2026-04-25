"""Strongly-typed research brief — the ONLY project context passed downstream.

The brief is built once per run from (project row, KG state, user feedback)
and threaded through the query planner, retriever, and synthesiser. All
project-specific context flows through this object; no agent reads project
metadata from anywhere else.

This is the architectural spine that prevents the bug class that caused travel
trends to leak into non-travel projects: if context isn't on the brief, the
downstream stages have no way to ask about it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from webapp.api.models import (
    KnowledgeEntity,
    KnowledgeObservation,
    Project,
)

STALE_OBS_DAYS = 30
LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_RECENT_TRENDS = 30
MAX_COMPETITORS = 30


@dataclass
class BriefEntityRef:
    """Compact reference to a KG entity surfaced in the brief."""
    id: int
    name: str
    canonical_name: str
    confidence: float
    last_updated_at: str | None = None
    description: str | None = None


@dataclass
class ResearchBrief:
    """Project + KG snapshot passed to every downstream research stage.

    Stable across a 24h planning TTL; changes trigger re-planning via content_hash.
    """
    project_id: int
    project_name: str
    project_description: str
    app_package: str | None

    # Portfolio summary extracted from the project's homepage (one Claude
    # call cached at process scope). Anchors EVERY downstream prompt on
    # what the company actually does, instead of letting keyword-only
    # queries drift into adjacent meanings (the "Platinum Industries the
    # company" vs "platinum the metal" failure mode).
    portfolio_summary: str | None = None

    # Anchors for seed generation.
    known_competitors: list[BriefEntityRef] = field(default_factory=list)
    recent_trends: list[BriefEntityRef] = field(default_factory=list)

    # Feedback signal — drives the compounding loop.
    starred_canonicals: list[str] = field(default_factory=list)
    dismissed_canonicals: list[str] = field(default_factory=list)
    # {canonical_name: reason} for dismissed items with an explanation.
    dismissed_reasons: dict[str, str] = field(default_factory=dict)

    # Research frontier — what the planner should target.
    low_confidence_entities: list[BriefEntityRef] = field(default_factory=list)
    stale_trend_canonicals: list[str] = field(default_factory=list)

    # Metadata
    built_at: str = ""
    stats: dict[str, int] = field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable hash over fields that should invalidate the cached research plan.

        Excludes `built_at` (monotonic) and entity `last_updated_at` (noise).
        Changes when: project metadata changes, a new competitor is learned,
        a trend is starred/dismissed, or a confidence shift crosses the threshold.
        """
        payload = {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_description": self.project_description or "",
            "app_package": self.app_package or "",
            "competitors": sorted(c.canonical_name for c in self.known_competitors),
            "recent_trends": sorted(t.canonical_name for t in self.recent_trends),
            "starred": sorted(self.starred_canonicals),
            "dismissed": sorted(self.dismissed_canonicals),
            "low_conf": sorted(e.canonical_name for e in self.low_confidence_entities),
            "stale": sorted(self.stale_trend_canonicals),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_prompt_context(self) -> str:
        """Render as compact markdown for the planner's system prompt.

        Designed to be prompt-cached — stable across a 24h TTL.
        """
        lines = [
            f"# Project brief — {self.project_name}",
            f"App package: {self.app_package or 'n/a'}",
            "",
        ]
        # Portfolio summary leads — it's the strongest grounding signal we
        # have because it's extracted from the actual homepage. Includes
        # the explicit "WHAT THIS COMPANY IS NOT" disambiguation block
        # which is the load-bearing piece for keyword-collision cases.
        if self.portfolio_summary:
            lines.append("## Portfolio summary (extracted from homepage — authoritative)")
            lines.append(self.portfolio_summary)
            lines.append("")
        lines.extend([
            "## Description (user-typed)",
            self.project_description or "(no description)",
            "",
        ])
        if self.known_competitors:
            lines.append(f"## Known competitors ({len(self.known_competitors)})")
            for c in self.known_competitors[:MAX_COMPETITORS]:
                suffix = f" — {c.description[:120]}" if c.description else ""
                lines.append(f"- {c.name}{suffix}")
            lines.append("")
        if self.recent_trends:
            lines.append(f"## Recently tracked trends ({len(self.recent_trends)})")
            for t in self.recent_trends[:MAX_RECENT_TRENDS]:
                lines.append(f"- {t.name}")
            lines.append("")
        if self.starred_canonicals:
            lines.append("## User-starred (positive examples — more like these)")
            for c in self.starred_canonicals:
                lines.append(f"- {c}")
            lines.append("")
        if self.dismissed_canonicals:
            lines.append("## User-dismissed (negative examples — avoid these patterns)")
            for c in self.dismissed_canonicals:
                reason = self.dismissed_reasons.get(c)
                suffix = f" (why: {reason})" if reason else ""
                lines.append(f"- {c}{suffix}")
            lines.append("")
        if self.low_confidence_entities:
            lines.append("## Low-confidence entities (validation targets)")
            for e in self.low_confidence_entities:
                lines.append(f"- {e.name} (confidence {e.confidence:.2f})")
            lines.append("")
        if self.stale_trend_canonicals:
            lines.append("## Stale trends (no observation in 30d — re-validate)")
            for c in self.stale_trend_canonicals:
                lines.append(f"- {c}")
            lines.append("")
        return "\n".join(lines).strip()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_brief(db: Session, project_id: int) -> ResearchBrief:
    """Build a ResearchBrief from the current DB state for a project.

    Reads: Project row, all entities (competitor + trend types), recent
    observations, user_signal + dismissed_reason columns.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    stale_cutoff = datetime.utcnow() - timedelta(days=STALE_OBS_DAYS)

    entities = (
        db.query(KnowledgeEntity)
        .filter(KnowledgeEntity.project_id == project_id)
        .all()
    )

    known_competitors: list[BriefEntityRef] = []
    recent_trends: list[BriefEntityRef] = []
    starred: list[str] = []
    dismissed: list[str] = []
    dismissed_reasons: dict[str, str] = {}
    low_confidence: list[BriefEntityRef] = []
    stale_trend_canonicals: list[str] = []

    trend_entity_ids: list[int] = []
    # Decay-flagged entities from agent/decay.py are authoritative — any trend
    # with decay_state='needs_revalidation' goes straight to the validation list.
    decay_flagged: set[str] = set()
    for e in entities:
        canonical = (e.canonical_name or e.name).lower()

        if e.user_signal == "starred":
            starred.append(canonical)
        elif e.user_signal == "dismissed":
            dismissed.append(canonical)
            if e.dismissed_reason:
                dismissed_reasons[canonical] = e.dismissed_reason
            # Dismissed entities do NOT populate `recent_trends` / `competitors`
            # below — we don't want the planner to treat them as anchors.
            continue

        ref = BriefEntityRef(
            id=e.id,
            name=e.name,
            canonical_name=canonical,
            confidence=e.confidence,
            last_updated_at=e.last_updated_at.isoformat() if e.last_updated_at else None,
            description=e.description,
        )

        if e.entity_type == "competitor":
            known_competitors.append(ref)
        elif e.entity_type == "trend":
            recent_trends.append(ref)
            trend_entity_ids.append(e.id)
            if e.decay_state == "needs_revalidation":
                decay_flagged.add(canonical)

        if e.confidence < LOW_CONFIDENCE_THRESHOLD:
            low_confidence.append(ref)

    # Sort: most recently updated first, so rendering truncation keeps the fresh end.
    known_competitors.sort(
        key=lambda x: x.last_updated_at or "", reverse=True,
    )
    recent_trends.sort(
        key=lambda x: x.last_updated_at or "", reverse=True,
    )

    # Stale trends: trends whose most recent observation is older than STALE_OBS_DAYS
    # (or trends with zero observations — orphaned claims that need evidence).
    if trend_entity_ids:
        last_obs_rows = (
            db.query(
                KnowledgeObservation.entity_id,
                # max(observed_at) per entity
                KnowledgeObservation.observed_at,
            )
            .filter(KnowledgeObservation.entity_id.in_(trend_entity_ids))
            .order_by(KnowledgeObservation.entity_id, KnowledgeObservation.observed_at.desc())
            .all()
        )
        # Keep only first (latest) per entity_id
        seen: set[int] = set()
        latest_by_entity: dict[int, datetime] = {}
        for eid, obs_at in last_obs_rows:
            if eid in seen:
                continue
            seen.add(eid)
            latest_by_entity[eid] = obs_at
        trends_by_id = {t.id: t for t in recent_trends}
        for tid, tref in trends_by_id.items():
            latest = latest_by_entity.get(tid)
            if latest is None or latest < stale_cutoff:
                stale_trend_canonicals.append(tref.canonical_name)
    # Union the observation-age check with the persistent decay_state flags.
    stale_trend_canonicals = sorted(set(stale_trend_canonicals) | decay_flagged)

    # Fetch portfolio summary from the project's homepage once per session.
    # Cached at process scope so this only costs one LLM call per (url, name).
    from agent.website_grounding import fetch_portfolio_summary
    portfolio_summary = fetch_portfolio_summary(project.app_package, project.name)

    return ResearchBrief(
        project_id=project_id,
        project_name=project.name,
        project_description=project.description or "",
        app_package=project.app_package,
        portfolio_summary=portfolio_summary,
        known_competitors=known_competitors,
        recent_trends=recent_trends,
        starred_canonicals=sorted(set(starred)),
        dismissed_canonicals=sorted(set(dismissed)),
        dismissed_reasons=dismissed_reasons,
        low_confidence_entities=low_confidence[:MAX_RECENT_TRENDS],
        stale_trend_canonicals=stale_trend_canonicals,
        built_at=datetime.utcnow().isoformat() + "Z",
        stats={
            "n_competitors": len(known_competitors),
            "n_recent_trends": len(recent_trends),
            "n_starred": len(starred),
            "n_dismissed": len(dismissed),
            "n_low_confidence": len(low_confidence),
            "n_stale_trends": len(stale_trend_canonicals),
        },
    )
