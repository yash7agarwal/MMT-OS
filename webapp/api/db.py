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
    """Create all tables and run lightweight column migrations.

    SQLite-only. For real schema management, use Alembic. For an MVP single-user
    app this is sufficient and avoids data loss.
    """
    from sqlalchemy import inspect, text
    from webapp.api import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Add columns that were introduced after initial create_all
    inspector = inspect(engine)
    if "screens" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("screens")}
        with engine.begin() as conn:
            if "context_hints" not in existing_cols:
                conn.execute(text("ALTER TABLE screens ADD COLUMN context_hints TEXT"))


SCREENSHOTS_DIR = _DATA_DIR / "screenshots"
