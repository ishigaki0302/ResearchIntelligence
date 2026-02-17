"""Tests for P19 — incremental indexing."""

from app.core.service import upsert_item
from app.indexing.engine import _compute_text_hash


def test_text_hash_computation(tmp_db):
    """text_hash should be deterministic SHA256 of title+abstract."""
    session = tmp_db
    item, _ = upsert_item(
        session,
        title="Test Paper",
        authors=["Author"],
        year=2024,
        abstract="This is a test abstract.",
    )
    session.commit()

    h1 = _compute_text_hash(item)
    h2 = _compute_text_hash(item)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex digest length


def test_text_hash_changes_on_content_change(tmp_db):
    """text_hash should change when title or abstract changes."""
    session = tmp_db
    item, _ = upsert_item(
        session,
        title="Original Title",
        authors=["Author"],
        year=2024,
        abstract="Original abstract.",
    )
    session.commit()

    h_original = _compute_text_hash(item)

    item.abstract = "Updated abstract."
    h_updated = _compute_text_hash(item)

    assert h_original != h_updated


def test_skip_unchanged(tmp_db):
    """Incremental index should skip items with matching text_hash."""
    session = tmp_db
    item, _ = upsert_item(
        session,
        title="Stable Paper",
        authors=["Author"],
        year=2024,
        abstract="Stable abstract content.",
    )
    # Set text_hash as if previously indexed
    item.text_hash = _compute_text_hash(item)
    session.commit()

    # Re-compute — should be unchanged
    new_hash = _compute_text_hash(item)
    assert item.text_hash == new_hash


def test_detect_changed(tmp_db):
    """Incremental index should detect items with stale text_hash."""
    session = tmp_db
    item, _ = upsert_item(
        session,
        title="Paper Will Change",
        authors=["Author"],
        year=2024,
        abstract="Original abstract.",
    )
    item.text_hash = _compute_text_hash(item)
    session.commit()

    # Modify the item
    item.abstract = "Updated abstract with new content."
    session.flush()

    new_hash = _compute_text_hash(item)
    assert item.text_hash != new_hash  # Hash should differ
