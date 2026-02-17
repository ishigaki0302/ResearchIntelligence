"""Reference extraction from paper text and PDF."""

import json
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import resolve_path, get_config
from app.core.models import Citation, Item

logger = logging.getLogger(__name__)

# Patterns for finding the references section
REFS_HEADING_RE = re.compile(
    r"\n\s*(References|Bibliography|REFERENCES|BIBLIOGRAPHY)\s*\n", re.IGNORECASE
)

# Pattern to split individual references: "[1]", "[2]", etc.
REF_SPLIT_BRACKET = re.compile(r"(?:^|\n)\s*\[(\d+)\]\s*")
# Pattern: author-year style with newline separation
REF_SPLIT_NEWLINE = re.compile(r"\n{2,}")

# DOI pattern
DOI_RE = re.compile(r"10\.\d{4,}/[^\s,;}\]]+")
# arXiv pattern
ARXIV_RE = re.compile(r"\b(\d{4}\.\d{4,5})\b")

# Title guess: first sentence-like chunk (capitalized, ending with period)
TITLE_GUESS_RE = re.compile(r"[A-Z][^.?!]{10,200}[.?!]")


def extract_references_from_text(text: str) -> dict:
    """Find the References section and extract individual entries.

    Returns:
        {
            "raw_section": str,
            "entries": [{"raw": str, "doi": str|None, "arxiv": str|None, "title_guess": str|None}]
        }
    """
    match = REFS_HEADING_RE.search(text)
    if not match:
        return {"raw_section": "", "entries": []}

    raw_section = text[match.end():]

    # Try bracket-numbered references first
    entries = []
    parts = REF_SPLIT_BRACKET.split(raw_section)
    if len(parts) > 2:
        # parts: [preamble, num1, text1, num2, text2, ...]
        for i in range(1, len(parts), 2):
            if i + 1 < len(parts):
                raw = parts[i + 1].strip()
                if raw:
                    entries.append(_parse_entry(raw))
    else:
        # Fallback: split by double newlines
        for chunk in REF_SPLIT_NEWLINE.split(raw_section):
            chunk = chunk.strip()
            if len(chunk) > 20:  # skip very short fragments
                entries.append(_parse_entry(chunk))

    return {"raw_section": raw_section, "entries": entries}


def _parse_entry(raw: str) -> dict:
    """Parse a single reference entry to extract DOI, arXiv, title guess."""
    doi_match = DOI_RE.search(raw)
    arxiv_match = ARXIV_RE.search(raw)
    title_match = TITLE_GUESS_RE.search(raw)

    return {
        "raw": raw,
        "doi": doi_match.group(0).rstrip(".") if doi_match else None,
        "arxiv": arxiv_match.group(1) if arxiv_match else None,
        "title_guess": title_match.group(0).rstrip(".") if title_match else None,
    }


def extract_references_for_item(session: Session, item: Item) -> list[dict]:
    """Extract references from an item's text and create Citation rows.

    Returns list of extracted entry dicts.
    """
    # Check idempotency: skip if citations with source="pdf" already exist
    existing = session.execute(
        select(Citation).where(
            Citation.src_item_id == item.id,
            Citation.source == "pdf",
        )
    ).scalars().first()
    if existing:
        return []

    # Get text content
    text = None
    if item.text_path:
        text_file = resolve_path(item.text_path)
        if text_file.exists():
            text = text_file.read_text(encoding="utf-8")

    if not text and item.pdf_path:
        pdf_file = resolve_path(item.pdf_path)
        if pdf_file.exists():
            from app.pipelines.extract import extract_pdf_text
            text = extract_pdf_text(pdf_file)

    if not text:
        return []

    result = extract_references_from_text(text)
    entries = result["entries"]

    if not entries:
        return entries

    # Cache raw references
    cfg = get_config()
    cache_dir = resolve_path(cfg["storage"]["cache_raw_dir"]) / "references"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{item.id}.json"
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # Create Citation rows
    for entry in entries:
        dst_key = entry.get("doi") or entry.get("arxiv") or None
        cit = Citation(
            src_item_id=item.id,
            raw_cite=entry["raw"][:500],  # truncate very long raw cites
            dst_key=dst_key,
            source="pdf",
        )
        session.add(cit)

    session.flush()
    return entries


def extract_all_references(session: Session, limit: int | None = None) -> dict:
    """Extract references for all items with text or PDF that haven't been processed yet.

    Returns {"extracted": int, "skipped": int, "failed": int}.
    """
    # Find items with text/PDF that don't already have pdf-source citations
    items_with_text = session.execute(
        select(Item).where(
            (Item.text_path.is_not(None)) | (Item.pdf_path.is_not(None))
        )
    ).scalars().all()

    # Filter out items already processed
    processed_ids = set(
        session.execute(
            select(Citation.src_item_id).where(Citation.source == "pdf").distinct()
        ).scalars().all()
    )

    items = [i for i in items_with_text if i.id not in processed_ids]
    if limit:
        items = items[:limit]

    extracted = 0
    skipped = 0
    failed = 0

    for item in items:
        try:
            entries = extract_references_for_item(session, item)
            if entries:
                extracted += 1
                logger.info(f"Extracted {len(entries)} references from item {item.id}")
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Reference extraction failed for item {item.id}: {e}")

    session.commit()
    return {"extracted": extracted, "skipped": skipped, "failed": failed}
