"""Citation network analysis — PageRank, degree, community detection."""

import logging

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Citation, Item

logger = logging.getLogger(__name__)


def analyze_citation_network(session: Session) -> dict:
    """Analyze the citation network.

    Returns: {
        "node_count": int, "edge_count": int,
        "top_in_degree": [...], "top_out_degree": [...],
        "top_pagerank": [...],
        "communities": [...], "community_count": int,
    }
    """
    # Build graph from resolved citations
    citations = session.execute(select(Citation).where(Citation.dst_item_id.is_not(None))).scalars().all()

    G = nx.DiGraph()

    # Add nodes for all items involved
    item_ids = set()
    for c in citations:
        item_ids.add(c.src_item_id)
        item_ids.add(c.dst_item_id)
        G.add_edge(c.src_item_id, c.dst_item_id)

    if not G.nodes():
        return {
            "node_count": 0,
            "edge_count": 0,
            "top_in_degree": [],
            "top_out_degree": [],
            "top_pagerank": [],
            "communities": [],
            "community_count": 0,
        }

    # Load item metadata
    items = session.execute(select(Item).where(Item.id.in_(item_ids))).scalars().all()
    item_map = {i.id: i for i in items}

    def _item_info(item_id, **extra):
        item = item_map.get(item_id)
        info = {
            "id": item_id,
            "title": item.title if item else f"Item {item_id}",
            "year": item.year if item else None,
            "venue": item.venue if item else None,
        }
        info.update(extra)
        return info

    # In-degree (most cited)
    in_deg = sorted(G.in_degree(), key=lambda x: x[1], reverse=True)[:20]
    top_in_degree = [_item_info(nid, in_degree=d) for nid, d in in_deg]

    # Out-degree (most citing)
    out_deg = sorted(G.out_degree(), key=lambda x: x[1], reverse=True)[:20]
    top_out_degree = [_item_info(nid, out_degree=d) for nid, d in out_deg]

    # PageRank
    try:
        pr = nx.pagerank(G, alpha=0.85)
        top_pr = sorted(pr.items(), key=lambda x: x[1], reverse=True)[:20]
        top_pagerank = [_item_info(nid, pagerank=round(score, 6)) for nid, score in top_pr]
    except Exception:
        top_pagerank = []

    # Community detection (on undirected version)
    communities = []
    community_count = 0
    try:
        G_undirected = G.to_undirected()
        from networkx.algorithms.community import greedy_modularity_communities

        comms = list(greedy_modularity_communities(G_undirected))
        community_count = len(comms)
        for idx, comm in enumerate(comms[:10]):
            members = []
            for nid in sorted(comm, key=lambda n: G.in_degree(n), reverse=True)[:5]:
                members.append(_item_info(nid, in_degree=G.in_degree(nid)))
            communities.append(
                {
                    "id": idx,
                    "size": len(comm),
                    "top_members": members,
                }
            )
    except Exception as e:
        logger.warning(f"Community detection failed: {e}")

    return {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "top_in_degree": top_in_degree,
        "top_out_degree": top_out_degree,
        "top_pagerank": top_pagerank,
        "communities": communities,
        "community_count": community_count,
    }
