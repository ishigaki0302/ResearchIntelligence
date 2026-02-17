"""Tests for watch/inbox functionality."""

import json
from unittest.mock import patch

import pytest

from app.core.models import InboxItem, Item, Watch
from app.core.service import upsert_item
from app.pipelines.watch import accept_inbox_item, run_watch


MOCK_ARXIV_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Retrieval Augmented Generation for NLP</title>
    <summary>We present a novel RAG approach for NLP tasks.</summary>
    <published>2025-12-01T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1" />
    <arxiv:primary_category term="cs.CL" />
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>Dense Passage Retrieval Revisited</title>
    <summary>An improved dense retrieval method.</summary>
    <published>2025-11-20T00:00:00Z</published>
    <author><name>Carol White</name></author>
    <link title="pdf" href="http://arxiv.org/pdf/2401.00002v1" />
    <arxiv:primary_category term="cs.IR" />
  </entry>
</feed>
"""

MOCK_OPENALEX_RESULTS = [
    {
        "id": "https://openalex.org/W123456",
        "title": "A Study on Transformers",
        "doi": "https://doi.org/10.1234/test.2024",
        "publication_year": 2024,
        "authorships": [
            {"author": {"display_name": "Dan Brown"}},
        ],
        "primary_location": {"source": {"display_name": "NeurIPS"}},
        "abstract_inverted_index": {"A": [0], "study": [1], "on": [2], "transformers": [3]},
    },
]


class TestWatchCreate:
    def test_watch_create(self, tmp_db):
        session = tmp_db
        watch = Watch(name="test-watch", source="arxiv", query="RAG")
        session.add(watch)
        session.commit()

        assert watch.id is not None
        assert watch.name == "test-watch"
        assert watch.enabled is True


class TestWatchRunArxiv:
    @patch("app.connectors.arxiv.requests.get")
    def test_watch_run_arxiv(self, mock_get, tmp_db):
        session = tmp_db

        # Mock arXiv response
        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.text = MOCK_ARXIV_XML
        mock_resp.raise_for_status = lambda: None

        watch = Watch(name="arxiv-rag", source="arxiv", query="RAG")
        session.add(watch)
        session.flush()

        result = run_watch(session, watch, since_days=365, limit=100)
        session.commit()

        assert result["fetched"] == 2
        assert result["added"] == 2
        assert result["skipped"] == 0

        # Verify inbox items
        items = session.query(InboxItem).all()
        assert len(items) == 2
        assert items[0].status == "new"


class TestWatchRunOpenalex:
    @patch("app.connectors.openalex.requests.get")
    def test_watch_run_openalex(self, mock_get, tmp_db):
        session = tmp_db

        # Mock OpenAlex response
        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": MOCK_OPENALEX_RESULTS}
        mock_resp.raise_for_status = lambda: None

        watch = Watch(name="oa-transformers", source="openalex", query="transformers")
        session.add(watch)
        session.flush()

        result = run_watch(session, watch, since_days=30, limit=100)
        session.commit()

        assert result["fetched"] == 1
        assert result["added"] == 1

        inbox = session.query(InboxItem).first()
        assert inbox.title == "A Study on Transformers"


class TestWatchIdempotent:
    @patch("app.connectors.arxiv.requests.get")
    def test_watch_run_idempotent(self, mock_get, tmp_db):
        session = tmp_db

        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.text = MOCK_ARXIV_XML
        mock_resp.raise_for_status = lambda: None

        watch = Watch(name="dedup-test", source="arxiv", query="RAG")
        session.add(watch)
        session.flush()

        result1 = run_watch(session, watch, since_days=365, limit=100)
        session.commit()
        assert result1["added"] == 2

        # Run again — should skip all
        result2 = run_watch(session, watch, since_days=365, limit=100)
        session.commit()
        assert result2["added"] == 0
        assert result2["skipped"] == 2

        assert session.query(InboxItem).count() == 2


class TestInboxAccept:
    def test_inbox_accept(self, tmp_db):
        session = tmp_db

        watch = Watch(name="accept-test", source="arxiv", query="test")
        session.add(watch)
        session.flush()

        inbox_item = InboxItem(
            watch_id=watch.id,
            source_id_type="arxiv",
            source_id_value="2401.99999",
            title="Test Paper for Accept",
            authors_json=json.dumps(["Author A", "Author B"]),
            year=2024,
            venue="arXiv",
            url="http://arxiv.org/abs/2401.99999",
            abstract="A test abstract.",
            dedup_hash="abc123",
        )
        session.add(inbox_item)
        session.flush()

        item = accept_inbox_item(session, inbox_item)
        session.commit()

        assert inbox_item.status == "accepted"
        assert inbox_item.accepted_item_id == item.id
        assert item.title == "Test Paper for Accept"

        # Check collection
        assert any(cl.collection.name == "watch:accept-test" for cl in item.collection_links)

    def test_inbox_accept_idempotent(self, tmp_db):
        """Accepting an item that already exists in items table should not create duplicate."""
        session = tmp_db

        # Pre-create the item in main DB
        existing, _ = upsert_item(
            session,
            title="Already Exists",
            year=2024,
            external_ids={"arxiv": "2401.88888"},
        )
        session.commit()

        watch = Watch(name="idem-test", source="arxiv", query="test")
        session.add(watch)
        session.flush()

        inbox_item = InboxItem(
            watch_id=watch.id,
            source_id_type="arxiv",
            source_id_value="2401.88888",
            title="Already Exists",
            authors_json=json.dumps([]),
            year=2024,
            dedup_hash="def456",
        )
        session.add(inbox_item)
        session.flush()

        item = accept_inbox_item(session, inbox_item)
        session.commit()

        # Should link to existing item, not create new
        assert item.id == existing.id
        assert inbox_item.accepted_item_id == existing.id


class TestInboxReject:
    def test_inbox_reject(self, tmp_db):
        session = tmp_db

        watch = Watch(name="reject-test", source="arxiv", query="test")
        session.add(watch)
        session.flush()

        inbox_item = InboxItem(
            watch_id=watch.id,
            source_id_type="arxiv",
            source_id_value="2401.77777",
            title="Paper to Reject",
            dedup_hash="ghi789",
        )
        session.add(inbox_item)
        session.flush()

        inbox_item.status = "rejected"
        session.commit()

        refreshed = session.get(InboxItem, inbox_item.id)
        assert refreshed.status == "rejected"
