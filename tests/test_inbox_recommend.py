"""Tests for inbox recommendation scoring and auto-tagging."""

import json

from sqlalchemy import select

from app.core.models import InboxItem, Item, ItemTag, Watch
from app.pipelines.inbox_recommend import _generate_auto_tags, apply_auto_tags_on_accept, recommend_inbox_items


def _create_watch(session, name="test-watch"):
    watch = Watch(name=name, source="arxiv", query="test query")
    session.add(watch)
    session.flush()
    return watch


def _create_inbox_item(session, watch, title="Test Paper", venue=None, year=2025, score=None):
    inbox_item = InboxItem(
        watch_id=watch.id,
        title=title,
        source_id_type="arxiv",
        source_id_value=f"2501.{session.execute(select(InboxItem)).scalars().all().__len__():05d}",
        year=year,
        venue=venue,
        score=score,
        matched_query=watch.query,
        authors_json=json.dumps(["Alice Smith", "Bob Jones"]),
    )
    session.add(inbox_item)
    session.flush()
    return inbox_item


def test_recommend_scoring(tmp_db):
    """Inbox items should receive recommendation scores."""
    watch = _create_watch(tmp_db)
    inbox1 = _create_inbox_item(tmp_db, watch, title="Paper A", score=0.8, year=2025)
    inbox2 = _create_inbox_item(tmp_db, watch, title="Paper B", score=0.2, year=2020)

    result = recommend_inbox_items(tmp_db, threshold=0.3)
    assert result["recommended"] + result["skipped"] == 2

    # Both should have scores set
    assert inbox1.recommend_score is not None
    assert inbox2.recommend_score is not None
    # Paper A (high score, recent) should score higher
    assert inbox1.recommend_score >= inbox2.recommend_score


def test_recommend_venue_match(tmp_db):
    """Items from known venues should score higher."""
    # Add existing item with venue
    existing = Item(title="Existing Paper", year=2024, venue="ACL")
    tmp_db.add(existing)
    tmp_db.flush()

    watch = _create_watch(tmp_db)
    inbox1 = _create_inbox_item(tmp_db, watch, title="Paper A", venue="ACL")
    inbox2 = _create_inbox_item(tmp_db, watch, title="Paper B", venue="UnknownConf")

    recommend_inbox_items(tmp_db, threshold=0.0)

    assert inbox1.recommend_score > inbox2.recommend_score


def test_auto_tags_from_watch(tmp_db):
    """Auto-tags should be generated from watch name."""
    watch = _create_watch(tmp_db, name="rag-papers")
    inbox_item = _create_inbox_item(tmp_db, watch, title="RAG Paper", venue="ACL")

    tags = _generate_auto_tags(tmp_db, inbox_item)
    assert any("topic/" in t for t in tags)
    assert any("venue/" in t for t in tags)


def test_apply_tags_on_accept(tmp_db):
    """Auto-tags should be applied to accepted items."""
    watch = _create_watch(tmp_db, name="rag-papers")
    inbox_item = _create_inbox_item(tmp_db, watch, title="RAG Paper", venue="ACL")
    inbox_item.auto_tags_json = json.dumps(["topic/rag", "venue/ACL"])
    tmp_db.flush()

    item = Item(title="RAG Paper", year=2025)
    tmp_db.add(item)
    tmp_db.flush()

    applied = apply_auto_tags_on_accept(tmp_db, inbox_item, item)
    assert len(applied) == 2
    assert "topic/rag" in applied

    # Verify tags in DB
    tag_links = tmp_db.execute(select(ItemTag).where(ItemTag.item_id == item.id)).scalars().all()
    assert len(tag_links) == 2
    assert all(t.source == "auto" for t in tag_links)


def test_recommend_no_items(tmp_db):
    """Recommend with no inbox items should return zeros."""
    result = recommend_inbox_items(tmp_db)
    assert result["recommended"] == 0
    assert result["skipped"] == 0
