"""Enrichment pipeline: add external IDs from OpenAlex and Semantic Scholar."""

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.core.models import Item, ItemId

logger = logging.getLogger(__name__)


def _get_item_ext_ids(session: Session, item: Item) -> dict[str, str]:
    """Get existing external IDs for an item as a dict."""
    links = session.execute(select(ItemId).where(ItemId.item_id == item.id)).scalars().all()
    return {link.id_type: link.id_value for link in links}


def _add_ext_id(session: Session, item: Item, id_type: str, id_value: str) -> bool:
    """Add an external ID if not already present. Returns True if added."""
    existing = session.execute(
        select(ItemId).where(ItemId.id_type == id_type, ItemId.id_value == id_value)
    ).scalar_one_or_none()
    if existing:
        return False
    session.add(ItemId(item_id=item.id, id_type=id_type, id_value=id_value))
    session.flush()
    return True


def enrich_item(session: Session, item: Item, update_metadata: bool = False) -> dict:
    """Enrich a single item with external IDs from S2 and OpenAlex.

    Returns {"ids_added": list, "source": "s2"|"openalex"|None}.
    """
    ext_ids = _get_item_ext_ids(session, item)
    ids_added = []
    source = None

    cfg = get_config()
    threshold = cfg.get("external", {}).get("enrich", {}).get("match_threshold", 0.85)

    # Try Semantic Scholar first (direct lookup by DOI or arXiv)
    s2_cfg = cfg.get("external", {}).get("semantic_scholar", {})
    if s2_cfg.get("enabled", False):
        from app.connectors.semantic_scholar import (
            extract_ids_from_s2,
            lookup_s2_by_arxiv,
            lookup_s2_by_doi,
        )

        s2_paper = None
        if "doi" in ext_ids:
            s2_paper = lookup_s2_by_doi(ext_ids["doi"])
        if not s2_paper and "arxiv" in ext_ids:
            s2_paper = lookup_s2_by_arxiv(ext_ids["arxiv"])

        if s2_paper:
            source = "s2"
            new_ids = extract_ids_from_s2(s2_paper)
            for id_type, id_value in new_ids.items():
                if id_type not in ext_ids:
                    if _add_ext_id(session, item, id_type, id_value):
                        ids_added.append(f"{id_type}:{id_value}")

            if update_metadata and s2_paper.get("title"):
                if not item.title or len(item.title) < len(s2_paper["title"]):
                    item.title = s2_paper["title"]
                if s2_paper.get("year") and not item.year:
                    item.year = s2_paper["year"]

    # Try OpenAlex if S2 didn't yield results
    oa_cfg = cfg.get("external", {}).get("openalex", {})
    if not source and oa_cfg.get("enabled", False):
        from app.connectors.openalex import (
            extract_ids_from_openalex,
            score_match,
            search_openalex,
        )

        candidates = search_openalex(
            item.title,
            year=item.year,
            first_author=item.author_names[0] if item.author_names else None,
        )

        best = None
        best_score = 0.0
        for cand in candidates:
            s = score_match(cand, item.title, item.year, item.author_names)
            if s > best_score:
                best_score = s
                best = cand

        if best and best_score >= threshold:
            source = "openalex"
            new_ids = extract_ids_from_openalex(best)
            for id_type, id_value in new_ids.items():
                if id_type not in ext_ids:
                    if _add_ext_id(session, item, id_type, id_value):
                        ids_added.append(f"{id_type}:{id_value}")

            if update_metadata and best.get("title"):
                if not item.title or len(item.title) < len(best["title"]):
                    item.title = best["title"]
                if best.get("publication_year") and not item.year:
                    item.year = best["publication_year"]

    session.flush()
    return {"ids_added": ids_added, "source": source}


def enrich_items(
    session: Session,
    items: list[Item],
    update_metadata: bool = False,
) -> dict:
    """Enrich multiple items with external IDs.

    Returns {"enriched": int, "skipped": int, "failed": int, "ids_added": int}.
    """
    cfg = get_config()
    sleep_sec = cfg.get("external", {}).get("enrich", {}).get("sleep_sec", 1.0)

    enriched = 0
    skipped = 0
    failed = 0
    total_ids = 0

    for i, item in enumerate(items):
        try:
            result = enrich_item(session, item, update_metadata=update_metadata)
            if result["ids_added"]:
                enriched += 1
                total_ids += len(result["ids_added"])
                logger.info(f"[{i+1}/{len(items)}] Enriched: {item.title[:60]} +{result['ids_added']}")
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            logger.warning(f"[{i+1}/{len(items)}] Enrichment failed for item {item.id}: {e}")

        if i < len(items) - 1:
            time.sleep(sleep_sec)

    session.commit()
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "ids_added": total_ids}
