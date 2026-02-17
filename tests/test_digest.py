"""Tests for digest generation (P13)."""

from datetime import datetime, timezone

from app.analytics.digest import generate_digest
from app.core.models import InboxItem, Watch


class TestDigest:
    def test_digest_empty(self, tmp_db):
        """Digest with no data should still produce valid output."""
        result = generate_digest(tmp_db, since="7d")
        assert "markdown" in result
        assert "data" in result
        assert result["data"]["summary"]["total_discovered"] == 0

    def test_digest_with_inbox_items(self, tmp_db):
        """Digest should count inbox items correctly."""
        session = tmp_db

        watch = Watch(name="digest-test", source="arxiv", query="test", enabled=True)
        session.add(watch)
        session.flush()

        # Add inbox items
        for i in range(5):
            item = InboxItem(
                watch_id=watch.id,
                title=f"Paper {i}",
                status="new" if i < 3 else "accepted",
                recommended=(i < 2),
                recommend_score=0.8 if i < 2 else 0.3,
                discovered_at=datetime.now(timezone.utc),
                dedup_hash=f"hash{i}",
            )
            session.add(item)
        session.commit()

        result = generate_digest(session, since="7d")
        summary = result["data"]["summary"]
        assert summary["total_discovered"] == 5
        assert summary["total_recommended"] == 2
        assert summary["total_accepted"] == 2
        assert summary["total_new"] == 3

    def test_digest_by_watch(self, tmp_db):
        """Digest with watch filter should only show that watch."""
        session = tmp_db

        w1 = Watch(name="watch-a", source="arxiv", query="alpha", enabled=True)
        w2 = Watch(name="watch-b", source="arxiv", query="beta", enabled=True)
        session.add_all([w1, w2])
        session.flush()

        for i in range(3):
            session.add(
                InboxItem(
                    watch_id=w1.id,
                    title=f"Alpha {i}",
                    status="new",
                    discovered_at=datetime.now(timezone.utc),
                    dedup_hash=f"a{i}",
                )
            )
        for i in range(2):
            session.add(
                InboxItem(
                    watch_id=w2.id,
                    title=f"Beta {i}",
                    status="new",
                    discovered_at=datetime.now(timezone.utc),
                    dedup_hash=f"b{i}",
                )
            )
        session.commit()

        result = generate_digest(session, since="7d", watch_name="watch-a")
        assert result["data"]["summary"]["total_discovered"] == 3

    def test_digest_writes_file(self, tmp_db, tmp_path):
        """Digest should write markdown and JSON files."""
        out = str(tmp_path / "test_digest.md")
        result = generate_digest(tmp_db, since="7d", output_path=out)
        assert result["output_path"] == out
        assert (tmp_path / "test_digest.md").exists()
        assert (tmp_path / "test_digest.json").exists()

    def test_digest_reproducible(self, tmp_db):
        """Same period should produce same data counts."""
        session = tmp_db

        watch = Watch(name="repro", source="arxiv", query="test", enabled=True)
        session.add(watch)
        session.flush()
        session.add(
            InboxItem(
                watch_id=watch.id,
                title="Paper",
                status="new",
                discovered_at=datetime.now(timezone.utc),
                dedup_hash="repro1",
            )
        )
        session.commit()

        r1 = generate_digest(session, since="7d")
        r2 = generate_digest(session, since="7d")
        assert r1["data"]["summary"] == r2["data"]["summary"]
