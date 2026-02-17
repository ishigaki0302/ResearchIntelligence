"""OpenAlex API connector for paper metadata enrichment."""

import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path

import requests

from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)

OPENALEX_API = "https://api.openalex.org"
DEFAULT_USER_AGENT = "ResearchIntelligence/0.4"


def _cache_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["cache_raw_dir"]) / "openalex"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(params: dict) -> str:
    raw = json.dumps(params, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _normalize_title(title: str) -> str:
    """Normalize title for comparison: lowercase, strip punctuation, collapse whitespace."""
    title = unicodedata.normalize("NFKD", title).lower()
    title = re.sub(r"[^\w\s]", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def search_openalex(title: str, year: int | None = None, first_author: str | None = None) -> list[dict]:
    """Search OpenAlex for works matching the given title.

    Returns list of work dicts from the API.
    """
    params = {"filter": f"title.search:{title}", "per_page": "5"}
    if year:
        params["filter"] += f",publication_year:{year}"

    cfg = get_config()
    email = cfg.get("external", {}).get("openalex", {}).get("email", "")
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if email:
        headers["User-Agent"] = f"{DEFAULT_USER_AGENT} (mailto:{email})"

    cache_file = _cache_dir() / f"{_cache_key(params)}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    try:
        resp = requests.get(
            f"{OPENALEX_API}/works",
            params=params,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        cache_file.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        return results
    except Exception as e:
        logger.warning(f"OpenAlex search failed: {e}")
        return []


def search_openalex_works(
    query: str,
    since_days: int | None = None,
    per_page: int = 100,
    sleep_sec: float = 1.0,
) -> list[dict]:
    """Search OpenAlex for works by keyword, optionally filtered by date.

    Returns list of normalized dicts for inbox ingestion.
    """
    import time
    from datetime import datetime, timedelta

    filter_parts = [f"default.search:{query}"]
    if since_days:
        from_date = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        filter_parts.append(f"from_publication_date:{from_date}")

    params = {
        "filter": ",".join(filter_parts),
        "per_page": str(per_page),
        "sort": "publication_date:desc",
    }

    cfg = get_config()
    email = cfg.get("external", {}).get("openalex", {}).get("email", "")
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if email:
        headers["User-Agent"] = f"{DEFAULT_USER_AGENT} (mailto:{email})"

    cache_file = _cache_dir() / f"search_{_cache_key(params)}.json"
    if cache_file.exists():
        results = json.loads(cache_file.read_text(encoding="utf-8"))
    else:
        time.sleep(sleep_sec)
        try:
            resp = requests.get(
                f"{OPENALEX_API}/works",
                params=params,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            cache_file.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning(f"OpenAlex search failed: {e}")
            return []

    # Normalize to inbox format
    normalized = []
    for work in results:
        title = work.get("title", "")
        if not title:
            continue

        authors = []
        for auth in work.get("authorships", []):
            name = auth.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)

        year = work.get("publication_year")
        doi = work.get("doi", "")
        oa_id = work.get("id", "").replace("https://openalex.org/", "")

        # Determine source_id
        source_id_type = "openalex"
        source_id_value = oa_id
        if doi:
            source_id_type = "doi"
            source_id_value = doi.replace("https://doi.org/", "")

        # Venue
        venue = ""
        primary_location = work.get("primary_location", {}) or {}
        source = primary_location.get("source", {}) or {}
        if source:
            venue = source.get("display_name", "")

        # Abstract
        abstract = ""
        abstract_index = work.get("abstract_inverted_index")
        if abstract_index:
            # Reconstruct abstract from inverted index
            word_positions = []
            for word, positions in abstract_index.items():
                for pos in positions:
                    word_positions.append((pos, word))
            word_positions.sort()
            abstract = " ".join(w for _, w in word_positions)

        normalized.append(
            {
                "source_id_type": source_id_type,
                "source_id_value": source_id_value,
                "title": title,
                "authors": authors,
                "year": year,
                "venue": venue,
                "url": work.get("id", ""),
                "abstract": abstract,
            }
        )

    return normalized


def score_match(candidate: dict, title: str, year: int | None = None, authors: list[str] | None = None) -> float:
    """Score how well an OpenAlex candidate matches the query.

    Returns 0.0 to 1.0.
    """
    score = 0.0

    # Title similarity (simple normalized comparison)
    cand_title = candidate.get("title", "")
    if cand_title:
        norm_cand = _normalize_title(cand_title)
        norm_query = _normalize_title(title)
        if norm_cand == norm_query:
            score += 0.6
        elif norm_query in norm_cand or norm_cand in norm_query:
            score += 0.4
        else:
            # Word overlap
            cand_words = set(norm_cand.split())
            query_words = set(norm_query.split())
            if query_words:
                overlap = len(cand_words & query_words) / len(query_words)
                score += 0.5 * overlap

    # Year match
    cand_year = candidate.get("publication_year")
    if year and cand_year and cand_year == year:
        score += 0.2

    # Author overlap
    if authors:
        cand_authors = []
        for auth in candidate.get("authorships", []):
            name = auth.get("author", {}).get("display_name", "")
            if name:
                cand_authors.append(name.lower())
        if cand_authors:
            query_authors = [a.lower() for a in authors]
            # Check first author match
            if query_authors and cand_authors:
                if query_authors[0].split()[-1] in cand_authors[0]:
                    score += 0.2

    return min(score, 1.0)


def extract_ids_from_openalex(work: dict) -> dict[str, str]:
    """Extract external IDs from an OpenAlex work object."""
    ids = {}

    # OpenAlex ID
    oa_id = work.get("id", "")
    if oa_id:
        ids["openalex"] = oa_id.replace("https://openalex.org/", "")

    # DOI
    doi = work.get("doi", "")
    if doi:
        ids["doi"] = doi.replace("https://doi.org/", "")

    # Biblio IDs
    ext_ids = work.get("ids", {})
    if "openalex" in ext_ids:
        ids["openalex"] = ext_ids["openalex"].replace("https://openalex.org/", "")
    if "doi" in ext_ids:
        ids["doi"] = ext_ids["doi"].replace("https://doi.org/", "")

    return ids
