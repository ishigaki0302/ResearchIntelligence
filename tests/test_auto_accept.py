"""Tests for the auto-accept pipeline."""

import json

from app.core.models import InboxItem, Watch
from app.pipelines.auto_accept import (
    QualityFlags,
    apply_auto_accept,
    compute_auto_accept_score,
    compute_quality_flags,
    evaluate_auto_accept,
)


def _create_watch(session):
    watch = Watch(name="test-watch", source="arxiv", query="machine learning transformers")
    session.add(watch)
    session.flush()
    return watch


def _create_inbox_item(session, watch, **kwargs):
    defaults = {
        "watch_id": watch.id,
        "source_id_type": "arxiv",
        "source_id_value": f"2024.{session.execute(
            __import__('sqlalchemy').select(__import__('sqlalchemy').func.count(InboxItem.id))
        ).scalar() + 1:05d}",
        "title": "A Good Paper on Machine Learning Transformers",
        "authors_json": json.dumps(["Alice Smith", "Bob Jones"]),
        "year": 2024,
        "venue": "ACL",
        "abstract": "This paper proposes a novel approach to improve transformer models for NLP tasks.",
        "status": "new",
        "recommend_score": 0.8,
    }
    defaults.update(kwargs)
    item = InboxItem(**defaults)
    session.add(item)
    session.flush()
    return item


def test_quality_flags_short_abstract(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    item = _create_inbox_item(session, watch, abstract="Short.")
    flags = compute_quality_flags(session, item)
    assert flags.too_short_abstract is True


def test_quality_flags_missing_authors(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    item = _create_inbox_item(session, watch, authors_json=json.dumps([]))
    flags = compute_quality_flags(session, item)
    assert flags.missing_authors is True


def test_quality_flags_suspicious_title(tmp_db):
    session = tmp_db
    watch = _create_watch(session)

    # All-caps title
    item1 = _create_inbox_item(session, watch, title="ALL CAPS TITLE HERE", source_id_value="2024.00001")
    flags1 = compute_quality_flags(session, item1)
    assert flags1.suspicious_title is True

    # Very short title
    item2 = _create_inbox_item(session, watch, title="Short", source_id_value="2024.00002")
    flags2 = compute_quality_flags(session, item2)
    assert flags2.suspicious_title is True


def test_quality_flags_clean(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    item = _create_inbox_item(session, watch)
    flags = compute_quality_flags(session, item)
    assert flags.count == 0


def test_auto_accept_score_calculation(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    item = _create_inbox_item(session, watch, recommend_score=0.8)
    flags = QualityFlags()
    score = compute_auto_accept_score(session, item, flags)
    # Should be recommend_score (0.8) + watch keyword boost (0.1) = 0.9
    assert score >= 0.8
    assert score <= 1.0


def test_auto_accept_score_penalized_by_flags(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    item = _create_inbox_item(session, watch, recommend_score=0.6)
    flags = QualityFlags(too_short_abstract=True, missing_authors=True)
    score = compute_auto_accept_score(session, item, flags)
    # Should be 0.6 + possible boost - 0.3 penalty
    assert score < 0.6


def test_evaluate_dry_run(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    _create_inbox_item(session, watch, source_id_value="2024.10001")
    _create_inbox_item(session, watch, source_id_value="2024.10002")
    session.flush()

    results = evaluate_auto_accept(session, threshold=0.75)
    assert len(results) == 2
    # Verify no side effects — items still status="new"
    for r in results:
        inbox = session.get(InboxItem, r["inbox_id"])
        assert inbox.status == "new"
        assert inbox.auto_accept is not True


def test_apply_auto_accept(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    # High-scoring item
    item = _create_inbox_item(session, watch, source_id_value="2024.20001", recommend_score=0.9)
    session.flush()

    result = apply_auto_accept(session, threshold=0.5)
    session.commit()
    assert result["accepted"] >= 1
    # Verify the inbox item is now accepted
    refreshed = session.get(InboxItem, item.id)
    assert refreshed.status == "accepted"
    assert refreshed.auto_accept is True
    assert refreshed.accepted_item_id is not None


def test_respects_limit(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    for i in range(5):
        _create_inbox_item(session, watch, source_id_value=f"2024.3000{i}")
    session.flush()

    results = evaluate_auto_accept(session, threshold=0.0, limit=2)
    assert len(results) == 2


def test_skips_flagged_items(tmp_db):
    session = tmp_db
    watch = _create_watch(session)
    # Item with quality issues — short abstract and no authors
    item = _create_inbox_item(
        session,
        watch,
        source_id_value="2024.40001",
        abstract="Short",
        authors_json=json.dumps([]),
        recommend_score=0.9,
    )
    session.flush()

    result = apply_auto_accept(session, threshold=0.5)
    assert result["skipped"] >= 1
    refreshed = session.get(InboxItem, item.id)
    assert refreshed.status == "new"  # Not accepted
