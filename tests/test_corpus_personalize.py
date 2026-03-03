"""Tests for Personalized Retrieval pipeline (Issue #61)."""

from unittest.mock import patch

import numpy as np
import pytest

from app.core.models import Item
from app.pipelines.corpus_personalize import _top30_path, personalize


def _add_corpus_item(session, title: str, abstract: str = "") -> Item:
    item = Item(type="corpus", title=title, abstract=abstract)
    session.add(item)
    session.commit()
    return item


def _fake_embed(texts, **kwargs):
    rng = np.random.default_rng(0)
    return rng.random((len(texts), 4)).astype(np.float32)


def _write_fake_embeddings(item_ids: list[int], dim: int = 4):
    from app.pipelines.corpus_personalize import _corpus_dir

    rng = np.random.default_rng(1)
    vectors = rng.random((len(item_ids), dim)).astype(np.float32)
    np.savez_compressed(
        _corpus_dir() / "embeddings.npz",
        item_ids=np.array(item_ids),
        vectors=vectors,
    )


class TestPersonalize:
    def test_missing_embeddings_raises(self, tmp_db):
        with pytest.raises(FileNotFoundError):
            personalize(tmp_db, profile="machine translation", explain=False)

    def test_returns_ranked_results(self, tmp_db):
        for i in range(5):
            _add_corpus_item(tmp_db, f"Paper {i}", f"Abstract {i}")
        _write_fake_embeddings(list(range(1, 6)))

        with patch("app.pipelines.corpus_personalize.embed_texts", side_effect=_fake_embed):
            results = personalize(tmp_db, profile="NLP research", top_k=5, explain=False)

        assert len(results) == 5
        # Scores should be in descending order
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_ranks_are_sequential(self, tmp_db):
        for i in range(3):
            _add_corpus_item(tmp_db, f"Paper {i}")
        _write_fake_embeddings([1, 2, 3])

        with patch("app.pipelines.corpus_personalize.embed_texts", side_effect=_fake_embed):
            results = personalize(tmp_db, profile="research profile", top_k=3, explain=False)

        assert [r["rank"] for r in results] == [1, 2, 3]

    def test_fewer_papers_than_top_k(self, tmp_db):
        for i in range(2):
            _add_corpus_item(tmp_db, f"Paper {i}")
        _write_fake_embeddings([1, 2])

        with patch("app.pipelines.corpus_personalize.embed_texts", side_effect=_fake_embed):
            results = personalize(tmp_db, profile="test", top_k=30, explain=False)

        assert len(results) == 2

    def test_profile_from_file(self, tmp_db, tmp_path):
        profile_file = tmp_path / "profile.txt"
        profile_file.write_text("I work on machine translation and neural networks.")

        _add_corpus_item(tmp_db, "MT Paper", "Neural machine translation paper.")
        _write_fake_embeddings([1])

        with patch("app.pipelines.corpus_personalize.embed_texts", side_effect=_fake_embed):
            results = personalize(tmp_db, profile=str(profile_file), top_k=5, explain=False)

        assert len(results) == 1

    def test_output_json_written(self, tmp_db):
        _add_corpus_item(tmp_db, "Test Paper", "Abstract.")
        _write_fake_embeddings([1])

        with patch("app.pipelines.corpus_personalize.embed_texts", side_effect=_fake_embed):
            personalize(tmp_db, profile="research profile", top_k=5, explain=False)

        assert _top30_path().exists()

    def test_empty_profile_returns_empty(self, tmp_db):
        results = personalize(tmp_db, profile="   ", explain=False)
        assert results == []
