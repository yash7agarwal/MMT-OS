"""SQLAlchemy engine, session, and Base setup.

Dual-mode:
  - **Local dev** (no DATABASE_URL set): single SQLite file under webapp/data/.
  - **Production** (Railway, Neon, etc.): Postgres via DATABASE_URL env.

Pick-up logic:
  1. If DATABASE_URL is set, use it. Normalize `postgres://` → `postgresql://`
     (Railway emits the legacy scheme; SQLAlchemy 2+ rejects it).
  2. Otherwise fall back to the local SQLite file. Keeps `python -m uvicorn ...`
     on a fresh checkout working without any env setup.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
(_DATA_DIR / "screenshots").mkdir(exist_ok=True)


def _resolve_database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "").strip()
    if raw:
        # Railway still emits the legacy "postgres://" prefix; SQLAlchemy 2+
        # requires "postgresql://" (or a driver-qualified variant).
        if raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://"):]
        return raw
    return f"sqlite:///{_DATA_DIR / 'appuat.db'}"


DATABASE_URL = _resolve_database_url()
_is_sqlite = DATABASE_URL.startswith("sqlite")

# SQLite needs check_same_thread=False for FastAPI's threadpool access;
# Postgres drivers don't need (and reject) that kwarg.
_engine_kwargs: dict = {"echo": False}
if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Reasonable pool defaults for Railway's small Postgres — Railway keeps
    # connections cheap but has a max; stay well under it.
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = 5
    _engine_kwargs["max_overflow"] = 10

engine = create_engine(DATABASE_URL, **_engine_kwargs)

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

    Backend-agnostic: uses SQLAlchemy's inspector for schema introspection and
    only runs SQL that both SQLite and Postgres understand. For real multi-
    developer schema management, switch to Alembic — this file handles the
    solo-PM case safely and avoids data loss on upgrade.
    """
    import logging
    from sqlalchemy import inspect, text
    from webapp.api import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    log = logging.getLogger(__name__)

    # Add columns introduced after initial create_all. Both SQLite and
    # Postgres accept `ALTER TABLE ... ADD COLUMN ...` and `DEFAULT 'val'`.
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
    if "knowledge_entities" in inspector.get_table_names():
        _dedup_knowledge_entities()
        _ensure_knowledge_indexes(log)
    if "knowledge_observations" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("knowledge_observations")}
        with engine.begin() as conn:
            if "lens_tags" not in existing_cols:
                # JSON type: Postgres resolves to jsonb, SQLite stores as TEXT.
                conn.execute(text("ALTER TABLE knowledge_observations ADD COLUMN lens_tags JSON"))

    # Phase-1 research-architecture columns.
    if "knowledge_entities" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("knowledge_entities")}
        with engine.begin() as conn:
            if "user_signal" not in existing_cols:
                conn.execute(text("ALTER TABLE knowledge_entities ADD COLUMN user_signal VARCHAR(20)"))
            if "dismissed_reason" not in existing_cols:
                conn.execute(text("ALTER TABLE knowledge_entities ADD COLUMN dismissed_reason TEXT"))
            if "decay_state" not in existing_cols:
                conn.execute(text("ALTER TABLE knowledge_entities ADD COLUMN decay_state VARCHAR(30)"))
    if "agent_sessions" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("agent_sessions")}
        with engine.begin() as conn:
            if "quality_score_json" not in existing_cols:
                conn.execute(text("ALTER TABLE agent_sessions ADD COLUMN quality_score_json JSON"))

    # v0.20.0 — heartbeat column on work_items so the UI can distinguish
    # "actively researching" from "stuck in_progress for 4 days".
    if "work_items" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("work_items")}
        with engine.begin() as conn:
            if "last_progress_at" not in existing_cols:
                conn.execute(text("ALTER TABLE work_items ADD COLUMN last_progress_at TIMESTAMP"))

    # v0.22.0 — content quality columns on observations.
    if "knowledge_observations" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("knowledge_observations")}
        with engine.begin() as conn:
            if "quality_score" not in existing_cols:
                # Both engines: SQLite stores as REAL, Postgres as DOUBLE PRECISION.
                # NOT NULL with default 0.0 — backfills existing rows safely.
                conn.execute(text(
                    "ALTER TABLE knowledge_observations ADD COLUMN quality_score FLOAT DEFAULT 0.0 NOT NULL"
                ))
            if "dedupe_count" not in existing_cols:
                conn.execute(text(
                    "ALTER TABLE knowledge_observations ADD COLUMN dedupe_count INTEGER DEFAULT 0 NOT NULL"
                ))

    # v0.21.2 — soft-hide flag on projects.
    if "projects" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("projects")}
        with engine.begin() as conn:
            if "is_hidden" not in existing_cols:
                # Backend-agnostic: SQLite stores Boolean as INTEGER, Postgres
                # as BOOLEAN. We use BOOLEAN with a numeric default — both
                # engines coerce 0 / FALSE correctly.
                conn.execute(text(
                    "ALTER TABLE projects ADD COLUMN is_hidden BOOLEAN DEFAULT FALSE NOT NULL"
                ))


SCREENSHOTS_DIR = _DATA_DIR / "screenshots"


def _dedup_knowledge_entities() -> None:
    """Merge duplicate (project_id, canonical_name) rows before the UNIQUE
    index goes on. Idempotent: zero duplicates → does nothing.

    Backend-agnostic: uses Python aggregation instead of SQLite's GROUP_CONCAT
    so the same code path works on Postgres.
    """
    import logging
    from collections import defaultdict
    from sqlalchemy import text

    log = logging.getLogger(__name__)
    with engine.begin() as conn:
        # Backfill canonical_name for legacy rows.
        conn.execute(text(
            "UPDATE knowledge_entities "
            "SET canonical_name = LOWER(TRIM(name)) "
            "WHERE canonical_name IS NULL OR canonical_name = ''"
        ))
        rows = conn.execute(text(
            "SELECT id, project_id, canonical_name FROM knowledge_entities "
            "WHERE canonical_name IS NOT NULL"
        )).fetchall()

        groups: dict[tuple[int, str], list[int]] = defaultdict(list)
        for row in rows:
            groups[(row.project_id, row.canonical_name)].append(row.id)

        dupes = {k: sorted(v) for k, v in groups.items() if len(v) > 1}
        if not dupes:
            return

        merged_count = 0
        for (_pid, _canon), ids in dupes.items():
            keep_id = ids[0]
            drop_ids = ids[1:]
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
            "[init_db] Deduplicated %d knowledge_entities rows across %d groups",
            merged_count, len(dupes),
        )


def _ensure_knowledge_indexes(log) -> None:
    """CREATE INDEX / UNIQUE INDEX IF NOT EXISTS. Both SQLite and Postgres
    understand IF NOT EXISTS, so this is portable.
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
                log.warning("[init_db] Index create skipped: %s — %s", sql.split(" ")[5], exc)
