"""Deduplication pipeline — detect and merge duplicate items."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import (
    CollectionItem,
    Item,
    ItemId,
    ItemTag,
)

logger = logging.getLogger(__name__)


def detect_duplicates(session: Session) -> list[dict]:
    """Detect duplicate items using multiple strategies.

    Returns list of {item_a_id, item_b_id, confidence, method, details}.
    """
    items = session.execute(select(Item).where(Item.status == "active")).scalars().all()
    duplicates = []
    seen_pairs = set()

    def _add_pair(a_id, b_id, confidence, method, details=""):
        pair = (min(a_id, b_id), max(a_id, b_id))
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            duplicates.append(
                {
                    "item_a_id": pair[0],
                    "item_b_id": pair[1],
                    "confidence": confidence,
                    "method": method,
                    "details": details,
                }
            )

    # Strategy 1: Exact external ID match (DOI, arXiv, ACL)
    ext_ids = session.execute(select(ItemId)).scalars().all()
    id_groups: dict[tuple[str, str], list[int]] = {}
    for eid in ext_ids:
        key = (eid.id_type, eid.id_value)
        id_groups.setdefault(key, []).append(eid.item_id)

    for (id_type, id_value), item_ids in id_groups.items():
        if len(item_ids) > 1:
            unique_ids = list(set(item_ids))
            for i in range(len(unique_ids)):
                for j in range(i + 1, len(unique_ids)):
                    _add_pair(
                        unique_ids[i],
                        unique_ids[j],
                        1.0,
                        f"exact_{id_type}",
                        f"{id_type}={id_value}",
                    )

    # Strategy 2: Title + year + first author match
    item_index: dict[str, list[Item]] = {}
    for item in items:
        if item.title and item.year:
            first_author = ""
            if item.author_links:
                sorted_links = sorted(item.author_links, key=lambda x: x.position)
                if sorted_links:
                    first_author = sorted_links[0].author.norm_name

            key = f"{item.title.lower().strip()}:{item.year}:{first_author}"
            item_index.setdefault(key, []).append(item)

    for key, group in item_index.items():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    _add_pair(
                        group[i].id,
                        group[j].id,
                        0.9,
                        "title_year_author",
                        f"key={key[:80]}",
                    )

    # Strategy 3: Embedding similarity > 0.95
    try:
        from app.indexing.engine import search_faiss

        for item in items:
            query_text = f"{item.title or ''} {item.abstract or ''}".strip()
            if not query_text:
                continue
            results = search_faiss(query_text, top_k=3)
            for r in results:
                if r.get("type") == "item" and r["id"] != item.id and r.get("vector_score", 0) > 0.95:
                    _add_pair(
                        item.id,
                        r["id"],
                        r["vector_score"],
                        "embedding_similarity",
                        f"score={r['vector_score']:.4f}",
                    )
    except Exception:
        logger.debug("FAISS not available for embedding-based dedup")

    # Sort by confidence descending
    duplicates.sort(key=lambda x: x["confidence"], reverse=True)
    return duplicates


def merge_items(session: Session, src_id: int, dst_id: int, dry_run: bool = True) -> dict:
    """Merge src item into dst item.

    Transfers tags, collections, notes, citations, external_ids from src to dst.
    Sets src.status='merged', src.merged_into_id=dst_id.
    Returns counts of moved entities.
    """
    src = session.get(Item, src_id)
    dst = session.get(Item, dst_id)
    if not src or not dst:
        return {"error": "Item not found", "moved": {}}

    counts = {
        "tags": 0,
        "collections": 0,
        "notes": 0,
        "citations_out": 0,
        "citations_in": 0,
        "external_ids": 0,
    }

    if dry_run:
        # Count what would be moved
        counts["tags"] = len(src.tag_links)
        counts["collections"] = len(src.collection_links)
        counts["notes"] = len(src.notes)
        counts["citations_out"] = len(src.citations_out)
        counts["citations_in"] = len(src.citations_in)
        counts["external_ids"] = len(src.external_ids)
        return {"dry_run": True, "src_id": src_id, "dst_id": dst_id, "moved": counts}

    # Transfer tags (skip duplicates)
    existing_tag_ids = {tl.tag_id for tl in dst.tag_links}
    for tl in list(src.tag_links):
        if tl.tag_id not in existing_tag_ids:
            session.add(ItemTag(item_id=dst_id, tag_id=tl.tag_id, source=tl.source))
            counts["tags"] += 1

    # Transfer collections (skip duplicates)
    existing_coll_ids = {cl.collection_id for cl in dst.collection_links}
    for cl in list(src.collection_links):
        if cl.collection_id not in existing_coll_ids:
            session.add(CollectionItem(collection_id=cl.collection_id, item_id=dst_id))
            counts["collections"] += 1

    # Transfer external IDs (skip duplicates)
    existing_ext = {(eid.id_type, eid.id_value) for eid in dst.external_ids}
    for eid in list(src.external_ids):
        if (eid.id_type, eid.id_value) not in existing_ext:
            session.add(ItemId(item_id=dst_id, id_type=eid.id_type, id_value=eid.id_value))
            counts["external_ids"] += 1

    # Transfer outgoing citations
    for cit in list(src.citations_out):
        cit.src_item_id = dst_id
        counts["citations_out"] += 1

    # Transfer incoming citations
    for cit in list(src.citations_in):
        cit.dst_item_id = dst_id
        counts["citations_in"] += 1

    # Transfer notes (keep all, they're file-based)
    for note in list(src.notes):
        note.item_id = dst_id
        counts["notes"] += 1

    # Mark source as merged
    src.status = "merged"
    src.merged_into_id = dst_id

    session.flush()
    return {"dry_run": False, "src_id": src_id, "dst_id": dst_id, "moved": counts}
