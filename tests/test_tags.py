"""Tests for tag management."""

from app.core.models import Item
from app.core.service import add_tag_to_item, remove_tag_from_item, list_tags_for_item


def test_add_tag(tmp_db):
    """Adding a tag should create it in the DB."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    add_tag_to_item(tmp_db, item.id, "method/RAG")
    tags = list_tags_for_item(tmp_db, item.id)
    assert "method/RAG" in tags


def test_remove_tag(tmp_db):
    """Removing a tag should delete it from the item."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    add_tag_to_item(tmp_db, item.id, "method/RAG")
    assert "method/RAG" in list_tags_for_item(tmp_db, item.id)

    removed = remove_tag_from_item(tmp_db, item.id, "method/RAG")
    assert removed is True
    assert "method/RAG" not in list_tags_for_item(tmp_db, item.id)


def test_remove_nonexistent_tag(tmp_db):
    """Removing a tag that doesn't exist should return False."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    removed = remove_tag_from_item(tmp_db, item.id, "nonexistent")
    assert removed is False


def test_add_tag_idempotent(tmp_db):
    """Adding the same tag twice should not error and should have one entry."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    link1 = add_tag_to_item(tmp_db, item.id, "method/RAG")
    link2 = add_tag_to_item(tmp_db, item.id, "method/RAG")
    assert link1.id == link2.id  # same link returned

    tags = list_tags_for_item(tmp_db, item.id)
    assert tags.count("method/RAG") == 1


def test_multiple_tags(tmp_db):
    """An item can have multiple tags."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    add_tag_to_item(tmp_db, item.id, "method/RAG")
    add_tag_to_item(tmp_db, item.id, "task/QA")
    add_tag_to_item(tmp_db, item.id, "eval/benchmark")

    tags = list_tags_for_item(tmp_db, item.id)
    assert len(tags) == 3
    assert "method/RAG" in tags
    assert "task/QA" in tags
