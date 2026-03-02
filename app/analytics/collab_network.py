"""Author and institution collaboration network analysis.

GPU-free module. Works on CPU-only machines (e.g., Mac).
Covers Issue #53 (NLP2026 Author/Institution Network Analysis).

Requires: networkx (already in dependencies)
"""

import json
import logging
import re
from collections import Counter, defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Author, Item, ItemAuthor, Tag, ItemTag

logger = logging.getLogger(__name__)


def build_coauthor_graph(
    session: Session,
    venue_instance: Optional[str] = None,
    min_edge_weight: int = 1,
) -> dict:
    """Build co-authorship graph for items in a venue.

    Returns:
        {
          "nodes": [{"id": author_id, "name": str, "paper_count": int}, ...],
          "edges": [{"source": id, "target": id, "weight": int}, ...],
          "stats": {"node_count": int, "edge_count": int, "paper_count": int}
        }
    """
    import networkx as nx

    # Fetch item-author pairs
    query = (
        select(ItemAuthor.item_id, ItemAuthor.author_id, Author.name)
        .join(Author, Author.id == ItemAuthor.author_id)
        .join(Item, Item.id == ItemAuthor.item_id)
        .where(Item.status == "active")
    )
    if venue_instance:
        query = query.where(Item.venue_instance == venue_instance)

    rows = session.execute(query).all()

    # Group by item
    item_authors: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for item_id, author_id, name in rows:
        item_authors[item_id].append((author_id, name))

    author_names: dict[int, str] = {}
    author_papers: Counter = Counter()
    edge_weights: Counter = Counter()

    for item_id, authors in item_authors.items():
        for aid, name in authors:
            author_names[aid] = name
            author_papers[aid] += 1

        # All pairs
        for i in range(len(authors)):
            for j in range(i + 1, len(authors)):
                a1 = authors[i][0]
                a2 = authors[j][0]
                key = (min(a1, a2), max(a1, a2))
                edge_weights[key] += 1

    # Build graph
    G = nx.Graph()
    for aid, name in author_names.items():
        G.add_node(aid, name=name, paper_count=author_papers[aid])

    for (a1, a2), weight in edge_weights.items():
        if weight >= min_edge_weight:
            G.add_edge(a1, a2, weight=weight)

    # Compute centrality for top nodes (limit for performance)
    top_nodes = set(aid for aid, _ in author_papers.most_common(100))
    subG = G.subgraph(top_nodes)
    try:
        pagerank = nx.pagerank(subG, weight="weight")
    except Exception:
        pagerank = {}

    nodes = [
        {
            "id": aid,
            "name": author_names[aid],
            "paper_count": author_papers[aid],
            "pagerank": round(pagerank.get(aid, 0), 6),
        }
        for aid in author_names
        if aid in top_nodes
    ]
    nodes.sort(key=lambda x: x["paper_count"], reverse=True)

    edges = [
        {"source": a1, "target": a2, "weight": w}
        for (a1, a2), w in edge_weights.items()
        if a1 in top_nodes and a2 in top_nodes and w >= min_edge_weight
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(author_names),
            "edge_count": len(edge_weights),
            "paper_count": len(item_authors),
        },
    }


def top_authors_ranking(
    session: Session,
    venue_instance: Optional[str] = None,
    top_n: int = 30,
) -> list[dict]:
    """Return top N authors by paper count."""
    query = (
        select(Author.id, Author.name, Author.norm_name)
        .join(ItemAuthor, ItemAuthor.author_id == Author.id)
        .join(Item, Item.id == ItemAuthor.item_id)
        .where(Item.status == "active")
    )
    if venue_instance:
        query = query.where(Item.venue_instance == venue_instance)

    rows = session.execute(query).all()
    counter: Counter = Counter()
    names: dict[int, str] = {}
    for aid, name, _ in rows:
        counter[aid] += 1
        names[aid] = name

    return [
        {"author_id": aid, "name": names[aid], "paper_count": cnt}
        for aid, cnt in counter.most_common(top_n)
    ]


def institution_ranking(
    session: Session,
    venue_instance: Optional[str] = None,
    top_n: int = 30,
) -> list[dict]:
    """Estimate institution distribution from author norm_names.

    Note: Institutions are not directly stored; this uses tag-based heuristics
    and the scraper data. Returns empty list if affiliation data is unavailable.
    """
    # Institution info would need to be scraped separately.
    # Here we provide a structure-compatible stub that can be populated
    # by the NLP2026 scraper which stores affiliations in tags.
    query = (
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .join(Item, Item.id == ItemTag.item_id)
        .where(Tag.name.startswith("affil/"), Item.status == "active")
    )
    if venue_instance:
        query = query.where(Item.venue_instance == venue_instance)

    rows = session.execute(query).all()
    counter: Counter = Counter()
    for (tag_name,) in rows:
        institution = tag_name.removeprefix("affil/")
        counter[institution] += 1

    return [
        {"institution": inst, "paper_count": cnt}
        for inst, cnt in counter.most_common(top_n)
    ]


def session_distribution(
    session: Session,
    venue_instance: str = "NLP2026",
) -> list[dict]:
    """Return paper count per session series (B, C, P, Q, etc.)."""
    query = (
        select(Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .join(Item, Item.id == ItemTag.item_id)
        .where(
            Tag.name.startswith("nlp2026/"),
            Item.venue_instance == venue_instance,
            Item.status == "active",
        )
    )
    rows = session.execute(query).all()
    counter: Counter = Counter()
    for (tag_name,) in rows:
        series = tag_name.removeprefix("nlp2026/")
        # Normalize: B1 -> B, C2 -> C
        match = re.match(r"^([A-Z]+)", series)
        if match:
            counter[match.group(1)] += 1

    return [
        {"series": series, "paper_count": cnt}
        for series, cnt in sorted(counter.items())
    ]


def keyword_frequency(
    session: Session,
    venue_instance: Optional[str] = None,
    top_n: int = 50,
    use_tldr: bool = True,
) -> list[dict]:
    """Extract top keywords from titles (and TLDRs) using simple tokenization.

    Works without GPU.
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    import numpy as np

    query = select(Item.title, Item.tldr, Item.abstract).where(Item.status == "active")
    if venue_instance:
        query = query.where(Item.venue_instance == venue_instance)

    rows = session.execute(query).all()
    texts = []
    for title, tldr, abstract in rows:
        parts = [title or ""]
        if use_tldr and tldr:
            parts.append(tldr)
        if abstract:
            parts.append(abstract)
        texts.append(" ".join(parts))

    if not texts:
        return []

    _STOP = frozenset([
        "the","a","an","in","of","for","and","or","to","is","are","with","on","at",
        "by","from","as","be","this","that","it","its","we","our","their","also",
        "using","used","use","based","proposed","model","models","method","methods",
        "approach","system","task","tasks","paper","work","results","show","can",
        "two","new","large","high","data","training","learning","neural","language",
        "natural","processing","deep","performance","evaluation","pre","fine",
    ])

    try:
        vec = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=500,
            min_df=2,
            token_pattern=r"(?u)\b[^\s]{2,}\b",
            stop_words=list(_STOP),
        )
        tfidf = vec.fit_transform(texts)
        scores = np.asarray(tfidf.sum(axis=0)).flatten()
        feature_names = vec.get_feature_names_out()
        top_idx = scores.argsort()[::-1][:top_n]
        return [
            {"keyword": feature_names[i], "score": round(float(scores[i]), 3)}
            for i in top_idx
        ]
    except Exception as e:
        logger.warning(f"keyword_frequency failed: {e}")
        return []
