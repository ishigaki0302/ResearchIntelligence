"""Tests for Report Generation pipeline (Issue #63)."""

import json

from app.pipelines.corpus_report import _corpus_dir, generate_report


def _write_corpus_data():
    """Write minimal test data for all 3 MVP sections."""
    corpus_dir = _corpus_dir()

    clusters = [
        {
            "cluster_id": 0,
            "label_en": "Neural MT",
            "label_ja": "ニューラル機械翻訳",
            "keywords": ["transformer", "attention", "BLEU"],
            "paper_ids": [1, 2],
            "centroid_xy": [0.1, 0.2],
            "representative_ids": [1],
        }
    ]
    (corpus_dir / "cluster_summary.json").write_text(json.dumps(clusters))

    umap = {"1": [0.1, 0.2], "2": [0.3, 0.4]}
    (corpus_dir / "umap2d.json").write_text(json.dumps(umap))

    top30 = [
        {
            "rank": 1,
            "item_id": 1,
            "title": "BERT for NER",
            "abstract": "An abstract.",
            "score": 0.95,
            "reason": "Near your profile.",
        },
        {"rank": 2, "item_id": 2, "title": "GPT for MT", "abstract": "Another abstract.", "score": 0.88, "reason": ""},
    ]
    (corpus_dir / "personalized_top30.json").write_text(json.dumps(top30))

    gaps = [
        {
            "rank": 1,
            "gap_type": "tag_pair",
            "description": "NER × Wikidata の組み合わせがない",
            "tag1": "corpus_task:ner",
            "tag2": "corpus_dataset:wikidata",
            "freq1": 10,
            "freq2": 8,
            "experiment": "BERT を Wikidata に fine-tune して NER を評価する。",
        }
    ]
    (corpus_dir / "gaps_top10.json").write_text(json.dumps(gaps))


class TestGenerateReport:
    def test_html_report_created(self, tmp_db, tmp_path):
        _write_corpus_data()
        out_path = generate_report(output_dir=tmp_path / "report", fmt="html")

        assert out_path.name == "index.html"
        assert out_path.exists()

    def test_html_has_html_tag(self, tmp_db, tmp_path):
        _write_corpus_data()
        out_path = generate_report(output_dir=tmp_path / "report", fmt="html")

        content = out_path.read_text()
        assert "<html" in content
        assert "Topic Atlas" in content
        assert "Top30" in content or "Top" in content

    def test_html_contains_all_three_sections(self, tmp_db, tmp_path):
        _write_corpus_data()
        out_path = generate_report(output_dir=tmp_path / "report", fmt="html")

        content = out_path.read_text()
        assert "Topic Atlas" in content
        assert "BERT for NER" in content  # top30
        assert "NER × Wikidata" in content  # gaps

    def test_markdown_report_created(self, tmp_db, tmp_path):
        _write_corpus_data()
        out_path = generate_report(output_dir=tmp_path / "report_md", fmt="markdown")

        assert out_path.name == "report.md"
        assert out_path.exists()

    def test_markdown_contains_sections(self, tmp_db, tmp_path):
        _write_corpus_data()
        out_path = generate_report(output_dir=tmp_path / "report_md", fmt="markdown")

        content = out_path.read_text()
        assert "# Corpus Analysis Report" in content
        assert "Neural MT" in content
        assert "BERT for NER" in content

    def test_report_works_with_empty_data(self, tmp_db, tmp_path):
        """Report should generate even when no data files exist."""
        out_path = generate_report(output_dir=tmp_path / "report_empty", fmt="html")

        assert out_path.exists()
        content = out_path.read_text()
        assert "<html" in content
