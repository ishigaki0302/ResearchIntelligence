"""Tests for trend analytics."""

import json
from pathlib import Path

import pytest

from app.analytics.trends import (
    items_by_year_venue,
    items_by_year_collection,
    items_by_year_tag,
    top_keyphrases_by_year,
)
from app.core.service import (
    add_item_to_collection,
    add_tag_to_item,
    get_or_create_collection,
    upsert_item,
)


class TestItemsByYearVenue:
    def test_items_by_year_venue(self, tmp_db):
        session = tmp_db

        upsert_item(session, title="Paper A", year=2023, venue="ACL")
        upsert_item(session, title="Paper B", year=2023, venue="ACL")
        upsert_item(session, title="Paper C", year=2024, venue="EMNLP")
        session.commit()

        result = items_by_year_venue(session)
        assert len(result) == 2

        acl_2023 = [r for r in result if r["year"] == 2023 and r["venue"] == "ACL"]
        assert len(acl_2023) == 1
        assert acl_2023[0]["count"] == 2

        emnlp_2024 = [r for r in result if r["year"] == 2024 and r["venue"] == "EMNLP"]
        assert len(emnlp_2024) == 1
        assert emnlp_2024[0]["count"] == 1


class TestTopKeyphrases:
    def test_top_keyphrases(self, tmp_db):
        session = tmp_db

        # Need at least 2 items per year for TF-IDF
        upsert_item(
            session,
            title="Retrieval Augmented Generation for Question Answering",
            year=2024,
            abstract="We propose a retrieval augmented generation method for QA tasks.",
        )
        upsert_item(
            session,
            title="Dense Passage Retrieval for Open-Domain QA",
            year=2024,
            abstract="A dense retrieval approach for open-domain question answering.",
        )
        upsert_item(
            session,
            title="Transformer Models for Language Understanding",
            year=2024,
            abstract="We study transformer architectures for natural language understanding.",
        )
        session.commit()

        result = top_keyphrases_by_year(session, top_n=5)
        assert len(result) > 0
        assert all(r["year"] == 2024 for r in result)
        assert all(isinstance(r["phrase"], str) for r in result)
        assert all(isinstance(r["score"], float) for r in result)


class TestAnalyticsExport:
    def test_export_structure(self, tmp_db):
        """Verify all trend functions return proper structure."""
        session = tmp_db

        upsert_item(session, title="Paper 1", year=2024, venue="ACL")
        item2, _ = upsert_item(session, title="Paper 2", year=2024, venue="EMNLP")
        session.commit()

        add_tag_to_item(session, item2.id, "method/RAG")
        coll = get_or_create_collection(session, "test-coll")
        add_item_to_collection(session, item2, coll)
        session.commit()

        yv = items_by_year_venue(session)
        yc = items_by_year_collection(session)
        yt = items_by_year_tag(session)

        assert isinstance(yv, list)
        assert isinstance(yc, list)
        assert isinstance(yt, list)

        # year_venue should have 2 entries (ACL 2024, EMNLP 2024)
        assert len(yv) == 2

        # year_collection should have 1 entry
        assert len(yc) == 1
        assert yc[0]["collection"] == "test-coll"

        # year_tag should have 1 entry
        assert len(yt) == 1
        assert yt[0]["tag"] == "method/RAG"
