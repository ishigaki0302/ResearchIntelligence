"""Core service layer for item management.

Handles CRUD, idempotent upsert, note generation, and author management.
"""

import json
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.bibtex import generate_bibtex_key, normalize_name
from app.core.config import get_config, resolve_path
from app.core.models import (
    Author,
    Citation,
    Collection,
    CollectionItem,
    Item,
    ItemAuthor,
    ItemId,
    ItemTag,
    Note,
    Tag,
)


def _library_dir() -> Path:
    cfg = get_config()
    return resolve_path(cfg["storage"]["library_dir"])


def _paper_dir(paper_id: int) -> Path:
    d = _library_dir() / str(paper_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_or_create_author(session: Session, name: str) -> Author:
    """Find or create an author by normalized name."""
    norm = normalize_name(name)
    author = session.execute(select(Author).where(Author.norm_name == norm)).scalar_one_or_none()
    if author is None:
        author = Author(name=name, norm_name=norm)
        session.add(author)
        session.flush()
    return author


def get_or_create_tag(session: Session, name: str) -> Tag:
    """Find or create a tag."""
    tag = session.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


def find_item_by_external_id(session: Session, id_type: str, id_value: str) -> Item | None:
    """Look up an item by external ID (DOI, arXiv, ACL anthology ID, etc.)."""
    link = session.execute(
        select(ItemId).where(ItemId.id_type == id_type, ItemId.id_value == id_value)
    ).scalar_one_or_none()
    if link:
        return link.item
    return None


def find_item_by_bibtex_key(session: Session, key: str) -> Item | None:
    """Look up an item by its BibTeX key."""
    return session.execute(select(Item).where(Item.bibtex_key == key)).scalar_one_or_none()


def _collect_existing_bibtex_keys(session: Session) -> set[str]:
    rows = session.execute(select(Item.bibtex_key).where(Item.bibtex_key.is_not(None))).scalars().all()
    return {k for k in rows if k}


def upsert_item(
    session: Session,
    *,
    item_type: str = "paper",
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    date: str | None = None,
    venue: str | None = None,
    venue_instance: str | None = None,
    abstract: str | None = None,
    tldr: str | None = None,
    source_url: str | None = None,
    bibtex_key: str | None = None,
    bibtex_raw: str | None = None,
    external_ids: dict[str, str] | None = None,
    tags: list[str] | None = None,
    pdf_source: str | Path | None = None,
    content_path: str | None = None,
) -> tuple[Item, bool]:
    """Insert or update an item. Returns (item, created).

    Idempotency: checks external IDs first, then bibtex_key, then (title+year) match.
    """
    # 1. Check external IDs for existing item
    existing = None
    if external_ids:
        for id_type, id_value in external_ids.items():
            existing = find_item_by_external_id(session, id_type, id_value)
            if existing:
                break

    # 2. Check bibtex_key
    if not existing and bibtex_key:
        existing = find_item_by_bibtex_key(session, bibtex_key)

    # 3. Fuzzy: title + year match
    if not existing and title and year:
        existing = session.execute(
            select(Item).where(
                Item.title == title,
                Item.year == year,
            )
        ).scalar_one_or_none()

    created = existing is None

    if existing:
        item = existing
        # Update fields if they were empty
        if not item.abstract and abstract:
            item.abstract = abstract
        if not item.tldr and tldr:
            item.tldr = tldr
        if not item.source_url and source_url:
            item.source_url = source_url
        if not item.bibtex_raw and bibtex_raw:
            item.bibtex_raw = bibtex_raw
        if not item.venue and venue:
            item.venue = venue
        if not item.venue_instance and venue_instance:
            item.venue_instance = venue_instance
    else:
        # Generate bibtex_key if not provided
        if not bibtex_key:
            existing_keys = _collect_existing_bibtex_keys(session)
            bibtex_key = generate_bibtex_key(authors or [], year, title, existing_keys)

        item = Item(
            type=item_type,
            title=title,
            abstract=abstract,
            tldr=tldr,
            year=year,
            date=date,
            venue=venue,
            venue_instance=venue_instance,
            source_url=source_url,
            bibtex_key=bibtex_key,
            bibtex_raw=bibtex_raw,
            content_path=content_path,
        )
        session.add(item)
        session.flush()  # get item.id

        # Authors (deduplicate by author_id)
        if authors:
            seen_author_ids = set()
            for i, name in enumerate(authors):
                author = get_or_create_author(session, name)
                if author.id in seen_author_ids:
                    continue
                seen_author_ids.add(author.id)
                link = ItemAuthor(item_id=item.id, author_id=author.id, position=i)
                session.add(link)

    # External IDs (always add missing ones)
    if external_ids:
        for id_type, id_value in external_ids.items():
            exists = session.execute(
                select(ItemId).where(ItemId.id_type == id_type, ItemId.id_value == id_value)
            ).scalar_one_or_none()
            if not exists:
                session.add(ItemId(item_id=item.id, id_type=id_type, id_value=id_value))

    # Tags
    if tags:
        for tag_name in tags:
            tag = get_or_create_tag(session, tag_name)
            exists = session.execute(
                select(ItemTag).where(ItemTag.item_id == item.id, ItemTag.tag_id == tag.id)
            ).scalar_one_or_none()
            if not exists:
                session.add(ItemTag(item_id=item.id, tag_id=tag.id, source="import"))

    session.flush()

    # Copy PDF if provided and item is new
    if created and pdf_source:
        pdf_path_src = Path(pdf_source)
        if pdf_path_src.exists():
            dest_dir = _paper_dir(item.id)
            dest = dest_dir / "paper.pdf"
            shutil.copy2(pdf_path_src, dest)
            item.pdf_path = str(dest.relative_to(resolve_path(".")))

    # Create note scaffold
    if created:
        ensure_note(session, item)

    session.flush()
    return item, created


def ensure_note(session: Session, item: Item) -> Note:
    """Ensure a main.md note exists for the item."""
    existing = session.execute(select(Note).where(Note.item_id == item.id, Note.title == "main")).scalar_one_or_none()
    if existing:
        return existing

    note_dir = _paper_dir(item.id)
    note_path = note_dir / "notes" / "main.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    if not note_path.exists():
        note_path.write_text(
            f"# {item.title}\n\n## Summary\n\n\n## Key Points\n\n\n## Notes\n\n",
            encoding="utf-8",
        )
    rel_path = str(note_path.relative_to(resolve_path(".")))
    note = Note(item_id=item.id, path=rel_path, title="main")
    session.add(note)
    session.flush()
    return note


def get_or_create_collection(session: Session, name: str, spec: dict | None = None) -> Collection:
    """Find or create a collection by name."""
    coll = session.execute(select(Collection).where(Collection.name == name)).scalar_one_or_none()
    if coll is None:
        coll = Collection(name=name, spec_json=json.dumps(spec) if spec else None)
        session.add(coll)
        session.flush()
    return coll


def add_item_to_collection(session: Session, item: Item, collection: Collection):
    """Add item to collection if not already there."""
    exists = session.execute(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection.id,
            CollectionItem.item_id == item.id,
        )
    ).scalar_one_or_none()
    if not exists:
        session.add(CollectionItem(collection_id=collection.id, item_id=item.id))


def add_tag_to_item(session: Session, item_id: int, tag_name: str, source: str = "manual") -> ItemTag:
    """Add a tag to an item. Idempotent — returns existing ItemTag if already present."""
    tag = get_or_create_tag(session, tag_name)
    existing = session.execute(
        select(ItemTag).where(ItemTag.item_id == item_id, ItemTag.tag_id == tag.id)
    ).scalar_one_or_none()
    if existing:
        return existing
    link = ItemTag(item_id=item_id, tag_id=tag.id, source=source)
    session.add(link)
    session.flush()
    return link


def remove_tag_from_item(session: Session, item_id: int, tag_name: str) -> bool:
    """Remove a tag from an item. Returns True if removed, False if not found."""
    tag = session.execute(select(Tag).where(Tag.name == tag_name)).scalar_one_or_none()
    if not tag:
        return False
    link = session.execute(
        select(ItemTag).where(ItemTag.item_id == item_id, ItemTag.tag_id == tag.id)
    ).scalar_one_or_none()
    if not link:
        return False
    session.delete(link)
    session.flush()
    return True


def list_tags_for_item(session: Session, item_id: int) -> list[str]:
    """Get all tag names for an item."""
    links = session.execute(select(ItemTag).where(ItemTag.item_id == item_id)).scalars().all()
    tag_ids = [link.tag_id for link in links]
    if not tag_ids:
        return []
    tags = session.execute(select(Tag).where(Tag.id.in_(tag_ids))).scalars().all()
    return sorted(t.name for t in tags)


def add_citation(
    session: Session,
    src_item: Item,
    dst_key: str,
    raw_cite: str | None = None,
    context: str | None = None,
    source: str = "bibtex",
) -> Citation:
    """Add a citation from src_item, resolving dst_item_id if possible."""
    dst_item = find_item_by_bibtex_key(session, dst_key)
    cit = Citation(
        src_item_id=src_item.id,
        dst_item_id=dst_item.id if dst_item else None,
        raw_cite=raw_cite,
        dst_key=dst_key,
        context=context,
        source=source,
    )
    session.add(cit)
    return cit
