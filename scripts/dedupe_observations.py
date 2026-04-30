"""Retroactive observation cleanup (v0.22.0).

Sweeps every entity's observations, merges high-similarity dupes (Jaccard
≥0.85 over word 3-grams), recomputes `quality_score` for survivors, and
backfills `dedupe_count` on the kept row to reflect how many were merged.

Run modes:
  --dry-run (default): reports what WOULD be merged; zero DB writes
  --apply             : actually merges + writes

Idempotent under --apply: a second run reports 0 dupes because the dedupe
gate in `KnowledgeStore.add_observation` (since v0.22.0) prevents new dupes,
and this script already merged the historical ones.

Foreign-key safety (v0.22.0 review must-fix #2): TWO references DO exist:
  - `KnowledgeObservation.superseded_by_id` (self-ref, ondelete=SET NULL)
  - `KnowledgeEmbedding.observation_id` (ondelete=CASCADE)

Naively deleting a duplicate would (a) cascade-delete its embedding —
silent vector loss — and (b) null any obs.superseded_by_id pointing at it.
We re-point both BEFORE delete so the keeper inherits the references.

Usage:
  .venv/bin/python -m scripts.dedupe_observations --dry-run
  .venv/bin/python -m scripts.dedupe_observations --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Repo root on sys.path so this can be run as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from sqlalchemy import text  # noqa: E402

from agent import quality_guard as qg  # noqa: E402
from webapp.api.db import SessionLocal, init_db  # noqa: E402
from webapp.api.models import KnowledgeEntity, KnowledgeObservation  # noqa: E402

# Ensure the v0.22.0 quality_score + dedupe_count columns exist before any
# query references them. Idempotent.
init_db()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dedupe")

DUPE_THRESHOLD = 0.85


def _group_dupes(observations: list[KnowledgeObservation]) -> list[list[KnowledgeObservation]]:
    """Group observations into sets of mutual ≥threshold similarity.
    Greedy single-pass clustering: each obs joins the first cluster whose
    representative it matches; otherwise starts its own cluster. Cheap,
    deterministic; finds same-content groups but not transitive ones (and
    transitive merges have weird semantics anyway)."""
    clusters: list[list[KnowledgeObservation]] = []
    for obs in observations:
        if not (obs.content or "").strip():
            continue
        joined = False
        for cluster in clusters:
            sim = qg.jaccard_3gram_similarity(obs.content, cluster[0].content)
            if sim >= DUPE_THRESHOLD:
                cluster.append(obs)
                joined = True
                break
        if not joined:
            clusters.append([obs])
    return clusters


def _process_entity(db: Session, entity_id: int, dry_run: bool) -> dict:
    """Returns a stats dict: {entity_id, total, kept, merged, scored}."""
    obs = (
        db.query(KnowledgeObservation)
        .filter(KnowledgeObservation.entity_id == entity_id)
        .order_by(KnowledgeObservation.observed_at.asc())
        .all()
    )
    if not obs:
        return {"entity_id": entity_id, "total": 0, "kept": 0, "merged": 0, "scored": 0}

    clusters = _group_dupes(obs)
    merged = 0
    kept = 0
    scored = 0

    for cluster in clusters:
        keeper = cluster[0]
        # Keep the OLDEST as canonical so original observed_at is preserved.
        # Already sorted by observed_at asc → cluster[0] is oldest.
        if len(cluster) > 1:
            for dupe in cluster[1:]:
                merged += 1
                if not dry_run:
                    # Bump dedupe_count on keeper; copy source_url if better
                    keeper.dedupe_count = (keeper.dedupe_count or 0) + 1
                    if not (keeper.source_url or "").strip() and (dupe.source_url or "").strip():
                        keeper.source_url = dupe.source_url
                    keeper.recorded_at = max(
                        keeper.recorded_at or datetime.utcnow(),
                        dupe.recorded_at or datetime.utcnow(),
                    )
                    # v0.22.0 review must-fix #2: re-point FK references
                    # BEFORE delete or we silently lose embeddings (CASCADE).
                    db.execute(
                        text(
                            "UPDATE knowledge_observations SET superseded_by_id = :keeper "
                            "WHERE superseded_by_id = :dupe"
                        ),
                        {"keeper": keeper.id, "dupe": dupe.id},
                    )
                    # Re-point embeddings if the table exists (it's a v0.10.x
                    # addition; some DBs may not have it).
                    try:
                        db.execute(
                            text(
                                "UPDATE knowledge_embeddings SET observation_id = :keeper "
                                "WHERE observation_id = :dupe"
                            ),
                            {"keeper": keeper.id, "dupe": dupe.id},
                        )
                    except Exception as exc:
                        log.debug("knowledge_embeddings repoint skipped: %s", exc)
                    db.delete(dupe)
        # Backfill score on keeper if zero/missing
        if (keeper.quality_score or 0.0) == 0.0:
            new_score = qg.score_observation(
                keeper.content or "", keeper.source_url, keeper.lens_tags
            )
            if not dry_run:
                keeper.quality_score = new_score
            scored += 1
        kept += 1

    if not dry_run:
        db.commit()

    return {
        "entity_id": entity_id,
        "total": len(obs),
        "kept": kept,
        "merged": merged,
        "scored": scored,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default is dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="Report only (default behavior)")
    args = ap.parse_args()
    dry_run = not args.apply

    log.info("Mode: %s", "APPLY (writes!)" if not dry_run else "dry-run (read-only)")

    db = SessionLocal()
    try:
        entity_ids = [
            row[0]
            for row in db.query(KnowledgeEntity.id).all()
        ]
        log.info("Scanning %d entities...", len(entity_ids))

        totals = {"total": 0, "kept": 0, "merged": 0, "scored": 0, "entities_touched": 0}
        for eid in entity_ids:
            stats = _process_entity(db, eid, dry_run=dry_run)
            totals["total"] += stats["total"]
            totals["kept"] += stats["kept"]
            totals["merged"] += stats["merged"]
            totals["scored"] += stats["scored"]
            if stats["merged"] > 0 or stats["scored"] > 0:
                totals["entities_touched"] += 1
                log.info(
                    "  entity %d: total=%d kept=%d merged=%d scored=%d",
                    eid, stats["total"], stats["kept"], stats["merged"], stats["scored"],
                )

        log.info("=" * 60)
        log.info(
            "DONE. observations: %d → %d kept (%d merged), %d scored across %d entities",
            totals["total"], totals["kept"], totals["merged"], totals["scored"],
            totals["entities_touched"],
        )
        if dry_run:
            log.info("Re-run with --apply to actually write changes.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
