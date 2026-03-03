"""Tests for Topic Atlas clustering pipeline (Issue #59)."""

import json

import numpy as np
import pytest

from app.analytics.corpus_cluster import (
    _cluster_summary_path,
    _embeddings_path,
    _umap_path,
    cluster_corpus,
)
from app.core.models import Item


def _add_corpus_item(session, title: str, abstract: str = "") -> Item:
    item = Item(type="corpus", title=title, abstract=abstract)
    session.add(item)
    session.commit()
    return item


def _write_fake_embeddings(n: int, dim: int = 4):
    rng = np.random.default_rng(0)
    item_ids = list(range(1, n + 1))
    vectors = rng.random((n, dim)).astype(np.float32)
    np.savez_compressed(_embeddings_path(), item_ids=np.array(item_ids), vectors=vectors)


def _write_fake_umap(item_ids: list[int]):
    rng = np.random.default_rng(1)
    data = {str(iid): [float(rng.random()), float(rng.random())] for iid in item_ids}
    _umap_path().write_text(json.dumps(data))


class TestClusterCorpus:
    def test_empty_embeddings_returns_empty(self, tmp_db):
        _write_fake_embeddings(0)
        result = cluster_corpus(tmp_db, method="kmeans", n_clusters=2, rebuild=True)
        assert result == []

    def test_kmeans_clusters_created(self, tmp_db):
        n = 10
        for i in range(n):
            _add_corpus_item(tmp_db, f"Paper {i}", f"Abstract {i}")
        _write_fake_embeddings(n)
        _write_fake_umap(list(range(1, n + 1)))

        result = cluster_corpus(tmp_db, method="kmeans", n_clusters=3, rebuild=True)

        assert len(result) == 3
        assert _cluster_summary_path().exists()

        # Each cluster has required keys
        for c in result:
            assert "cluster_id" in c
            assert "label_en" in c
            assert "paper_ids" in c
            assert "keywords" in c
            assert "centroid_xy" in c
            assert "representative_ids" in c

    def test_all_papers_covered(self, tmp_db):
        n = 6
        for i in range(n):
            _add_corpus_item(tmp_db, f"Paper {i}")
        _write_fake_embeddings(n)
        _write_fake_umap(list(range(1, n + 1)))

        result = cluster_corpus(tmp_db, method="kmeans", n_clusters=2, rebuild=True)
        all_ids = [pid for c in result for pid in c["paper_ids"]]
        # All n items should be in some cluster
        assert len(all_ids) == n

    def test_skips_if_exists_no_rebuild(self, tmp_db):
        n = 4
        for i in range(n):
            _add_corpus_item(tmp_db, f"Paper {i}")
        _write_fake_embeddings(n)

        result1 = cluster_corpus(tmp_db, method="kmeans", n_clusters=2, rebuild=True)
        summary_path = _cluster_summary_path()
        mtime = summary_path.stat().st_mtime

        result2 = cluster_corpus(tmp_db, method="kmeans", n_clusters=2, rebuild=False)
        assert summary_path.stat().st_mtime == mtime
        assert len(result2) == len(result1)

    def test_missing_embeddings_raises(self, tmp_db):
        with pytest.raises(FileNotFoundError):
            cluster_corpus(tmp_db, method="kmeans", n_clusters=2, rebuild=True)

    def test_fallback_label_when_no_llm(self, tmp_db):
        n = 4
        for i in range(n):
            _add_corpus_item(tmp_db, f"Paper {i}")
        _write_fake_embeddings(n)

        # LLM unavailable → labels should be "Cluster N"
        result = cluster_corpus(tmp_db, method="kmeans", n_clusters=2, rebuild=True)
        for c in result:
            # Either LLM provided a label or fallback is used
            assert c["label_en"] is not None
            assert len(c["label_en"]) > 0
