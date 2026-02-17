"""Database session management and migration framework."""

import logging
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_config, resolve_path
from app.core.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None

SCHEMA_VERSION = 3  # Current schema version


def get_engine(db_path: Path | None = None):
    global _engine
    if _engine is None:
        if db_path is None:
            cfg = get_config()
            db_path = resolve_path(cfg["storage"]["db_path"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)

        @sa_event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session_factory(db_path: Path | None = None) -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine(db_path)
        _SessionLocal = sessionmaker(bind=engine)
    return _SessionLocal


def get_session(db_path: Path | None = None) -> Session:
    """Get a new session. Caller is responsible for closing."""
    factory = get_session_factory(db_path)
    return factory()


def _ensure_schema_version_table(engine):
    """Create schema_version table if it doesn't exist."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                description TEXT
            )
        """))
        conn.commit()


def _get_current_version(engine) -> int:
    """Get the current schema version (0 if no migrations recorded)."""
    with engine.connect() as conn:
        try:
            row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
            return row or 0
        except Exception:
            return 0


def _record_version(engine, version: int, description: str):
    """Record a migration version."""
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO schema_version (version, description) VALUES (:v, :d)"),
            {"v": version, "d": description},
        )
        conn.commit()


# Migration definitions: version -> (description, callable)
def _migration_v1(engine):
    """v0.4 column additions (inbox_items, citations)."""
    insp = inspect(engine)
    migrations = [
        ("inbox_items", "recommended", "BOOLEAN DEFAULT 0"),
        ("inbox_items", "recommend_score", "FLOAT"),
        ("inbox_items", "reasons_json", "TEXT"),
        ("inbox_items", "auto_tags_json", "TEXT"),
        ("citations", "raw_cite_hash", "VARCHAR(64)"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            if table not in insp.get_table_names():
                continue
            existing = [c["name"] for c in insp.get_columns(table)]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info(f"Migration v1: added {table}.{column}")
        conn.commit()


def _migration_v2(engine):
    """v0.5: add summary/started_at/finished_at to jobs table."""
    insp = inspect(engine)
    migrations = [
        ("jobs", "summary_json", "TEXT"),
        ("jobs", "started_at", "DATETIME"),
        ("jobs", "finished_at", "DATETIME"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            if table not in insp.get_table_names():
                continue
            existing = [c["name"] for c in insp.get_columns(table)]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info(f"Migration v2: added {table}.{column}")
        conn.commit()


def _migration_v3(engine):
    """v0.6: auto-accept columns on inbox_items, dedup columns on items."""
    insp = inspect(engine)
    migrations = [
        ("inbox_items", "auto_accept", "BOOLEAN DEFAULT 0"),
        ("inbox_items", "auto_accept_score", "FLOAT"),
        ("inbox_items", "quality_flags_json", "TEXT"),
        ("items", "text_hash", "VARCHAR(64)"),
        ("items", "status", "VARCHAR(16) DEFAULT 'active'"),
        ("items", "merged_into_id", "INTEGER REFERENCES items(id) ON DELETE SET NULL"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            if table not in insp.get_table_names():
                continue
            existing = [c["name"] for c in insp.get_columns(table)]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info(f"Migration v3: added {table}.{column}")
        conn.commit()


MIGRATIONS = {
    1: ("v0.4 column additions", _migration_v1),
    2: ("v0.5 job summary + timestamps", _migration_v2),
    3: ("v0.6 auto-accept + dedup columns", _migration_v3),
}


def run_migrations(engine) -> list[int]:
    """Run all pending migrations. Returns list of applied version numbers."""
    _ensure_schema_version_table(engine)
    current = _get_current_version(engine)
    applied = []

    for ver in sorted(MIGRATIONS.keys()):
        if ver <= current:
            continue
        desc, func = MIGRATIONS[ver]
        logger.info(f"Applying migration v{ver}: {desc}")
        func(engine)
        _record_version(engine, ver, desc)
        applied.append(ver)

    return applied


def get_schema_version(engine) -> int:
    """Get current schema version."""
    _ensure_schema_version_table(engine)
    return _get_current_version(engine)


def init_db(db_path: Path | None = None):
    """Create all tables and run migrations. Safe to call multiple times."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    run_migrations(engine)
    # Create FTS5 virtual table for full-text search
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                title, abstract, content,
                content='', content_rowid='rowid'
            )
        """))
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                title, body,
                content='', content_rowid='rowid'
            )
        """))
        conn.commit()


def reset_engine():
    """Reset cached engine/session (for testing)."""
    global _engine, _SessionLocal
    if _engine:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
