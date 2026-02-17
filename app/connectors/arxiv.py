"""arXiv API connector for paper discovery.

Uses the arXiv Atom feed API: http://export.arxiv.org/api/query
"""

import hashlib
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _cache_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["cache_raw_dir"]) / "watch" / "arxiv"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_query(keyword: str, category: str | None = None, since_days: int | None = None) -> str:
    """Build an arXiv search query string."""
    parts = []
    if keyword:
        parts.append(f"all:{keyword}")
    if category:
        parts.append(f"cat:{category}")
    query = " AND ".join(parts) if parts else keyword
    return query


def search_arxiv(
    keyword: str,
    category: str | None = None,
    since_days: int | None = None,
    max_results: int = 100,
    sleep_sec: float = 3.0,
) -> list[dict]:
    """Search arXiv for papers matching the query.

    Returns list of normalized dicts.
    """
    query = _build_query(keyword, category)
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    # Check cache
    cache_key = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()
    cache_file = _cache_dir() / f"{cache_key}.xml"

    if cache_file.exists():
        logger.info(f"Using cached arXiv results: {cache_file.name}")
        xml_text = cache_file.read_text(encoding="utf-8")
    else:
        logger.info(f"Querying arXiv: {query}")
        time.sleep(sleep_sec)
        resp = requests.get(ARXIV_API, params=params, timeout=60)
        resp.raise_for_status()
        xml_text = resp.text
        cache_file.write_text(xml_text, encoding="utf-8")

    return _parse_atom_feed(xml_text, since_days=since_days)


def _parse_atom_feed(xml_text: str, since_days: int | None = None) -> list[dict]:
    """Parse arXiv Atom XML into normalized paper dicts."""
    root = ET.fromstring(xml_text)
    results = []

    cutoff = None
    if since_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)

    for entry in root.findall(f"{ATOM_NS}entry"):
        # Extract arxiv ID from the id URL
        id_url = entry.findtext(f"{ATOM_NS}id", "")
        arxiv_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else ""
        if not arxiv_id:
            continue

        published_str = entry.findtext(f"{ATOM_NS}published", "")
        published_dt = None
        if published_str:
            try:
                published_dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            except ValueError:
                pass

        # Apply date filter
        if cutoff and published_dt and published_dt < cutoff:
            continue

        title = entry.findtext(f"{ATOM_NS}title", "").strip().replace("\n", " ")

        # Authors
        authors = []
        for author_el in entry.findall(f"{ATOM_NS}author"):
            name = author_el.findtext(f"{ATOM_NS}name", "").strip()
            if name:
                authors.append(name)

        abstract = entry.findtext(f"{ATOM_NS}summary", "").strip().replace("\n", " ")

        # Year from published date
        year = published_dt.year if published_dt else None

        # PDF link
        pdf_url = ""
        for link in entry.findall(f"{ATOM_NS}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
                break

        # Categories
        categories = []
        for cat in entry.findall(f"{ARXIV_NS}primary_category"):
            term = cat.get("term", "")
            if term:
                categories.append(term)

        results.append(
            {
                "source_id_type": "arxiv",
                "source_id_value": arxiv_id,
                "title": title,
                "authors": authors,
                "year": year,
                "venue": "arXiv",
                "url": id_url,
                "pdf_url": pdf_url,
                "abstract": abstract,
                "published": published_str,
                "categories": categories,
            }
        )

    return results
