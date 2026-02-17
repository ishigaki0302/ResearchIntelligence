"""ACL Anthology connector.

Fetches papers from the ACL Anthology for a given event and year.
Strategy:
1. Try the BibTeX export endpoint first (most stable).
2. Fall back to HTML scraping if needed.
Caches raw data under data/cache/raw/acl/.
"""

import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from app.core.bibtex import parse_author_string, parse_bibtex_string
from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)

# ACL Anthology volume ID patterns:
# ACL 2024 main → 2024.acl-long, 2024.acl-short
# ACL 2024 findings → 2024.findings-acl
# Format: {year}.{prefix}
VOLUME_MAP = {
    # (event, volume_type) → list of volume ID prefixes
    "main": ["acl-long", "acl-short"],
    "findings": ["findings-acl"],
    "demo": ["acl-demo"],
    "srw": ["acl-srw"],
    "tutorials": ["acl-tutorials"],
}

# Broader venue map for non-ACL events
VENUE_PREFIXES = {
    "acl": VOLUME_MAP,
    "emnlp": {
        "main": ["emnlp-main"],
        "findings": ["findings-emnlp"],
        "demo": ["emnlp-demo"],
        "srw": ["emnlp-srw"],
    },
    "naacl": {
        "main": ["naacl-long", "naacl-short"],
        "findings": ["findings-naacl"],
        "demo": ["naacl-demo"],
        "srw": ["naacl-srw"],
    },
    "eacl": {
        "main": ["eacl-long", "eacl-short"],
        "findings": ["findings-eacl"],
        "demo": ["eacl-demo"],
        "srw": ["eacl-srw"],
    },
    "coling": {
        "main": ["coling-main"],
    },
}

# Year-dependent venue prefixes (prefix changed over time)
def _get_venue_prefixes(event: str, year: int) -> dict[str, list[str]]:
    """Get volume prefix map for a venue, considering year-dependent changes."""
    event_lower = event.lower()

    # IJCNLP / AACL-IJCNLP: prefix changed from aacl-* (2020-2022) to ijcnlp-* (2023+)
    # Also "main" became "long" in 2025+
    if event_lower in ("ijcnlp", "aacl", "aacl-ijcnlp"):
        if year <= 2022:
            return {
                "main": ["aacl-main", "aacl-short"],
                "findings": ["findings-aacl"],
                "demo": ["aacl-demo"],
                "srw": ["aacl-srw"],
            }
        elif year <= 2024:
            return {
                "main": ["ijcnlp-main", "ijcnlp-short"],
                "findings": ["findings-ijcnlp"],
                "demo": ["ijcnlp-demo"],
                "srw": ["ijcnlp-srw"],
            }
        else:  # 2025+
            return {
                "main": ["ijcnlp-long", "ijcnlp-short"],
                "findings": ["findings-ijcnlp"],
                "demo": ["ijcnlp-demo"],
                "srw": ["ijcnlp-srw"],
            }

    return VENUE_PREFIXES.get(event_lower, VOLUME_MAP)

ACL_ANTHOLOGY_BASE = "https://aclanthology.org"


def _cache_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["cache_raw_dir"]) / "acl"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _volume_ids(event: str, year: int, volumes: list[str] | None = None) -> list[str]:
    """Build ACL Anthology volume IDs for the given event/year/volumes."""
    vol_map = _get_venue_prefixes(event, year)

    if volumes is None:
        volumes = list(vol_map.keys())

    result = []
    for vol in volumes:
        prefixes = vol_map.get(vol, [vol])
        for prefix in prefixes:
            result.append(f"{year}.{prefix}")
    return result


def fetch_volume_bibtex(volume_id: str, delay: float = 1.0) -> str:
    """Fetch the BibTeX export for an entire volume.

    Uses the endpoint: https://aclanthology.org/volumes/{volume_id}
    BibTeX link: https://aclanthology.org/{volume_id}.bib
    """
    cache_file = _cache_dir() / f"{volume_id}.bib"
    if cache_file.exists():
        logger.info(f"Using cached BibTeX for {volume_id}")
        return cache_file.read_text(encoding="utf-8")

    url = f"{ACL_ANTHOLOGY_BASE}/volumes/{volume_id}.bib"
    logger.info(f"Fetching BibTeX from {url}")
    time.sleep(delay)  # Be polite
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    bib_text = resp.text
    cache_file.write_text(bib_text, encoding="utf-8")
    return bib_text


def parse_acl_entries(bib_text: str, event: str, year: int, volume_type: str) -> list[dict[str, Any]]:
    """Parse BibTeX entries from an ACL volume and normalize into our format."""
    raw_entries = parse_bibtex_string(bib_text)
    results = []
    for entry in raw_entries:
        # Skip proceedings-level entries
        etype = entry.get("ENTRYTYPE", "")
        if etype == "proceedings":
            continue

        title = entry.get("title", "").strip()
        if not title:
            continue

        # Clean up LaTeX artifacts in title
        title = re.sub(r"[{}]", "", title)

        author_str = entry.get("author", "")
        authors = parse_author_string(author_str) if author_str else []

        entry_year = entry.get("year", str(year))
        try:
            entry_year = int(entry_year)
        except (ValueError, TypeError):
            entry_year = year

        # ACL Anthology ID from the key
        acl_id = entry.get("ID", "")

        # URLs
        url = entry.get("url", "")
        pdf_url = url.rstrip("/") + ".pdf" if url else ""

        abstract = entry.get("abstract", "")
        if abstract:
            abstract = re.sub(r"[{}]", "", abstract).strip()

        # DOI
        doi = entry.get("doi", "")

        venue_instance = f"{event.upper()} {entry_year}"

        results.append(
            {
                "title": title,
                "authors": authors,
                "year": entry_year,
                "venue": event.upper(),
                "venue_instance": venue_instance,
                "abstract": abstract,
                "source_url": url,
                "pdf_url": pdf_url,
                "acl_id": acl_id,
                "doi": doi,
                "bibtex_key": acl_id,
                "bibtex_raw": _entry_to_bib_str(entry),
                "volume_type": volume_type,
            }
        )

    return results


def _entry_to_bib_str(entry: dict) -> str:
    """Re-serialize a parsed BibTeX entry."""
    etype = entry.get("ENTRYTYPE", "inproceedings")
    key = entry.get("ID", "unknown")
    lines = [f"@{etype}{{{key},"]
    skip = {"ENTRYTYPE", "ID"}
    for k, v in sorted(entry.items()):
        if k in skip:
            continue
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


def fetch_acl_papers(
    event: str, year: int, volumes: list[str] | None = None, delay: float = 1.0
) -> list[dict[str, Any]]:
    """Fetch all papers for the given ACL event/year/volumes.

    Args:
        event: Event name (e.g. "acl", "emnlp")
        year: Year (e.g. 2024)
        volumes: Volume types to fetch (e.g. ["main", "findings"]). None = all.
        delay: Seconds to wait between HTTP requests.

    Returns:
        List of normalized paper dicts ready for upsert_item().
    """
    vol_ids = _volume_ids(event, year, volumes)
    all_papers = []

    for vol_id in vol_ids:
        # Determine volume type for tagging
        vol_type = "unknown"
        for vt, prefixes in _get_venue_prefixes(event, year).items():
            for p in prefixes:
                if vol_id.endswith(p):
                    vol_type = vt
                    break

        try:
            bib_text = fetch_volume_bibtex(vol_id, delay=delay)
            papers = parse_acl_entries(bib_text, event, year, vol_type)
            logger.info(f"  Volume {vol_id}: {len(papers)} papers")
            all_papers.extend(papers)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"  Volume {vol_id}: not found (404), skipping")
            else:
                logger.error(f"  Volume {vol_id}: HTTP error {e}")
        except Exception as e:
            logger.error(f"  Volume {vol_id}: error {e}")

    return all_papers
