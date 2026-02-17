"""Database session management."""

import logging
from pathlib import Path

from sqlalchemy import create_engine, event as sa_event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import resolve_path, get_config
from app.core.models import Base

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def get_engine(db_path: Path | None = None):
    global _engine
    if _engine is None:
        if db_path is None:
            cfg = get_config()
            db_path = resolve_path(cfg["storage"]["db_path"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{db_path}", echo=False)
        # Enable WAL mode and foreign keys
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


def _migrate_add_columns(engine):
    """Add new columns to existing tables if missing (v0.4 migration)."""
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
                logger.info(f"Migration: added {table}.{column}")
        conn.commit()


def init_db(db_path: Path | None = None):
    """Create all tables. Safe to call multiple times."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    _migrate_add_columns(engine)
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
