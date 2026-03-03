"""Tests for corpus embedding + UMAP pipeline (Issue #58)."""

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.core.models import Item
from app.pipelines.corpus_embed import _embeddings_path, _umap_path, compute_umap, embed_corpus


def _add_corpus_item(session, title: str = "Test Paper", abstract: str = "An abstract.") -> Item:
    item = Item(type="corpus", title=title, abstract=abstract)
    session.add(item)
    session.commit()
    return item


def _fake_embed(texts, **kwargs):
    """Return deterministic fake 4-dim embeddings."""
    return np.random.default_rng(0).random((len(texts), 4)).astype(np.float32)


class TestEmbedCorpus:
    def test_no_corpus_items(self, tmp_db):
        result = embed_corpus(tmp_db, rebuild=False)
        assert result == {"total": 0, "embedded": 0, "cached": 0, "dim": 0}

    def test_embeds_corpus_items(self, tmp_db):
        _add_corpus_item(tmp_db, "Paper A")
        _add_corpus_item(tmp_db, "Paper B")

        with patch("app.pipelines.corpus_embed.embed_texts", side_effect=_fake_embed):
            result = embed_corpus(tmp_db, rebuild=False)

        assert result["total"] == 2
        assert result["embedded"] == 2
        assert result["cached"] == 0
        assert result["dim"] == 4
        assert _embeddings_path().exists()

        data = np.load(_embeddings_path())
        assert len(data["item_ids"]) == 2
        assert data["vectors"].shape == (2, 4)

    def test_caches_existing_embeddings(self, tmp_db):
        _add_corpus_item(tmp_db, "Paper A")

        with patch("app.pipelines.corpus_embed.embed_texts", side_effect=_fake_embed):
            embed_corpus(tmp_db, rebuild=False)
            result2 = embed_corpus(tmp_db, rebuild=False)

        # Second run: item already cached, embed_texts not called again for it
        assert result2["cached"] == 1
        assert result2["embedded"] == 0

    def test_rebuild_re_embeds_all(self, tmp_db):
        _add_corpus_item(tmp_db, "Paper A")

        with patch("app.pipelines.corpus_embed.embed_texts", side_effect=_fake_embed):
            embed_corpus(tmp_db, rebuild=False)

        with patch("app.pipelines.corpus_embed.embed_texts", side_effect=_fake_embed):
            result = embed_corpus(tmp_db, rebuild=True)

        assert result["embedded"] == 1
        assert result["cached"] == 0


class TestComputeUmap:
    def _setup_embeddings(self, n: int = 5):
        """Write fake embeddings.npz for n items."""
        rng = np.random.default_rng(42)
        item_ids = list(range(1, n + 1))
        vectors = rng.random((n, 4)).astype(np.float32)
        np.savez_compressed(_embeddings_path(), item_ids=np.array(item_ids), vectors=vectors)

    def test_produces_umap_json(self, tmp_db):
        self._setup_embeddings(5)
        result = compute_umap(rebuild=True)

        assert result["total"] == 5
        assert Path(result["output_path"]).exists()
        data = json.loads(Path(result["output_path"]).read_text())
        assert len(data) == 5
        # Each value is [x, y]
        for xy in data.values():
            assert len(xy) == 2

    def test_skips_if_exists_no_rebuild(self, tmp_db):
        self._setup_embeddings(3)
        compute_umap(rebuild=True)  # create

        # Second call without rebuild should not re-compute
        umap_path = _umap_path()
        mtime_before = umap_path.stat().st_mtime
        result = compute_umap(rebuild=False)
        mtime_after = umap_path.stat().st_mtime

        assert mtime_before == mtime_after
        assert result["total"] == 3

    def test_missing_embeddings_raises(self, tmp_db):
        with pytest.raises(FileNotFoundError):
            compute_umap(rebuild=True)

    def test_item_ids_in_json_keys(self, tmp_db):
        self._setup_embeddings(4)
        compute_umap(rebuild=True)
        data = json.loads(_umap_path().read_text())
        assert set(data.keys()) == {"1", "2", "3", "4"}
