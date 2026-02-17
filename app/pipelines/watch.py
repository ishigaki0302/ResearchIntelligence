"""Watch pipeline — fetch papers from sources, deduplicate, and populate inbox."""

import hashlib
import json
import logging
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import InboxItem, Item, ItemId, Watch
from app.core.service import add_item_to_collection, get_or_create_collection, upsert_item

logger = logging.getLogger(__name__)


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for deduplication: lowercase, strip punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text).lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _compute_dedup_hash(
    source_id_type: str | None,
    source_id_value: str | None,
    title: str | None = None,
    year: int | None = None,
    first_author: str | None = None,
) -> str:
    """Compute a dedup hash for an inbox candidate."""
    if source_id_type and source_id_value:
        raw = f"{source_id_type}:{source_id_value}"
    else:
        norm_title = _normalize_for_dedup(title or "")
        norm_author = _normalize_for_dedup(first_author or "")
        raw = f"{norm_title}:{year or ''}:{norm_author}"
    return hashlib.md5(raw.encode()).hexdigest()


def _already_in_inbox(session: Session, dedup_hash: str) -> bool:
    """Check if a paper with this hash is already in the inbox."""
    return session.execute(select(InboxItem).where(InboxItem.dedup_hash == dedup_hash)).scalar_one_or_none() is not None


def _already_in_items(session: Session, source_id_type: str | None, source_id_value: str | None) -> bool:
    """Check if this paper is already in the main items table."""
    if not source_id_type or not source_id_value:
        return False
    return (
        session.execute(
            select(ItemId).where(ItemId.id_type == source_id_type, ItemId.id_value == source_id_value)
        ).scalar_one_or_none()
        is not None
    )


def run_watch(session: Session, watch: Watch, since_days: int = 14, limit: int = 100) -> dict:
    """Run a single watch: fetch from source, deduplicate, insert into inbox.

    Returns: {"fetched": int, "added": int, "skipped": int}
    """
    from app.core.config import get_config

    cfg = get_config()
    watch_cfg = cfg.get("watch", {})
    source = watch.source.lower()

    filters = json.loads(watch.filters_json) if watch.filters_json else {}

    # Fetch candidates from source
    candidates = []
    if source == "arxiv":
        from app.connectors.arxiv import search_arxiv

        arxiv_cfg = watch_cfg.get("arxiv", {})
        sleep_sec = arxiv_cfg.get("sleep_sec", 3.0)
        max_results = min(limit, arxiv_cfg.get("max_results", 100))
        category = filters.get("category")

        candidates = search_arxiv(
            keyword=watch.query,
            category=category,
            since_days=since_days,
            max_results=max_results,
            sleep_sec=sleep_sec,
        )

    elif source == "openalex":
        from app.connectors.openalex import search_openalex_works

        oa_cfg = watch_cfg.get("openalex", {})
        sleep_sec = oa_cfg.get("sleep_sec", 1.0)
        max_results = min(limit, oa_cfg.get("max_results", 100))

        candidates = search_openalex_works(
            query=watch.query,
            since_days=since_days,
            per_page=max_results,
            sleep_sec=sleep_sec,
        )

    else:
        logger.warning(f"Unknown watch source: {source}")
        return {"fetched": 0, "added": 0, "skipped": 0}

    fetched = len(candidates)
    added = 0
    skipped = 0

    for cand in candidates:
        sid_type = cand.get("source_id_type")
        sid_value = cand.get("source_id_value")
        authors = cand.get("authors", [])
        first_author = authors[0] if authors else None

        dedup_hash = _compute_dedup_hash(sid_type, sid_value, cand.get("title"), cand.get("year"), first_author)

        # Skip if already in inbox or items
        if _already_in_inbox(session, dedup_hash):
            skipped += 1
            continue
        if _already_in_items(session, sid_type, sid_value):
            skipped += 1
            continue

        inbox_item = InboxItem(
            watch_id=watch.id,
            source_id_type=sid_type,
            source_id_value=sid_value,
            title=cand.get("title", ""),
            authors_json=json.dumps(authors, ensure_ascii=False),
            year=cand.get("year"),
            venue=cand.get("venue"),
            url=cand.get("url"),
            abstract=cand.get("abstract"),
            matched_query=watch.query,
            dedup_hash=dedup_hash,
            status="new",
        )
        session.add(inbox_item)
        added += 1

    session.flush()
    return {"fetched": fetched, "added": added, "skipped": skipped}


def accept_inbox_item(session: Session, inbox_item: InboxItem) -> Item:
    """Accept an inbox item: create/update item in main DB, assign to watch collection."""
    authors = json.loads(inbox_item.authors_json) if inbox_item.authors_json else []

    ext_ids = {}
    if inbox_item.source_id_type and inbox_item.source_id_value:
        ext_ids[inbox_item.source_id_type] = inbox_item.source_id_value

    item, _created = upsert_item(
        session,
        title=inbox_item.title,
        authors=authors,
        year=inbox_item.year,
        venue=inbox_item.venue,
        abstract=inbox_item.abstract,
        source_url=inbox_item.url,
        external_ids=ext_ids if ext_ids else None,
    )

    inbox_item.status = "accepted"
    inbox_item.accepted_item_id = item.id

    # Add to watch collection
    watch = inbox_item.watch
    coll_name = f"watch:{watch.name}"
    coll = get_or_create_collection(session, coll_name)
    add_item_to_collection(session, item, coll)

    session.flush()
    return item
