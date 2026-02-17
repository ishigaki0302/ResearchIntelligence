"""Tests for topic clustering (P14)."""

from app.analytics.clustering import cluster_items
from app.core.models import Item


class TestClustering:
    def test_cluster_basic(self, tmp_db):
        """Clustering should produce valid clusters."""
        session = tmp_db

        # Add enough items for clustering
        titles = [
            "Attention Is All You Need",
            "BERT: Pre-training of Deep Bidirectional Transformers",
            "GPT-3: Language Models are Few-Shot Learners",
            "Retrieval Augmented Generation for Knowledge-Intensive Tasks",
            "Dense Passage Retrieval for Open-Domain QA",
            "Convolutional Neural Networks for Image Recognition",
            "ResNet: Deep Residual Learning for Image Recognition",
            "ViT: Vision Transformer for Image Classification",
        ]
        for i, title in enumerate(titles):
            item = Item(title=title, abstract=f"Abstract about {title.lower()}", year=2020 + i % 3)
            session.add(item)
        session.commit()

        result = cluster_items(session, n_clusters=2)
        assert "clusters" in result
        assert len(result["clusters"]) == 2
        assert result["total_items"] == 8

        for c in result["clusters"]:
            assert c["size"] > 0
            assert len(c["top_terms"]) > 0
            assert len(c["representative_items"]) > 0

    def test_cluster_empty(self, tmp_db):
        """Clustering with no items should return empty result."""
        result = cluster_items(tmp_db, n_clusters=3)
        assert result["clusters"] == []
        assert result["total_items"] == 0

    def test_cluster_few_items(self, tmp_db):
        """Clustering with fewer items than clusters should adjust."""
        session = tmp_db
        session.add(Item(title="Only Paper", abstract="Solo", year=2024))
        session.commit()

        result = cluster_items(session, n_clusters=5)
        assert result["n_clusters"] == 1
        assert len(result["clusters"]) == 1
