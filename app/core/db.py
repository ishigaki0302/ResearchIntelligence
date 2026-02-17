"""Database session management."""

from pathlib import Path

from sqlalchemy import create_engine, event as sa_event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import resolve_path, get_config
from app.core.models import Base

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


def init_db(db_path: Path | None = None):
    """Create all tables. Safe to call multiple times."""
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
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
