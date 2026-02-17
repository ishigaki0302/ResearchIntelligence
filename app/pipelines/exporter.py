"""BibTeX export pipeline."""

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.bibtex import item_to_bibtex_entry
from app.core.models import Collection, CollectionItem, Item, ItemTag, Tag

logger = logging.getLogger(__name__)


def _build_query(session: Session, filters: dict | None = None) -> list[Item]:
    """Build a filtered query for items."""
    stmt = select(Item)

    if filters:
        if filters.get("year_from"):
            stmt = stmt.where(Item.year >= filters["year_from"])
        if filters.get("year_to"):
            stmt = stmt.where(Item.year <= filters["year_to"])
        if filters.get("venue"):
            stmt = stmt.where(Item.venue.ilike(f"%{filters['venue']}%"))
        if filters.get("type"):
            stmt = stmt.where(Item.type == filters["type"])
        if filters.get("collection"):
            coll = session.execute(
                select(Collection).where(Collection.name.ilike(f"%{filters['collection']}%"))
            ).scalar_one_or_none()
            if coll:
                item_ids = [
                    ci.item_id for ci in
                    session.execute(
                        select(CollectionItem).where(CollectionItem.collection_id == coll.id)
                    ).scalars().all()
                ]
                stmt = stmt.where(Item.id.in_(item_ids))
            else:
                return []
        if filters.get("tag"):
            tag = session.execute(
                select(Tag).where(Tag.name == filters["tag"])
            ).scalar_one_or_none()
            if tag:
                item_ids = [
                    it.item_id for it in
                    session.execute(
                        select(ItemTag).where(ItemTag.tag_id == tag.id)
                    ).scalars().all()
                ]
                stmt = stmt.where(Item.id.in_(item_ids))
            else:
                return []

    stmt = stmt.order_by(Item.year.desc(), Item.title)
    return list(session.execute(stmt).scalars().all())


def export_bibtex(
    session: Session,
    output_path: str | Path = "export.bib",
    filters: dict | None = None,
) -> dict[str, Any]:
    """Export items matching filters as a .bib file.

    Returns: {"count": int, "path": str}
    """
    items = _build_query(session, filters)

    entries = []
    for item in items:
        if item.bibtex_raw:
            entries.append(item.bibtex_raw)
        elif item.bibtex_key:
            entry_type = "misc"
            if item.type == "paper":
                entry_type = "inproceedings" if item.venue else "article"
            bib_str = item_to_bibtex_entry(
                bibtex_key=item.bibtex_key,
                title=item.title,
                authors=item.author_names,
                year=item.year,
                venue=item.venue_instance or item.venue,
                url=item.source_url,
                abstract=item.abstract,
                entry_type=entry_type,
            )
            entries.append(bib_str)

    output = Path(output_path)
    output.write_text("\n\n".join(entries) + "\n", encoding="utf-8")
    logger.info(f"Exported {len(entries)} BibTeX entries to {output}")

    return {"count": len(entries), "path": str(output)}
