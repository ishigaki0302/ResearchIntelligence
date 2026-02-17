"""Citation graph construction and querying."""

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Citation, Item, ItemId

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy matching: lowercase, strip punctuation, collapse whitespace."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_similarity(a: str, b: str) -> float:
    """Simple word-overlap similarity between two normalized titles."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def resolve_citations(session: Session) -> dict:
    """Attempt to resolve unresolved citations using multiple strategies.

    Returns resolution stats including method breakdown.
    """
    unresolved = session.execute(select(Citation).where(Citation.dst_item_id.is_(None))).scalars().all()

    stats = {
        "resolved": 0,
        "resolved_by_bibtex_key": 0,
        "resolved_by_doi": 0,
        "resolved_by_arxiv": 0,
        "resolved_by_acl": 0,
        "resolved_by_url": 0,
        "resolved_by_title": 0,
        "remaining": 0,
    }

    # Pre-load all items for title matching
    all_items = session.execute(select(Item)).scalars().all()
    title_index = {}
    for it in all_items:
        if it.title:
            norm = _normalize_title(it.title)
            title_index[norm] = it

    for cit in unresolved:
        item = None
        method = None

        # Parse all possible IDs from raw_cite
        ids_to_try = {}
        if cit.dst_key:
            ids_to_try["primary"] = cit.dst_key

        if cit.raw_cite:
            from app.pipelines.references import ACL_ID_NEW_RE, ACL_ID_RE, ARXIV_RE, DOI_RE, URL_RE

            doi_m = DOI_RE.search(cit.raw_cite)
            if doi_m:
                ids_to_try["doi"] = doi_m.group(0).rstrip(".")
            arxiv_m = ARXIV_RE.search(cit.raw_cite)
            if arxiv_m:
                ids_to_try["arxiv"] = arxiv_m.group(1)
            acl_m = ACL_ID_RE.search(cit.raw_cite) or ACL_ID_NEW_RE.search(cit.raw_cite)
            if acl_m:
                ids_to_try["acl"] = acl_m.group(1)
            url_m = URL_RE.search(cit.raw_cite)
            if url_m:
                ids_to_try["url"] = url_m.group(0).rstrip(".)")

        # 1. Match by bibtex_key
        if cit.dst_key:
            item = session.execute(select(Item).where(Item.bibtex_key == cit.dst_key)).scalar_one_or_none()
            if item:
                method = "bibtex_key"

        # 2. Match by DOI
        if not item and "doi" in ids_to_try:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "doi", ItemId.id_value == ids_to_try["doi"])
            ).scalar_one_or_none()
            if link:
                item = link.item
                method = "doi"

        # Also try primary key as DOI
        if not item and cit.dst_key and not method:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "doi", ItemId.id_value == cit.dst_key)
            ).scalar_one_or_none()
            if link:
                item = link.item
                method = "doi"

        # 3. Match by arXiv ID
        if not item and "arxiv" in ids_to_try:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "arxiv", ItemId.id_value == ids_to_try["arxiv"])
            ).scalar_one_or_none()
            if link:
                item = link.item
                method = "arxiv"

        if not item and cit.dst_key and not method:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "arxiv", ItemId.id_value == cit.dst_key)
            ).scalar_one_or_none()
            if link:
                item = link.item
                method = "arxiv"

        # 4. Match by ACL Anthology ID
        if not item and "acl" in ids_to_try:
            link = session.execute(
                select(ItemId).where(ItemId.id_type == "acl", ItemId.id_value == ids_to_try["acl"])
            ).scalar_one_or_none()
            if link:
                item = link.item
                method = "acl"

        # 5. Match by URL in source_url
        if not item and "url" in ids_to_try:
            item = session.execute(select(Item).where(Item.source_url == ids_to_try["url"])).scalar_one_or_none()
            if item:
                method = "url"

        # 6. Title normalization fallback
        if not item and cit.raw_cite:
            from app.pipelines.references import TITLE_GUESS_RE

            title_m = TITLE_GUESS_RE.search(cit.raw_cite)
            if title_m:
                guess = _normalize_title(title_m.group(0).rstrip("."))
                if guess:
                    # Try exact match first
                    if guess in title_index:
                        item = title_index[guess]
                        method = "title"
                    else:
                        # Fuzzy match
                        best_sim = 0.0
                        best_item = None
                        for norm_title, candidate in title_index.items():
                            sim = _title_similarity(guess, norm_title)
                            if sim > best_sim:
                                best_sim = sim
                                best_item = candidate
                        if best_sim > 0.85 and best_item:
                            item = best_item
                            method = "title"

        if item and item.id != cit.src_item_id:  # avoid self-citation loops
            cit.dst_item_id = item.id
            stats["resolved"] += 1
            if method:
                stats[f"resolved_by_{method}"] += 1
        else:
            stats["remaining"] += 1

    session.commit()
    return stats


def get_citation_subgraph(session: Session, item_id: int, depth: int = 1) -> dict[str, Any]:
    """Get the local citation subgraph for an item.

    Returns:
    {
        "center": {"id", "title", "year"},
        "cites": [{"id", "title", "year"}],
        "cited_by": [{"id", "title", "year"}],
        "unresolved_refs": [{"raw_cite", "dst_key"}],
        "edges": [{"src", "dst"}],
    }
    """
    center = session.get(Item, item_id)
    if not center:
        return {"center": None, "cites": [], "cited_by": [], "unresolved_refs": [], "edges": []}

    def _item_info(item: Item) -> dict:
        return {"id": item.id, "title": item.title, "year": item.year, "bibtex_key": item.bibtex_key}

    # Outgoing citations (this item cites ...)
    out_cits = session.execute(select(Citation).where(Citation.src_item_id == item_id)).scalars().all()

    cites = []
    edges = []
    unresolved_refs = []
    seen_items = {item_id}

    for c in out_cits:
        if c.dst_item_id:
            dst = session.get(Item, c.dst_item_id)
            if dst:
                cites.append(_item_info(dst))
                edges.append({"src": item_id, "dst": dst.id})
                seen_items.add(dst.id)
        else:
            unresolved_refs.append(
                {
                    "raw_cite": (c.raw_cite or "")[:200],
                    "dst_key": c.dst_key,
                }
            )

    # Incoming citations (... cites this item)
    in_cits = session.execute(select(Citation).where(Citation.dst_item_id == item_id)).scalars().all()
    cited_by = []
    for c in in_cits:
        src = session.get(Item, c.src_item_id)
        if src:
            cited_by.append(_item_info(src))
            edges.append({"src": src.id, "dst": item_id})
            seen_items.add(src.id)

    # Depth 2: follow one more hop from resolved citations
    if depth >= 2:
        hop2_ids = set()
        for c_info in cites + cited_by:
            cid = c_info["id"]
            if cid in seen_items and cid != item_id:
                hop2_ids.add(cid)

        for hop_id in hop2_ids:
            # Outgoing from hop
            hop_out = (
                session.execute(
                    select(Citation).where(Citation.src_item_id == hop_id, Citation.dst_item_id.is_not(None))
                )
                .scalars()
                .all()
            )
            for c in hop_out:
                if c.dst_item_id not in seen_items:
                    dst = session.get(Item, c.dst_item_id)
                    if dst:
                        cites.append(_item_info(dst))
                        edges.append({"src": hop_id, "dst": dst.id})
                        seen_items.add(dst.id)

            # Incoming to hop
            hop_in = session.execute(select(Citation).where(Citation.dst_item_id == hop_id)).scalars().all()
            for c in hop_in:
                if c.src_item_id not in seen_items:
                    src = session.get(Item, c.src_item_id)
                    if src:
                        cited_by.append(_item_info(src))
                        edges.append({"src": src.id, "dst": hop_id})
                        seen_items.add(src.id)

    return {
        "center": _item_info(center),
        "cites": cites,
        "cited_by": cited_by,
        "unresolved_refs": unresolved_refs,
        "edges": edges,
    }
