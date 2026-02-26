"""Import pipelines for different source types.

Supports: bib files, PDFs, URLs, ACL Anthology collections.
All imports are idempotent.
"""

import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.connectors.acl import fetch_acl_papers
from app.connectors.semantic_scholar import (
    fetch_s2_paper_details,
    search_s2_by_title,
)
from app.core.bibtex import parse_author_string, parse_bibtex_file, parse_bibtex_string
from app.core.db import get_session, init_db
from app.core.service import upsert_item

logger = logging.getLogger(__name__)

# venue 正規化マップ: 長い名称 → 略名
# ACL BibTeX の booktitle や S2 の venue フィールドはバラつきが多いため統一する
_VENUE_NORMALIZE: list[tuple[str, str]] = [
    # ACL系 — Findings は親会場と同じ略名にする（トラックは tags で管理）
    (r"findings.*acl|acl.*findings", "ACL"),
    (r"findings.*emnlp|emnlp.*findings", "EMNLP"),
    (r"findings.*naacl|naacl.*findings", "NAACL"),
    (r"findings.*eacl|eacl.*findings", "EACL"),
    (r"findings.*coling|coling.*findings", "COLING"),
    (r"annual meeting.*association for computational linguistics", "ACL"),
    (r"empirical methods in natural language processing", "EMNLP"),
    (r"north american chapter.*association for computational linguistics", "NAACL"),
    (r"european chapter.*association for computational linguistics", "EACL"),
    (r"international conference on computational linguistics", "COLING"),
    (r"transactions of the association for computational linguistics", "TACL"),
    (r"acl[-– ]ijcnlp", "ACL"),
    (r"joint conference.*computational linguistics.*natural language processing", "EMNLP"),
    # ML系
    (r"international conference on learning representations", "ICLR"),
    (r"international conference on machine learning", "ICML"),
    (r"neural information processing systems|advances in neural information processing", "NeurIPS"),
    (r"aaai conference on artificial intelligence", "AAAI"),
    (r"international joint conference on artificial intelligence", "IJCAI"),
    (r"natural language processing and chinese computing", "NLPCC"),
]


def normalize_venue(raw: str) -> str:
    """長い会場名を略名に正規化する (e.g. 'Proceedings of the 60th ACL...' -> 'ACL')."""
    if not raw:
        return raw
    lower = raw.lower()
    for pattern, short in _VENUE_NORMALIZE:
        if re.search(pattern, lower):
            return short
    return raw


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
    tags: list[str] | None = None,
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
            tags=tags or [],
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
    tags: list[str] | None = None,
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
            tags=tags or [],
        )
        session.commit()
        item_id = item.id
        item_title = item.title
    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()

    return {"item_id": item_id, "created": created, "title": item_title}


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

    imported = 0
    skipped = 0

    try:
        for paper in papers:
            ext_ids = {}
            if paper.get("acl_id"):
                ext_ids["acl"] = paper["acl_id"]
            if paper.get("doi"):
                ext_ids["doi"] = paper["doi"]

            tags = [event.lower()]
            if paper.get("volume_type"):
                tags.append(f"{event.lower()}/{paper['volume_type']}")

            item, created = upsert_item(
                session,
                title=paper["title"],
                authors=paper.get("authors", []),
                year=paper.get("year"),
                venue=normalize_venue(paper.get("venue") or ""),
                venue_instance=paper.get("venue_instance"),
                abstract=paper.get("abstract"),
                source_url=paper.get("source_url"),
                bibtex_key=paper.get("bibtex_key"),
                bibtex_raw=paper.get("bibtex_raw"),
                external_ids=ext_ids if ext_ids else None,
                tags=tags,
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

    return {
        "event": event.upper(),
        "year": year,
        "volumes": volumes,
        "event_tag": event.lower(),
        "imported": imported,
        "skipped": skipped,
        "total": len(papers),
    }


def _title_score(query: str, candidate: str) -> float:
    """Score similarity between query title and a candidate title (0.0–1.0)."""

    def _normalize(s: str) -> str:
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return re.sub(r"[^\w\s]", "", s).lower().strip()

    q = _normalize(query)
    c = _normalize(candidate)
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.8
    q_words = set(q.split())
    c_words = set(c.split())
    if not q_words or not c_words:
        return 0.0
    overlap = len(q_words & c_words)
    return 0.7 * overlap / max(len(q_words), len(c_words))


def import_by_title(query: str, tags: list[str] | None = None, session: Session | None = None) -> dict[str, Any]:
    """Import a paper by title, resolving metadata via Semantic Scholar + arXiv.

    Returns: {"item_id": int, "created": bool, "title": str, "source": str}
    """
    own_session = session is None
    if own_session:
        init_db()
        session = get_session()

    try:
        # 1. Search S2 for candidates
        candidates = search_s2_by_title(query)

        # 2. Score and pick best match
        best = None
        best_score = 0.0
        for cand in candidates:
            cand_title = cand.get("title") or ""
            score = _title_score(query, cand_title)
            if score > best_score:
                best_score = score
                best = cand

        if best and best_score >= 0.8:
            ext_ids = best.get("externalIds") or {}
            arxiv_id = ext_ids.get("ArXiv")
            acl_id = ext_ids.get("ACL")  # ACL Anthology ID (e.g. "2022.emnlp-main.797")

            if acl_id:
                # 3a. ACL Anthology を優先: @inproceedings で venue/pages/doi などが揃う
                bib_url = f"https://aclanthology.org/{acl_id}.bib"
                try:
                    resp = requests.get(bib_url, timeout=15)
                    resp.raise_for_status()
                    entries = parse_bibtex_string(resp.text)
                except Exception as e:
                    logger.warning(f"Failed to fetch ACL BibTeX for {acl_id}: {e}")
                    entries = []

                if entries:
                    entry = entries[0]
                    etype = entry.get("ENTRYTYPE", "inproceedings")
                    title = re.sub(r"[{}]", "", entry.get("title", query).strip())
                    author_str = entry.get("author", "")
                    authors = parse_author_string(author_str) if author_str else []
                    year = None
                    if "year" in entry:
                        try:
                            year = int(entry["year"])
                        except (ValueError, TypeError):
                            pass
                    abstract = re.sub(r"[{}]", "", entry.get("abstract", "")).strip()
                    url = entry.get("url", f"https://aclanthology.org/{acl_id}")
                    bib_key = entry.get("ID", "")
                    venue = normalize_venue(re.sub(r"[{}]", "", entry.get("booktitle", entry.get("journal", ""))).strip()) or None

                    raw_lines = [f"@{etype}{{{bib_key},"]
                    for k, v in sorted(entry.items()):
                        if k in ("ENTRYTYPE", "ID"):
                            continue
                        raw_lines.append(f"  {k} = {{{v}}},")
                    raw_lines.append("}")
                    bibtex_raw = "\n".join(raw_lines)

                    ext_id_map: dict[str, str] = {"acl": acl_id}
                    if arxiv_id:
                        ext_id_map["arxiv"] = arxiv_id

                    item, created = upsert_item(
                        session,
                        title=title,
                        authors=authors,
                        year=year,
                        abstract=abstract,
                        venue=venue,
                        source_url=url,
                        bibtex_key=bib_key or None,
                        bibtex_raw=bibtex_raw,
                        external_ids=ext_id_map,
                        tags=tags or [],
                    )
                    session.commit()
                    item_id = item.id
                    item_title = item.title
                    return {"item_id": item_id, "created": created, "title": item_title, "source": "acl"}

            if arxiv_id:
                # 3b. ACL IDなし → arXiv BibTeX にフォールバック
                bib_url = f"https://arxiv.org/bibtex/{arxiv_id}"
                try:
                    resp = requests.get(bib_url, timeout=15)
                    resp.raise_for_status()
                    entries = parse_bibtex_string(resp.text)
                except Exception as e:
                    logger.warning(f"Failed to fetch arXiv BibTeX for {arxiv_id}: {e}")
                    entries = []

                if entries:
                    entry = entries[0]
                    etype = entry.get("ENTRYTYPE", "article")
                    title = re.sub(r"[{}]", "", entry.get("title", query).strip())
                    author_str = entry.get("author", "")
                    authors = parse_author_string(author_str) if author_str else []
                    year = None
                    if "year" in entry:
                        try:
                            year = int(entry["year"])
                        except (ValueError, TypeError):
                            pass
                    abstract = re.sub(r"[{}]", "", entry.get("abstract", "")).strip()
                    url = entry.get("url", f"https://arxiv.org/abs/{arxiv_id}")
                    bib_key = entry.get("ID", "")

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
                        abstract=abstract,
                        source_url=url,
                        bibtex_key=bib_key or None,
                        bibtex_raw=bibtex_raw,
                        external_ids={"arxiv": arxiv_id},
                        tags=tags or [],
                    )
                    session.commit()
                    item_id = item.id
                    item_title = item.title
                    return {"item_id": item_id, "created": created, "title": item_title, "source": "arxiv"}

            # 3b. No arXiv ID — fetch full S2 details
            paper_id = best.get("paperId", "")
            details = fetch_s2_paper_details(paper_id) if paper_id else None
            if details:
                title = details.get("title") or query
                year = details.get("year")
                abstract = details.get("abstract") or ""
                venue_obj = details.get("publicationVenue") or {}
                venue = normalize_venue(venue_obj.get("name") or details.get("venue") or "")
                authors_raw = details.get("authors") or []
                authors = [a.get("name", "") for a in authors_raw if a.get("name")]
                s2_ext = details.get("externalIds") or {}
                doi = s2_ext.get("DOI")
                external_ids = {"s2": paper_id}
                if doi:
                    external_ids["doi"] = doi
            else:
                title = best.get("title") or query
                year = best.get("year")
                abstract = ""
                venue = None
                authors_raw = best.get("authors") or []
                authors = [a.get("name", "") for a in authors_raw if a.get("name")]
                external_ids = {"s2": paper_id} if paper_id else None

            item, created = upsert_item(
                session,
                title=title,
                authors=authors,
                year=year,
                abstract=abstract,
                venue=venue,
                external_ids=external_ids,
                tags=tags or [],
            )
            session.commit()
            item_id = item.id
            item_title = item.title
            return {"item_id": item_id, "created": created, "title": item_title, "source": "s2"}

        # 4. No match — import as placeholder
        logger.warning(f"No Semantic Scholar match found for title: {query!r} (best score={best_score:.2f})")
        item, created = upsert_item(session, title=query, tags=tags or [])
        session.commit()
        item_id = item.id
        item_title = item.title
        return {"item_id": item_id, "created": created, "title": item_title, "source": "placeholder"}

    except Exception:
        session.rollback()
        raise
    finally:
        if own_session:
            session.close()


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

    # Title: title:Paper Title Here
    if spec.startswith("title:"):
        return {"type": "title", "args": {"query": spec[6:]}}

    raise ValueError(f"Unknown import spec: {spec}")
