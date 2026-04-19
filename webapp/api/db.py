"""SQLAlchemy engine, session, and Base setup. Single SQLite file under webapp/data/."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
(_DATA_DIR / "screenshots").mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{_DATA_DIR / 'appuat.db'}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables and run lightweight column + index migrations.

    SQLite-only. For real schema management, use Alembic. For an MVP single-user
    app this is sufficient and avoids data loss.
    """
    import logging
    from sqlalchemy import inspect, text
    from webapp.api import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    log = logging.getLogger(__name__)

    # Add columns that were introduced after initial create_all
    inspector = inspect(engine)
    if "screens" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("screens")}
        with engine.begin() as conn:
            if "context_hints" not in existing_cols:
                conn.execute(text("ALTER TABLE screens ADD COLUMN context_hints TEXT"))
    if "test_plans" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("test_plans")}
        with engine.begin() as conn:
            if "plan_type" not in existing_cols:
                conn.execute(text(
                    "ALTER TABLE test_plans ADD COLUMN plan_type VARCHAR(50) DEFAULT 'feature_flow'"
                ))

    # v0.10.1 — dedup + indexes on the knowledge graph.
    # SQLAlchemy's create_all covers fresh installs; these statements upgrade
    # existing DBs without breaking them.
    if "knowledge_entities" in inspector.get_table_names():
        _dedup_knowledge_entities()
        _ensure_knowledge_indexes(log)
    if "knowledge_observations" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("knowledge_observations")}
        with engine.begin() as conn:
            if "lens_tags" not in existing_cols:
                conn.execute(text("ALTER TABLE knowledge_observations ADD COLUMN lens_tags JSON"))


SCREENSHOTS_DIR = _DATA_DIR / "screenshots"


def _dedup_knowledge_entities() -> None:
    """Merge duplicate (project_id, name) rows in knowledge_entities before the
    UNIQUE index goes on. Keeps the lowest id, rewrites FKs on observations,
    relations (both ends), and screenshots, then deletes the redundant rows.

    Idempotent: finds zero duplicates → does nothing.
    """
    import logging
    from sqlalchemy import text

    log = logging.getLogger(__name__)
    with engine.begin() as conn:
        # Backfill canonical_name for legacy rows (pre-v0.9 writes didn't set it).
        conn.execute(text(
            "UPDATE knowledge_entities "
            "SET canonical_name = LOWER(TRIM(name)) "
            "WHERE canonical_name IS NULL OR canonical_name = ''"
        ))
        dupes = conn.execute(text(
            """
            SELECT project_id, canonical_name, MIN(id) AS keep_id,
                   GROUP_CONCAT(id) AS all_ids
              FROM knowledge_entities
             WHERE canonical_name IS NOT NULL
             GROUP BY project_id, canonical_name
            HAVING COUNT(*) > 1
            """
        )).fetchall()

        if not dupes:
            return

        merged_count = 0
        for row in dupes:
            keep_id = row.keep_id
            drop_ids = [int(x) for x in row.all_ids.split(",") if int(x) != keep_id]
            if not drop_ids:
                continue
            placeholders = ",".join(str(i) for i in drop_ids)
            conn.execute(text(
                f"UPDATE knowledge_observations SET entity_id = :k WHERE entity_id IN ({placeholders})"
            ), {"k": keep_id})
            conn.execute(text(
                f"UPDATE knowledge_relations SET from_entity_id = :k WHERE from_entity_id IN ({placeholders})"
            ), {"k": keep_id})
            conn.execute(text(
                f"UPDATE knowledge_relations SET to_entity_id = :k WHERE to_entity_id IN ({placeholders})"
            ), {"k": keep_id})
            conn.execute(text(
                f"UPDATE knowledge_screenshots SET entity_id = :k WHERE entity_id IN ({placeholders})"
            ), {"k": keep_id})
            conn.execute(text(
                f"DELETE FROM knowledge_entities WHERE id IN ({placeholders})"
            ))
            merged_count += len(drop_ids)

        log.info(
            "[init_db] Deduplicated %d knowledge_entities rows across %d (project_id, name) groups",
            merged_count, len(dupes),
        )


def _ensure_knowledge_indexes(log) -> None:
    """CREATE INDEX / UNIQUE INDEX IF NOT EXISTS for knowledge-graph hot paths.

    create_all() would create these for fresh DBs, but existing installs still
    need the upgrade. IF NOT EXISTS makes this safely idempotent.
    """
    from sqlalchemy import text
    statements = [
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_entities_project_canonical "
        "ON knowledge_entities(project_id, canonical_name)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_entities_project_type "
        "ON knowledge_entities(project_id, entity_type)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_observations_entity_type "
        "ON knowledge_observations(entity_id, observation_type)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_relations_from "
        "ON knowledge_relations(from_entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_relations_to "
        "ON knowledge_relations(to_entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_artifacts_project_type "
        "ON knowledge_artifacts(project_id, artifact_type)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_screenshots_project "
        "ON knowledge_screenshots(project_id)",
        "CREATE INDEX IF NOT EXISTS ix_knowledge_screenshots_entity "
        "ON knowledge_screenshots(entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_work_items_agent_project_status "
        "ON work_items(agent_type, project_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_agent_sessions_project_started "
        "ON agent_sessions(project_id, started_at)",
    ]
    with engine.begin() as conn:
        for sql in statements:
            try:
                conn.execute(text(sql))
            except Exception as exc:
                # UNIQUE may fail if dedup missed a case; log and continue so we
                # don't brick startup on a migration edge case.
                log.warning("[init_db] Index create skipped: %s — %s", sql.split(" ")[5], exc)
