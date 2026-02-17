"""Citation graph construction and querying."""

import logging
from typing import Any

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.core.models import Citation, Item, ItemId

logger = logging.getLogger(__name__)


def resolve_citations(session: Session) -> dict:
    """Attempt to resolve unresolved citations by matching dst_key to bibtex_key, DOI, or arXiv ID."""
    unresolved = session.execute(
        select(Citation).where(Citation.dst_item_id.is_(None), Citation.dst_key.is_not(None))
    ).scalars().all()

    resolved = 0
    for cit in unresolved:
        # 1. Match by bibtex_key
        item = session.execute(
            select(Item).where(Item.bibtex_key == cit.dst_key)
        ).scalar_one_or_none()

        # 2. Match by DOI in item_ids
        if not item:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "doi", ItemId.id_value == cit.dst_key)
            ).scalar_one_or_none()
            if link:
                item = link.item

        # 3. Match by arXiv ID in item_ids
        if not item:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "arxiv", ItemId.id_value == cit.dst_key)
            ).scalar_one_or_none()
            if link:
                item = link.item

        if item:
            cit.dst_item_id = item.id
            resolved += 1

    session.commit()
    return {"resolved": resolved, "remaining": len(unresolved) - resolved}


def get_citation_subgraph(session: Session, item_id: int, depth: int = 1) -> dict[str, Any]:
    """Get the local citation subgraph for an item.

    Returns:
    {
        "center": {"id", "title", "year"},
        "cites": [{"id", "title", "year"}],       # papers this item cites
        "cited_by": [{"id", "title", "year"}],     # papers citing this item
        "edges": [{"src", "dst"}],
    }
    """
    center = session.get(Item, item_id)
    if not center:
        return {"center": None, "cites": [], "cited_by": [], "edges": []}

    def _item_info(item: Item) -> dict:
        return {"id": item.id, "title": item.title, "year": item.year, "bibtex_key": item.bibtex_key}

    # Outgoing citations (this item cites ...)
    out_cits = session.execute(
        select(Citation).where(Citation.src_item_id == item_id, Citation.dst_item_id.is_not(None))
    ).scalars().all()
    cites = []
    edges = []
    for c in out_cits:
        dst = session.get(Item, c.dst_item_id)
        if dst:
            cites.append(_item_info(dst))
            edges.append({"src": item_id, "dst": dst.id})

    # Incoming citations (... cites this item)
    in_cits = session.execute(
        select(Citation).where(Citation.dst_item_id == item_id)
    ).scalars().all()
    cited_by = []
    for c in in_cits:
        src = session.get(Item, c.src_item_id)
        if src:
            cited_by.append(_item_info(src))
            edges.append({"src": src.id, "dst": item_id})

    return {
        "center": _item_info(center),
        "cites": cites,
        "cited_by": cited_by,
        "edges": edges,
    }
