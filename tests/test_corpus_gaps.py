"""Tests for Gap Detection pipeline (Issue #62)."""

import json

from app.analytics.corpus_gaps import _corpus_dir, _gaps_path, detect_gaps


def _write_tag_patterns(gaps: list[dict]):
    corpus_dir = _corpus_dir()
    patterns = {"top_pairs": [], "gaps": gaps}
    (corpus_dir / "tag_patterns.json").write_text(json.dumps(patterns))


class TestDetectGaps:
    def test_no_patterns_file_returns_empty(self, tmp_db):
        result = detect_gaps(tmp_db)
        assert result == []

    def test_empty_gaps_returns_empty_list(self, tmp_db):
        _write_tag_patterns([])
        result = detect_gaps(tmp_db)
        assert result == []
        assert _gaps_path().exists()

    def test_produces_top10_json(self, tmp_db):
        gaps_input = [
            {"tag1": "corpus_task:ner", "tag2": "corpus_dataset:wikidata", "freq1": 10, "freq2": 8},
            {"tag1": "corpus_method:bert", "tag2": "corpus_metric:bleu", "freq1": 15, "freq2": 6},
            {"tag1": "corpus_task:mt", "tag2": "corpus_dataset:conll", "freq1": 5, "freq2": 7},
        ]
        _write_tag_patterns(gaps_input)

        result = detect_gaps(tmp_db, top_n=10)

        assert len(result) == 3
        assert _gaps_path().exists()

        # Verify JSON structure
        data = json.loads(_gaps_path().read_text())
        assert len(data) == 3
        for item in data:
            assert "rank" in item
            assert "description" in item
            assert "tag1" in item
            assert "tag2" in item

    def test_ranks_are_sequential(self, tmp_db):
        gaps_input = [
            {"tag1": "t1", "tag2": "t2", "freq1": 5, "freq2": 3},
            {"tag1": "t3", "tag2": "t4", "freq1": 7, "freq2": 2},
        ]
        _write_tag_patterns(gaps_input)

        result = detect_gaps(tmp_db, top_n=5)

        assert [g["rank"] for g in result] == list(range(1, len(result) + 1))

    def test_top_n_limits_output(self, tmp_db):
        gaps_input = [{"tag1": f"t{i}", "tag2": f"t{i+1}", "freq1": i, "freq2": i + 1} for i in range(20)]
        _write_tag_patterns(gaps_input)

        result = detect_gaps(tmp_db, top_n=5)

        assert len(result) == 5

    def test_uses_top30_for_context(self, tmp_db):
        """When personalized_top30.json exists, it's used as context (no crash)."""
        corpus_dir = _corpus_dir()
        top30 = [{"title": "Paper A", "abstract": "About NER."}, {"title": "Paper B", "abstract": "About MT."}]
        (corpus_dir / "personalized_top30.json").write_text(json.dumps(top30))

        _write_tag_patterns([{"tag1": "t1", "tag2": "t2", "freq1": 3, "freq2": 2}])
        result = detect_gaps(tmp_db)

        assert len(result) == 1
