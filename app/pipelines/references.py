"""Reference extraction from paper text and PDF."""

import hashlib
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Citation, Item

logger = logging.getLogger(__name__)

# Patterns for finding the references section (multi-language)
REFS_HEADING_RE = re.compile(
    r"\n\s*(References|Bibliography|REFERENCES|BIBLIOGRAPHY|"
    r"References and Notes|参考文献|Références|Literatur)\s*\n",
    re.IGNORECASE,
)

# Pattern to split individual references: "[1]", "[2]", etc.
REF_SPLIT_BRACKET = re.compile(r"(?:^|\n)\s*\[(\d+)\]\s*")
# Pattern: numbered-dot format "1. Author..."
REF_SPLIT_NUMBERED_DOT = re.compile(r"(?:^|\n)\s*(\d+)\.\s+(?=[A-Z])")
# Pattern: author-year style with newline separation
REF_SPLIT_NEWLINE = re.compile(r"\n{2,}")

# DOI pattern
DOI_RE = re.compile(r"10\.\d{4,}/[^\s,;}\]]+")
# arXiv pattern
ARXIV_RE = re.compile(r"\b(\d{4}\.\d{4,5})\b")
# ACL Anthology ID patterns
ACL_ID_RE = re.compile(r"\b([A-Z]\d{2}-\d{4})\b")
ACL_ID_NEW_RE = re.compile(r"\b(\d{4}\.[a-z]+-[a-z]+\.\d+)\b")
# OpenReview ID
OPENREVIEW_RE = re.compile(r"forum\?id=([A-Za-z0-9_-]+)")
# URL
URL_RE = re.compile(r"https?://[^\s,;}\]]+")
# ISBN
ISBN_RE = re.compile(r"ISBN[:\s]*([\dX\-]+)")

# Title guess: first sentence-like chunk (capitalized, ending with period)
TITLE_GUESS_RE = re.compile(r"[A-Z][^.?!]{10,200}[.?!]")


def _compute_cite_hash(raw_cite: str) -> str:
    """Compute SHA256 hash of normalized raw citation text for dedup."""
    normalized = re.sub(r"\s+", " ", raw_cite.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_references_from_text(text: str) -> dict:
    """Find the References section and extract individual entries.

    Returns:
        {
            "raw_section": str,
            "entries": [{"raw": str, "doi": str|None, "arxiv": str|None,
                         "acl_id": str|None, "openreview_id": str|None,
                         "url": str|None, "title_guess": str|None,
                         "all_ids": dict}]
        }
    """
    match = REFS_HEADING_RE.search(text)
    raw_section = ""

    if match:
        raw_section = text[match.end() :]
    else:
        # Tail region fallback: try last 20% of text
        tail_start = int(len(text) * 0.8)
        tail = text[tail_start:]
        # Check if it looks like references
        if re.search(r"\[1\]|\b1\.\s+[A-Z]", tail):
            raw_section = tail

    if not raw_section:
        return {"raw_section": "", "entries": []}

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
        # Try numbered-dot format "1. Author..."
        parts = REF_SPLIT_NUMBERED_DOT.split(raw_section)
        if len(parts) > 2:
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
    """Parse a single reference entry to extract all possible IDs."""
    doi_match = DOI_RE.search(raw)
    arxiv_match = ARXIV_RE.search(raw)
    acl_match = ACL_ID_RE.search(raw) or ACL_ID_NEW_RE.search(raw)
    openreview_match = OPENREVIEW_RE.search(raw)
    url_match = URL_RE.search(raw)
    isbn_match = ISBN_RE.search(raw)
    title_match = TITLE_GUESS_RE.search(raw)

    doi = doi_match.group(0).rstrip(".") if doi_match else None
    arxiv = arxiv_match.group(1) if arxiv_match else None
    acl_id = acl_match.group(1) if acl_match else None
    openreview_id = openreview_match.group(1) if openreview_match else None
    url = url_match.group(0).rstrip(".)") if url_match else None
    isbn = isbn_match.group(1) if isbn_match else None
    title_guess = title_match.group(0).rstrip(".") if title_match else None

    # Collect all found IDs
    all_ids = {}
    if doi:
        all_ids["doi"] = doi
    if arxiv:
        all_ids["arxiv"] = arxiv
    if acl_id:
        all_ids["acl"] = acl_id
    if openreview_id:
        all_ids["openreview"] = openreview_id
    if url:
        all_ids["url"] = url
    if isbn:
        all_ids["isbn"] = isbn

    return {
        "raw": raw,
        "doi": doi,
        "arxiv": arxiv,
        "acl_id": acl_id,
        "openreview_id": openreview_id,
        "url": url,
        "title_guess": title_guess,
        "all_ids": all_ids,
    }


def extract_references_for_item(session: Session, item: Item) -> list[dict]:
    """Extract references from an item's text and create Citation rows.

    Uses hash-based dedup: can be re-run to add newly extracted entries.
    Returns list of extracted entry dicts.
    """
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

    # Get existing hashes for this item to dedup
    existing_hashes = set(
        session.execute(
            select(Citation.raw_cite_hash).where(
                Citation.src_item_id == item.id,
                Citation.raw_cite_hash.is_not(None),
            )
        )
        .scalars()
        .all()
    )

    new_entries = []
    for entry in entries:
        cite_hash = _compute_cite_hash(entry["raw"])
        if cite_hash in existing_hashes:
            continue

        # Pick the best dst_key from all available IDs
        dst_key = entry.get("doi") or entry.get("arxiv") or entry.get("acl_id") or None
        cit = Citation(
            src_item_id=item.id,
            raw_cite=entry["raw"][:500],
            dst_key=dst_key,
            source="pdf",
            raw_cite_hash=cite_hash,
        )
        session.add(cit)
        existing_hashes.add(cite_hash)
        new_entries.append(entry)

    session.flush()
    return new_entries


def extract_all_references(session: Session, limit: int | None = None) -> dict:
    """Extract references for all items with text or PDF.

    Uses hash-based dedup so it's safe to re-run.
    Returns {"extracted": int, "skipped": int, "failed": int}.
    """
    items_with_text = (
        session.execute(select(Item).where((Item.text_path.is_not(None)) | (Item.pdf_path.is_not(None))))
        .scalars()
        .all()
    )

    if limit:
        items_with_text = items_with_text[:limit]

    extracted = 0
    skipped = 0
    failed = 0

    for item in items_with_text:
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
