"""Text extraction from PDF and HTML/URL sources."""

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import resolve_path, get_config
from app.core.models import Item

logger = logging.getLogger(__name__)


def extract_pdf_text(pdf_path: str | Path) -> str:
    """Extract text from a PDF using pdfplumber."""
    import pdfplumber

    pdf_path = Path(pdf_path)
    texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                texts.append(text)
    return "\n\n".join(texts)


def extract_url_text(url: str) -> tuple[str, str | None]:
    """Extract main text content from a URL.

    Returns: (text, title_or_none)
    """
    import requests
    from readability import Document

    resp = requests.get(url, timeout=30, headers={"User-Agent": "ResearchIndex/0.1"})
    resp.raise_for_status()
    doc = Document(resp.text)
    title = doc.title()

    # Get clean text from the summary HTML
    import re
    html_content = doc.summary()
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", html_content)
    text = re.sub(r"\s+", " ", text).strip()
    return text, title


def extract_text_for_item(item: Item, session: Session) -> bool:
    """Extract text for an item and save to disk.

    Returns True if text was extracted.
    """
    if item.text_path:
        text_file = resolve_path(item.text_path)
        if text_file.exists():
            return False  # already done

    text = None

    # Try PDF first
    if item.pdf_path:
        pdf_file = resolve_path(item.pdf_path)
        if pdf_file.exists():
            try:
                text = extract_pdf_text(pdf_file)
                logger.info(f"Extracted {len(text)} chars from PDF for item {item.id}")
            except Exception as e:
                logger.warning(f"PDF extraction failed for item {item.id}: {e}")

    # Try URL if no PDF text
    if not text and item.source_url and item.type in ("blog", "slide"):
        try:
            text, title = extract_url_text(item.source_url)
            if title and item.title == item.source_url:
                item.title = title
            logger.info(f"Extracted {len(text)} chars from URL for item {item.id}")
        except Exception as e:
            logger.warning(f"URL extraction failed for item {item.id}: {e}")

    if not text:
        return False

    # Save text file
    cfg = get_config()
    lib_dir = resolve_path(cfg["storage"]["library_dir"])
    item_dir = lib_dir / str(item.id)
    item_dir.mkdir(parents=True, exist_ok=True)
    text_file = item_dir / "text.txt"
    text_file.write_text(text, encoding="utf-8")

    item.text_path = str(text_file.relative_to(resolve_path(".")))
    session.flush()
    return True


def extract_all(session: Session) -> dict:
    """Extract text for all items that don't have it yet."""
    items = session.execute(
        select(Item).where(Item.text_path.is_(None))
    ).scalars().all()

    extracted = 0
    failed = 0
    for item in items:
        try:
            if extract_text_for_item(item, session):
                extracted += 1
        except Exception as e:
            logger.error(f"Extraction failed for item {item.id}: {e}")
            failed += 1

    session.commit()
    return {"extracted": extracted, "failed": failed, "total": len(items)}
