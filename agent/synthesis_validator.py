"""Deterministic synthesis-output validator.

The synthesizer (stage 4) returns candidate observations, each with a
mandatory `source_url`. Before writing anything to the KG, this module checks
that every claimed source URL is actually present in the retrieval bundle
the synthesizer was given.

This is the cheapest possible hallucination guardrail: no LLM judge, no
embedding compare, just URL-set membership. A candidate whose `source_url`
isn't in the bundle is rejected outright.

Workspace rule: "Zero hallucination tolerance — Agents must NEVER fabricate
data/percentages. Source URL mandatory." This module enforces that rule.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    """Summary of a validation pass — written to AgentSession.quality_score_json."""
    total_in: int
    total_out: int
    dropped_missing_source: int
    dropped_invalid_url: int
    dropped_url_not_in_bundle: int
    drop_reasons: list[dict]  # [{url, canonical, reason}]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_in": self.total_in,
            "total_out": self.total_out,
            "dropped_missing_source": self.dropped_missing_source,
            "dropped_invalid_url": self.dropped_invalid_url,
            "dropped_url_not_in_bundle": self.dropped_url_not_in_bundle,
            "drop_reasons": self.drop_reasons[:50],  # cap to keep row size bounded
        }


def _normalize(url: str) -> str:
    """Lowercase scheme+host, strip trailing slash, drop fragment and tracking query noise.

    Designed to make 'https://example.com/a/' and 'http://Example.com/a?utm=x#frag'
    compare equal.
    """
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return ""
    if not p.scheme or not p.netloc:
        return ""
    host = p.netloc.lower()
    path = p.path.rstrip("/") or "/"
    # Drop tracking params; keep semantically meaningful ones unchanged is too
    # risky (e.g. article IDs), so we keep the whole query except common utm_*
    # markers. Conservative approach: keep query as-is except drop utm_* keys.
    query = ""
    if p.query:
        kept = [
            kv for kv in p.query.split("&")
            if kv and not kv.lower().startswith(("utm_", "fbclid=", "gclid="))
        ]
        if kept:
            query = "?" + "&".join(kept)
    return f"{p.scheme.lower()}://{host}{path}{query}"


def validate_candidates(
    candidates: list[dict],
    retrieval_bundle: list[dict],
) -> tuple[list[dict], ValidationReport]:
    """Filter out candidate observations whose source_url isn't in the bundle.

    Args:
        candidates: list of dicts, each with at least {source_url, ...}. Any shape
            is accepted; only `source_url` and optional `canonical_name`/`name` are
            read.
        retrieval_bundle: list of dicts with at least {url: ...}. Typically the
            output of web_research — one dict per fetched page.

    Returns:
        (kept_candidates, report). `kept_candidates` is a new list preserving
        input order and objects; `report` records the rejections.
    """
    bundle_urls: set[str] = set()
    for r in retrieval_bundle:
        u = r.get("url") if isinstance(r, dict) else getattr(r, "url", None)
        norm = _normalize(u) if u else ""
        if norm:
            bundle_urls.add(norm)

    kept: list[dict] = []
    drop_reasons: list[dict] = []
    missing = invalid = not_in_bundle = 0

    for c in candidates:
        if not isinstance(c, dict):
            # Coerce to dict-like via attribute access — defensive for future callers.
            try:
                c = dict(c)  # type: ignore[arg-type]
            except Exception:
                missing += 1
                drop_reasons.append({"canonical": "<unknown>", "url": "", "reason": "non-dict candidate"})
                continue

        url = (c.get("source_url") or "").strip()
        canonical = c.get("canonical_name") or c.get("name") or "<unnamed>"
        if not url:
            missing += 1
            drop_reasons.append({"canonical": canonical, "url": "", "reason": "missing source_url"})
            continue
        norm = _normalize(url)
        if not norm:
            invalid += 1
            drop_reasons.append({"canonical": canonical, "url": url, "reason": "unparseable url"})
            continue
        if norm not in bundle_urls:
            not_in_bundle += 1
            drop_reasons.append({"canonical": canonical, "url": url, "reason": "url not in retrieval bundle"})
            continue
        kept.append(c)

    report = ValidationReport(
        total_in=len(candidates),
        total_out=len(kept),
        dropped_missing_source=missing,
        dropped_invalid_url=invalid,
        dropped_url_not_in_bundle=not_in_bundle,
        drop_reasons=drop_reasons,
    )

    if missing or invalid or not_in_bundle:
        logger.warning(
            "[validator] dropped %d/%d candidates (missing=%d invalid=%d not_in_bundle=%d)",
            report.total_in - report.total_out, report.total_in,
            missing, invalid, not_in_bundle,
        )
    return kept, report
