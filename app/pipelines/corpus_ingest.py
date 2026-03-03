"""PDF Corpus Ingest Pipeline.

Ingests a directory of PDFs into the items table with type='corpus'.
Extracts title, abstract, and full text using pdfplumber.
Idempotent: skips PDFs already registered by pdf_path.
"""

import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Item

logger = logging.getLogger(__name__)

# Regex to find "Abstract" heading in extracted PDF text
_ABSTRACT_RE = re.compile(r"(?m)^\s*abstract\s*$", re.IGNORECASE)
# Regex to detect next section header (Introduction, 1. Introduction, etc.)
_NEXT_SECTION_RE = re.compile(
    r"(?m)^\s*(?:\d+\.?\s+)?(introduction|related work|background|references"
    r"|acknowledgements?|appendix|keywords?)\s*$",
    re.IGNORECASE,
)


def _extract_structured(pdf_path: Path) -> dict:
    """Extract title, abstract, and full text from a PDF.

    Returns dict with keys: title, abstract, fulltext, page_count.
    Falls back gracefully on any extraction error.
    """
    import pdfplumber

    title = ""
    abstract = ""
    fulltext_parts = []
    page_count = 0

    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    fulltext_parts.append(text)
    except Exception as e:
        # Re-raise so ingest_pdf can mark the item as failed
        raise RuntimeError(f"pdfplumber could not open {pdf_path.name}: {e}") from e

    fulltext = "\n\n".join(fulltext_parts)

    if fulltext:
        # --- Title heuristic ---
        # Take the first 1-2 non-empty lines from the text, limited to 300 chars each.
        lines = [ln.strip() for ln in fulltext.split("\n") if ln.strip()]
        title_lines = []
        for ln in lines[:10]:
            if 10 < len(ln) < 300:
                title_lines.append(ln)
                if len(title_lines) >= 2:
                    break
            elif title_lines:
                break
        title = " ".join(title_lines) if title_lines else pdf_path.stem

        # --- Abstract heuristic ---
        abs_match = _ABSTRACT_RE.search(fulltext)
        if abs_match:
            after = fulltext[abs_match.end() :]
            next_sec = _NEXT_SECTION_RE.search(after)
            raw_abstract = after[: next_sec.start()].strip() if next_sec else after[:2000].strip()
            abstract = re.sub(r"\s+", " ", raw_abstract)[:2000]
        else:
            # Fallback: look for "Abstract" as an inline prefix
            inline = re.search(r"(?i)\babstract[—:\s]+(.{50,1500}?)(?=\n\n|\Z)", fulltext, re.DOTALL)
            if inline:
                abstract = re.sub(r"\s+", " ", inline.group(1)).strip()[:2000]

    return {
        "title": title or pdf_path.stem,
        "abstract": abstract,
        "fulltext": fulltext,
        "page_count": page_count,
    }


def ingest_pdf(pdf_path: Path, session: Session) -> tuple:
    """Ingest a single PDF into the database.

    Returns (item_or_None, status) where status is 'created', 'skipped', or 'failed'.
    """
    pdf_path = pdf_path.resolve()
    pdf_str = str(pdf_path)

    # Idempotency: skip if already registered by pdf_path
    existing = session.execute(select(Item).where(Item.pdf_path == pdf_str)).scalar_one_or_none()
    if existing:
        return existing, "skipped"

    try:
        extracted = _extract_structured(pdf_path)

        # Persist full text next to a hash-named dir inside the library
        cfg = get_config()
        lib_dir = resolve_path(cfg["storage"]["library_dir"])
        name_hash = hashlib.sha256(pdf_str.encode()).hexdigest()[:12]
        item_dir = lib_dir / f"corpus_{name_hash}"
        item_dir.mkdir(parents=True, exist_ok=True)

        text_path = None
        if extracted["fulltext"]:
            text_file = item_dir / "text.txt"
            text_file.write_text(extracted["fulltext"], encoding="utf-8")
            text_path = str(text_file)

        item = Item(
            type="corpus",
            title=extracted["title"],
            abstract=extracted["abstract"] or None,
            pdf_path=pdf_str,
            text_path=text_path,
        )
        session.add(item)
        session.flush()
        return item, "created"

    except Exception as e:
        logger.error(f"Ingest failed for {pdf_path.name}: {e}")
        return None, "failed"


def ingest_directory(
    pdf_dir: Path,
    session: Session,
    show_progress: bool = True,
) -> dict:
    """Ingest all PDFs from a directory.

    Returns dict with keys: created, skipped, failed, total.
    """
    pdf_dir = Path(pdf_dir)
    if not pdf_dir.exists():
        raise FileNotFoundError(f"Directory not found: {pdf_dir}")

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    total = len(pdf_files)

    if total == 0:
        logger.info(f"No PDF files found in {pdf_dir}")
        return {"created": 0, "skipped": 0, "failed": 0, "total": 0}

    logger.info(f"Found {total} PDF files in {pdf_dir}")

    iterable = pdf_files
    if show_progress:
        try:
            from tqdm import tqdm

            iterable = tqdm(pdf_files, desc="Ingesting PDFs", unit="pdf")
        except ImportError:
            pass

    created = skipped = failed = 0
    for pdf_path in iterable:
        _item, status = ingest_pdf(pdf_path, session)
        if status == "created":
            created += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
        session.commit()

    result = {"created": created, "skipped": skipped, "failed": failed, "total": total}
    logger.info(f"Ingest complete: {result}")
    return result
