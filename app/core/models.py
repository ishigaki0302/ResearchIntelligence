"""SQLAlchemy ORM models for the paper management system."""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Item(Base):
    """A paper, blog post, slide deck, or other document."""

    __tablename__ = "items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(32), nullable=False, default="paper")  # paper/blog/slide/note
    title = Column(Text, nullable=False)
    tldr = Column(Text)
    abstract = Column(Text)
    year = Column(Integer)
    date = Column(String(16))  # ISO date string
    venue = Column(String(128))  # e.g. "ACL"
    venue_instance = Column(String(128))  # e.g. "ACL 2024"
    source_url = Column(Text)
    pdf_path = Column(Text)
    content_path = Column(Text)
    text_path = Column(Text)
    bibtex_key = Column(String(256), unique=True)
    bibtex_raw = Column(Text)  # raw BibTeX entry
    text_hash = Column(String(64))  # SHA256 of title+abstract for incremental indexing
    status = Column(String(16), nullable=False, default="active")  # active/merged
    merged_into_id = Column(Integer, ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    external_ids = relationship("ItemId", back_populates="item", cascade="all, delete-orphan")
    author_links = relationship(
        "ItemAuthor", back_populates="item", cascade="all, delete-orphan", order_by="ItemAuthor.position"
    )
    tag_links = relationship("ItemTag", back_populates="item", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="item", cascade="all, delete-orphan")
    collection_links = relationship("CollectionItem", back_populates="item", cascade="all, delete-orphan")
    citations_out = relationship(
        "Citation", foreign_keys="Citation.src_item_id", back_populates="src_item", cascade="all, delete-orphan"
    )
    citations_in = relationship(
        "Citation", foreign_keys="Citation.dst_item_id", back_populates="dst_item", cascade="all, delete-orphan"
    )

    @property
    def authors(self) -> list["Author"]:
        return [link.author for link in sorted(self.author_links, key=lambda x: x.position)]

    @property
    def author_names(self) -> list[str]:
        return [a.name for a in self.authors]

    def __repr__(self) -> str:
        return f"<Item(id={self.id}, type={self.type}, title={self.title!r:.60})>"


class ItemId(Base):
    """External identifiers for an item (DOI, arXiv ID, ACL Anthology ID, etc.)."""

    __tablename__ = "item_ids"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    id_type = Column(String(32), nullable=False)  # doi/arxiv/acl/s2
    id_value = Column(String(512), nullable=False)

    item = relationship("Item", back_populates="external_ids")

    __table_args__ = (UniqueConstraint("id_type", "id_value", name="uq_item_ids_type_value"),)


class Author(Base):
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    norm_name = Column(Text, nullable=False, index=True)

    item_links = relationship("ItemAuthor", back_populates="author")


class ItemAuthor(Base):
    __tablename__ = "item_authors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    author_id = Column(Integer, ForeignKey("authors.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False, default=0)

    item = relationship("Item", back_populates="author_links")
    author = relationship("Author", back_populates="item_links")

    __table_args__ = (UniqueConstraint("item_id", "author_id", name="uq_item_authors"),)


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False, unique=True)


class ItemTag(Base):
    __tablename__ = "item_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(64))  # "manual", "auto", "connector"

    item = relationship("Item", back_populates="tag_links")
    tag = relationship("Tag")

    __table_args__ = (UniqueConstraint("item_id", "tag_id", name="uq_item_tags"),)


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    path = Column(Text, nullable=False)
    title = Column(Text, default="main")
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    item = relationship("Item", back_populates="notes")


class Collection(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False, unique=True)
    spec_json = Column(Text)
    created_at = Column(DateTime, default=_utcnow)

    item_links = relationship("CollectionItem", back_populates="collection", cascade="all, delete-orphan")


class CollectionItem(Base):
    __tablename__ = "collection_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)

    collection = relationship("Collection", back_populates="item_links")
    item = relationship("Item", back_populates="collection_links")

    __table_args__ = (UniqueConstraint("collection_id", "item_id", name="uq_collection_items"),)


class Citation(Base):
    __tablename__ = "citations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    src_item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    dst_item_id = Column(Integer, ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    raw_cite = Column(Text)
    dst_key = Column(Text)
    context = Column(Text)
    source = Column(String(64))  # "bibtex", "note", "auto"
    raw_cite_hash = Column(String(64))  # SHA256 of normalized raw_cite for dedup

    src_item = relationship("Item", foreign_keys=[src_item_id], back_populates="citations_out")
    dst_item = relationship("Item", foreign_keys=[dst_item_id], back_populates="citations_in")

    __table_args__ = (UniqueConstraint("src_item_id", "raw_cite_hash", name="uq_citation_src_hash"),)


class Chunk(Base):
    """A text chunk from an item, used for fine-grained vector search."""

    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    start_char = Column(Integer)
    end_char = Column(Integer)
    created_at = Column(DateTime, default=_utcnow)

    item = relationship("Item", backref="chunks")

    __table_args__ = (UniqueConstraint("item_id", "chunk_index", name="uq_chunk_item_index"),)


class Watch(Base):
    """A saved search / watch for continuous paper discovery."""

    __tablename__ = "watches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False, unique=True)
    source = Column(String(32), nullable=False)  # arxiv/openalex/s2/acl
    query = Column(Text, nullable=False)
    filters_json = Column(Text)  # JSON: category, date range, etc.
    schedule_json = Column(Text)  # JSON: cron or interval config
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    inbox_items = relationship("InboxItem", back_populates="watch", cascade="all, delete-orphan")


class InboxItem(Base):
    """A paper discovered by a watch, pending user review."""

    __tablename__ = "inbox_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    watch_id = Column(Integer, ForeignKey("watches.id", ondelete="CASCADE"), nullable=False)
    discovered_at = Column(DateTime, default=_utcnow)
    source_id_type = Column(String(32))  # arxiv/doi/openalex
    source_id_value = Column(String(512))
    title = Column(Text, nullable=False)
    authors_json = Column(Text)  # JSON list of author names
    year = Column(Integer)
    venue = Column(String(128))
    url = Column(Text)
    abstract = Column(Text)
    score = Column(Float)  # relevance score from connector
    matched_query = Column(Text)
    raw_json_path = Column(Text)  # path to cached raw result
    status = Column(String(16), nullable=False, default="new")  # new/accepted/rejected
    accepted_item_id = Column(Integer, ForeignKey("items.id", ondelete="SET NULL"), nullable=True)
    dedup_hash = Column(String(64), index=True)
    recommended = Column(Boolean, default=False)
    recommend_score = Column(Float)
    reasons_json = Column(Text)  # JSON list of reason strings
    auto_tags_json = Column(Text)  # JSON list of suggested tag strings
    auto_accept = Column(Boolean, default=False)
    auto_accept_score = Column(Float)
    quality_flags_json = Column(Text)  # JSON list of quality flag strings

    watch = relationship("Watch", back_populates="inbox_items")
    accepted_item = relationship("Item")

    __table_args__ = (UniqueConstraint("source_id_type", "source_id_value", name="uq_inbox_source_id"),)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")  # pending/running/done/failed
    payload_json = Column(Text)
    error = Column(Text)
    summary_json = Column(Text)  # JSON: counts, duration, etc.
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
