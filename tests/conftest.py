"""Test fixtures."""

import tempfile
from pathlib import Path

import pytest

from app.core.config import REPO_ROOT


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database for testing."""
    from app.core.db import reset_engine, get_engine, init_db, get_session

    reset_engine()
    db_path = tmp_path / "test.sqlite"

    # Patch config to use tmp_path
    import app.core.config as cfg_mod
    original_root = cfg_mod.REPO_ROOT

    # Override resolve_path for testing
    original_resolve = cfg_mod.resolve_path
    cfg_mod.REPO_ROOT = tmp_path

    # Create directory structure
    (tmp_path / "data" / "library" / "papers").mkdir(parents=True)
    (tmp_path / "data" / "cache" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "cache" / "embeddings").mkdir(parents=True)
    (tmp_path / "db").mkdir(parents=True)

    # Override config
    test_config = {
        "storage": {
            "base_dir": "data",
            "library_dir": "data/library/papers",
            "cache_raw_dir": "data/cache/raw",
            "cache_embeddings_dir": "data/cache/embeddings",
            "db_path": "db/test.sqlite",
        },
        "embedding": {
            "backend": "sentence-transformers",
            "model": "all-MiniLM-L6-v2",
            "dimension": 384,
        },
        "indexing": {
            "bm25_backend": "fts5",
            "faiss_index_path": "data/cache/embeddings/faiss.index",
            "faiss_id_map_path": "data/cache/embeddings/faiss_ids.json",
        },
        "search": {"default_top_k": 20, "bm25_weight": 0.5, "vector_weight": 0.5},
    }
    import app.core.config as config_mod
    config_mod.get_config._cache = test_config

    init_db(db_path)
    session = get_session(db_path)

    yield session

    session.close()
    reset_engine()
    cfg_mod.REPO_ROOT = original_root
    if hasattr(config_mod.get_config, "_cache"):
        del config_mod.get_config._cache
