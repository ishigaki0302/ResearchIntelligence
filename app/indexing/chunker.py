"""Text chunking for fine-grained vector search."""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import resolve_path
from app.core.models import Chunk, Item

logger = logging.getLogger(__name__)

# Heading patterns for section splitting
HEADING_RE = re.compile(r"\n(?=#+\s|\n[A-Z][^\n]{3,50}\n(?:={3,}|-{3,})?)")
PARAGRAPH_RE = re.compile(r"\n{2,}")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def chunk_text(text: str, target_size: int = 1000, overlap: int = 150) -> list[dict]:
    """Split text into chunks with heading awareness.

    Returns list of {text, start_char, end_char, chunk_index}.
    """
    if not text or not text.strip():
        return []

    # Step 1: Split by headings
    sections = HEADING_RE.split(text)
    sections = [s for s in sections if s.strip()]

    raw_chunks = []
    for section in sections:
        if len(section) <= target_size * 1.2:
            raw_chunks.append(section.strip())
        else:
            # Step 2: Split large sections by paragraphs
            paragraphs = PARAGRAPH_RE.split(section)
            current = ""
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if len(current) + len(para) + 2 <= target_size * 1.2:
                    current = current + "\n\n" + para if current else para
                else:
                    if current:
                        raw_chunks.append(current)
                    if len(para) > target_size * 1.2:
                        # Step 3: Split large paragraphs by sentences
                        raw_chunks.extend(_split_by_sentences(para, target_size, overlap))
                    else:
                        current = para
                        continue
                    current = ""
            if current:
                raw_chunks.append(current)

    # Build final chunks with char offsets and overlap
    result = []
    pos = 0
    for idx, chunk_text_str in enumerate(raw_chunks):
        # Find actual position in original text
        start = text.find(chunk_text_str[:50], pos)
        if start == -1:
            start = pos
        end = start + len(chunk_text_str)

        result.append(
            {
                "text": chunk_text_str,
                "start_char": start,
                "end_char": end,
                "chunk_index": idx,
            }
        )
        pos = max(pos, start + 1)

    return result


def _split_by_sentences(text: str, target_size: int, overlap: int) -> list[str]:
    """Split text by sentences with overlap."""
    sentences = SENTENCE_RE.split(text)
    chunks = []
    current = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= target_size:
            current = current + " " + sent if current else sent
        else:
            if current:
                chunks.append(current)
            current = sent

    if current:
        chunks.append(current)

    return chunks


def chunk_item(session: Session, item: Item) -> list[Chunk]:
    """Create chunks for an item. Idempotent: deletes old chunks first."""
    if not item.text_path:
        return []

    text_file = resolve_path(item.text_path)
    if not text_file.exists():
        return []

    text = text_file.read_text(encoding="utf-8")
    if not text.strip():
        return []

    # Delete existing chunks (idempotent)
    existing = session.execute(select(Chunk).where(Chunk.item_id == item.id)).scalars().all()
    for c in existing:
        session.delete(c)
    session.flush()

    # Create new chunks
    chunk_dicts = chunk_text(text)
    chunks = []
    for cd in chunk_dicts:
        chunk = Chunk(
            item_id=item.id,
            chunk_index=cd["chunk_index"],
            text=cd["text"],
            start_char=cd["start_char"],
            end_char=cd["end_char"],
        )
        session.add(chunk)
        chunks.append(chunk)

    session.flush()
    return chunks


def chunk_all_items(session: Session, limit: int | None = None) -> dict:
    """Chunk all items with text. Returns {chunked, skipped, failed}."""
    query = select(Item).where(Item.text_path.is_not(None))
    items = session.execute(query).scalars().all()

    if limit:
        items = items[:limit]

    chunked = 0
    skipped = 0
    failed = 0

    for item in items:
        try:
            chunks = chunk_item(session, item)
            if chunks:
                chunked += 1
                logger.info(f"Chunked item {item.id}: {len(chunks)} chunks")
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Chunking failed for item {item.id}: {e}")

    session.commit()
    return {"chunked": chunked, "skipped": skipped, "failed": failed}
