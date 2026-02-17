"""Import pipelines for different source types.

Supports: bib files, PDFs, URLs, ACL Anthology collections.
All imports are idempotent.
"""

import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.connectors.acl import fetch_acl_papers
from app.core.bibtex import parse_bibtex_file, parse_author_string
from app.core.db import get_session, init_db
from app.core.service import (
    add_item_to_collection,
    get_or_create_collection,
    upsert_item,
)

logger = logging.getLogger(__name__)


def import_bibtex(path: str | Path, session: Session | None = None) -> dict[str, Any]:
    """Import papers from a .bib file.

    Returns: {"imported": int, "skipped": int, "total": int}
    """
    own_session = session is None
    if own_session:
        init_db()
        session = get_session()

    path = Path(path)
    entries = parse_bibtex_file(path)
    imported = 0
    skipped = 0

    try:
        for entry in entries:
            etype = entry.get("ENTRYTYPE", "")
            if etype == "proceedings":
                continue

            title = entry.get("title", "").strip()
            title = re.sub(r"[{}]", "", title)
            if not title:
                continue

            author_str = entry.get("author", "")
            authors = parse_author_string(author_str) if author_str else []

            year = None
            if "year" in entry:
                try:
                    year = int(entry["year"])
                except (ValueError, TypeError):
                    pass

            bib_key = entry.get("ID", "")
            doi = entry.get("doi", "")
            url = entry.get("url", "")
            abstract = entry.get("abstract", "")
            if abstract:
                abstract = re.sub(r"[{}]", "", abstract).strip()
            venue = entry.get("booktitle", entry.get("journal", ""))
            venue = re.sub(r"[{}]", "", venue) if venue else None

            ext_ids = {}
            if doi:
                ext_ids["doi"] = doi

            # Re-serialize the entry
            raw_lines = [f"@{etype}{{{bib_key},"]
            for k, v in sorted(entry.items()):
                if k in ("ENTRYTYPE", "ID"):
                    continue
                raw_lines.append(f"  {k} = {{{v}}},")
            raw_lines.append("}")
            bibtex_raw = "\n".join(raw_lines)

            item, created = upsert_item(
                session,
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract,
                source_url=url,
                bibtex_key=bib_key if bib_key else None,
                bibtex_raw=bibtex_raw,
                external_ids=ext_ids if ext_ids else None,
            )
            if created:
                imported += 1
            else:
                skipped += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()

    return {"imported": imported, "skipped": skipped, "total": len(entries)}


def import_pdf(
    path: str | Path,
    title: str | None = None,
    year: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Import a single PDF file.

    If title is not provided, uses the filename.
    """
    own_session = session is None
    if own_session:
        init_db()
        session = get_session()

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    if title is None:
        title = path.stem.replace("_", " ").replace("-", " ")

    try:
        item, created = upsert_item(
            session,
            title=title,
            year=year,
            pdf_source=str(path),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()

    return {"item_id": item.id, "created": created, "title": item.title}


def import_url(
    url: str,
    item_type: str = "blog",
    title: str | None = None,
    year: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Import a URL (blog, slide, etc.)."""
    own_session = session is None
    if own_session:
        init_db()
        session = get_session()

    if title is None:
        title = url  # placeholder; text extraction can improve this later

    try:
        item, created = upsert_item(
            session,
            item_type=item_type,
            title=title,
            year=year,
            source_url=url,
            external_ids={"url": url},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()

    return {"item_id": item.id, "created": created, "title": item.title}


def import_acl(
    event: str,
    year: int,
    volumes: list[str] | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Import papers from ACL Anthology.

    Args:
        event: e.g. "acl", "emnlp"
        year: e.g. 2024
        volumes: e.g. ["main", "findings"]. None = all available.
    """
    own_session = session is None
    if own_session:
        init_db()
        session = get_session()

    papers = fetch_acl_papers(event, year, volumes)
    logger.info(f"Fetched {len(papers)} papers from {event.upper()} {year}")

    # Create collection
    vol_str = ",".join(volumes) if volumes else "all"
    coll_name = f"{event.upper()} {year} ({vol_str})"
    collection = get_or_create_collection(
        session, coll_name,
        spec={"event": event, "year": year, "volumes": volumes},
    )

    imported = 0
    skipped = 0

    try:
        for paper in papers:
            ext_ids = {}
            if paper.get("acl_id"):
                ext_ids["acl"] = paper["acl_id"]
            if paper.get("doi"):
                ext_ids["doi"] = paper["doi"]

            tags = []
            if paper.get("volume_type"):
                tags.append(f"acl/{paper['volume_type']}")

            item, created = upsert_item(
                session,
                title=paper["title"],
                authors=paper.get("authors", []),
                year=paper.get("year"),
                venue=paper.get("venue"),
                venue_instance=paper.get("venue_instance"),
                abstract=paper.get("abstract"),
                source_url=paper.get("source_url"),
                bibtex_key=paper.get("bibtex_key"),
                bibtex_raw=paper.get("bibtex_raw"),
                external_ids=ext_ids if ext_ids else None,
                tags=tags if tags else None,
            )

            add_item_to_collection(session, item, collection)

            if created:
                imported += 1
            else:
                skipped += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()

    return {
        "event": event.upper(),
        "year": year,
        "volumes": volumes,
        "collection": coll_name,
        "imported": imported,
        "skipped": skipped,
        "total": len(papers),
    }


def parse_import_spec(spec: str) -> dict[str, Any]:
    """Parse an import spec string like 'acl:2024{main,findings}' or 'bib:/path/file.bib'.

    Returns: {"type": str, "args": dict}
    """
    # ACL-like: acl:2024 or acl:2024{main,findings}
    m = re.match(r"^(\w+):(\d{4})(?:\{([^}]+)\})?$", spec)
    if m:
        event = m.group(1)
        year = int(m.group(2))
        volumes = None
        if m.group(3):
            volumes = [v.strip() for v in m.group(3).split(",")]
        return {"type": "acl", "args": {"event": event, "year": year, "volumes": volumes}}

    # BibTeX: bib:/path/to/file.bib
    if spec.startswith("bib:"):
        path = spec[4:]
        return {"type": "bib", "args": {"path": path}}

    # PDF: pdf:/path/to/file.pdf
    if spec.startswith("pdf:"):
        path = spec[4:]
        return {"type": "pdf", "args": {"path": path}}

    # URL: url:https://...
    if spec.startswith("url:"):
        url = spec[4:]
        return {"type": "url", "args": {"url": url}}

    raise ValueError(f"Unknown import spec: {spec}")
