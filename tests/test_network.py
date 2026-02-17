"""Tests for citation network analysis (P14)."""

from app.analytics.network import analyze_citation_network
from app.core.models import Citation, Item


class TestNetworkAnalysis:
    def test_empty_network(self, tmp_db):
        """Empty citation graph should return zeros."""
        result = analyze_citation_network(tmp_db)
        assert result["node_count"] == 0
        assert result["edge_count"] == 0
        assert result["top_in_degree"] == []
        assert result["community_count"] == 0

    def test_basic_network(self, tmp_db):
        """Network with citations should compute metrics."""
        session = tmp_db

        items = []
        for i in range(5):
            item = Item(title=f"Paper {i}", year=2020 + i)
            session.add(item)
            items.append(item)
        session.flush()

        # Create citation chain: 0->1, 0->2, 1->2, 3->2, 4->2
        edges = [(0, 1), (0, 2), (1, 2), (3, 2), (4, 2)]
        for src, dst in edges:
            session.add(
                Citation(
                    src_item_id=items[src].id,
                    dst_item_id=items[dst].id,
                    raw_cite=f"cite {src}->{dst}",
                )
            )
        session.commit()

        result = analyze_citation_network(session)
        assert result["node_count"] == 5
        assert result["edge_count"] == 5

        # Paper 2 should be most cited
        top_cited = result["top_in_degree"][0]
        assert top_cited["id"] == items[2].id
        assert top_cited["in_degree"] == 4

        # PageRank should exist
        assert len(result["top_pagerank"]) > 0

        # Communities
        assert result["community_count"] >= 1

    def test_network_with_unresolved(self, tmp_db):
        """Unresolved citations (dst_item_id=None) should be excluded."""
        session = tmp_db

        item = Item(title="Solo", year=2024)
        session.add(item)
        session.flush()

        session.add(
            Citation(
                src_item_id=item.id,
                dst_item_id=None,
                raw_cite="unresolved ref",
            )
        )
        session.commit()

        result = analyze_citation_network(session)
        assert result["node_count"] == 0
        assert result["edge_count"] == 0
