"""Tests for LLM Tag Normalization pipeline (Issue #60)."""

import json

from app.core.models import Item, Tag
from app.pipelines.corpus_tags import compute_tag_patterns, normalize_tags


def _add_corpus_item(session, title: str, abstract: str = "") -> Item:
    item = Item(type="corpus", title=title, abstract=abstract)
    session.add(item)
    session.commit()
    return item


class TestNormalizeTags:
    def test_no_items_returns_zero(self, tmp_db):
        result = normalize_tags(tmp_db)
        assert result["total"] == 0
        assert result["tagged"] == 0

    def test_tags_created_for_items(self, tmp_db):
        _add_corpus_item(tmp_db, "BERT for NER", "We fine-tune BERT on the CoNLL dataset, evaluated with F1.")
        _add_corpus_item(tmp_db, "GPT for summarization", "We apply GPT-2 on CNN/DailyMail with ROUGE metric.")

        result = normalize_tags(tmp_db, rebuild=False)

        assert result["total"] == 2
        assert result["tagged"] == 2
        assert result["tag_count"] > 0

        # Tags should be in DB
        from sqlalchemy import select

        tags = tmp_db.execute(select(Tag).where(Tag.kind.like("corpus_%"))).scalars().all()
        assert len(tags) > 0

    def test_idempotent_no_rebuild(self, tmp_db):
        _add_corpus_item(tmp_db, "Test paper", "Abstract about transformers and attention.")

        r1 = normalize_tags(tmp_db, rebuild=False)
        r2 = normalize_tags(tmp_db, rebuild=False)

        assert r1["tagged"] == 1
        assert r2["tagged"] == 0  # already tagged → skipped
        assert r2["skipped"] == 1

    def test_rebuild_retags_items(self, tmp_db):
        _add_corpus_item(tmp_db, "Test paper", "Abstract about LSTM and sequence labeling.")

        r1 = normalize_tags(tmp_db, rebuild=False)
        r2 = normalize_tags(tmp_db, rebuild=True)

        assert r1["tagged"] == 1
        assert r2["tagged"] == 1  # forced re-tag

    def test_item_without_text_skipped(self, tmp_db):
        item = Item(type="corpus", title="", abstract="")
        tmp_db.add(item)
        tmp_db.commit()

        result = normalize_tags(tmp_db)
        assert result["skipped"] == 1


class TestComputeTagPatterns:
    def test_produces_patterns_files(self, tmp_db):
        from app.pipelines.corpus_tags import _corpus_dir

        _add_corpus_item(tmp_db, "BERT NER", "Fine-tune BERT on CoNLL with F1.")
        _add_corpus_item(tmp_db, "BERT MT", "Fine-tune BERT on WMT with BLEU.")
        normalize_tags(tmp_db, rebuild=True)

        patterns = compute_tag_patterns(tmp_db)

        assert "top_pairs" in patterns
        assert "gaps" in patterns
        assert (_corpus_dir() / "tag_cooccurrence.json").exists()
        assert (_corpus_dir() / "tag_patterns.json").exists()

    def test_cooccurrence_structure(self, tmp_db):
        from app.pipelines.corpus_tags import _corpus_dir

        _add_corpus_item(tmp_db, "Paper A", "BERT fine-tuning NER CoNLL F1.")
        _add_corpus_item(tmp_db, "Paper B", "BERT fine-tuning MT WMT BLEU.")
        normalize_tags(tmp_db, rebuild=True)
        compute_tag_patterns(tmp_db)

        cooc_data = json.loads((_corpus_dir() / "tag_cooccurrence.json").read_text())
        # Keys should be "tag1|||tag2"
        for key in cooc_data:
            assert "|||" in key
