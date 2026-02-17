"""Tests for the sync pipeline (P12)."""

import json
from unittest.mock import patch

from sqlalchemy import select

from app.core.models import Job, Watch


class TestSyncRun:
    def test_sync_run_calls_watch_and_recommend(self, tmp_db):
        """sync run should call watch run + inbox recommend."""
        session = tmp_db

        watch = Watch(name="test-sync", source="arxiv", query="testing", enabled=True)
        session.add(watch)
        session.commit()

        mock_candidates = [
            {
                "source_id_type": "arxiv",
                "source_id_value": "2401.00001",
                "title": "Sync Test Paper",
                "authors": ["Author A"],
                "year": 2024,
                "abstract": "A paper about testing sync.",
            }
        ]

        with patch("app.connectors.arxiv.search_arxiv", return_value=mock_candidates):
            from app.pipelines.sync import run_sync

            result = run_sync(since="7d", run_recommend=True)

        assert result["watches_run"] == 1
        assert result["total_added"] == 1
        assert "job_id" in result

        # Verify job was recorded
        from app.core.db import get_session

        s2 = get_session()
        job = s2.execute(select(Job).where(Job.job_type == "sync")).scalar_one()
        assert job.status == "done"
        assert job.summary_json is not None
        summary = json.loads(job.summary_json)
        assert summary["watches_run"] == 1
        s2.close()

    def test_sync_run_specific_watch(self, tmp_db):
        """sync run with --watch should only run that watch."""
        session = tmp_db

        w1 = Watch(name="watch-a", source="arxiv", query="test a", enabled=True)
        w2 = Watch(name="watch-b", source="arxiv", query="test b", enabled=True)
        session.add_all([w1, w2])
        session.commit()

        with patch("app.connectors.arxiv.search_arxiv", return_value=[]):
            from app.pipelines.sync import run_sync

            result = run_sync(since="7d", watch_name="watch-a")

        assert result["watches_run"] == 1

    def test_sync_records_failed_job(self, tmp_db):
        """sync should record failed job on error."""
        session = tmp_db

        watch = Watch(name="fail-watch", source="arxiv", query="fail", enabled=True)
        session.add(watch)
        session.commit()

        with patch("app.connectors.arxiv.search_arxiv", side_effect=RuntimeError("API error")):
            from app.pipelines.sync import run_sync

            try:
                run_sync(since="7d")
            except RuntimeError:
                pass

        from app.core.db import get_session

        s2 = get_session()
        job = s2.execute(select(Job).where(Job.job_type == "sync")).scalar_one()
        assert job.status == "failed"
        assert "API error" in job.error
        s2.close()
