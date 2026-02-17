"""Semantic Scholar API connector for paper metadata enrichment."""

import hashlib
import json
import logging
from pathlib import Path

import requests

from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)

S2_API = "https://api.semanticscholar.org/graph/v1"
DEFAULT_FIELDS = "externalIds,title,year,authors"


def _cache_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["cache_raw_dir"]) / "semantic_scholar"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_headers() -> dict:
    cfg = get_config()
    api_key = cfg.get("external", {}).get("semantic_scholar", {}).get("api_key", "")
    headers = {"User-Agent": "ResearchIntelligence/0.4"}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _cached_get(url: str, params: dict | None = None) -> dict | None:
    """GET with file caching.  Cache key includes both URL and params."""
    key_src = url
    if params:
        key_src += "?" + json.dumps(params, sort_keys=True)
    cache_key = hashlib.md5(key_src.encode()).hexdigest()
    cache_file = _cache_dir() / f"{cache_key}.json"
    if cache_file.exists():
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data if data else None

    try:
        resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
        if resp.status_code == 404:
            cache_file.write_text("null", encoding="utf-8")
            return None
        if resp.status_code == 429:
            logger.warning("Semantic Scholar rate limit hit, waiting 3s...")
            import time
            time.sleep(3)
            resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except Exception as e:
        logger.warning(f"Semantic Scholar request failed: {e}")
        return None


def lookup_s2_by_doi(doi: str) -> dict | None:
    """Look up a paper by DOI on Semantic Scholar."""
    url = f"{S2_API}/paper/DOI:{doi}"
    return _cached_get(url, {"fields": DEFAULT_FIELDS})


def lookup_s2_by_arxiv(arxiv_id: str) -> dict | None:
    """Look up a paper by arXiv ID on Semantic Scholar."""
    url = f"{S2_API}/paper/ARXIV:{arxiv_id}"
    return _cached_get(url, {"fields": DEFAULT_FIELDS})


def search_s2_by_title(title: str) -> list[dict]:
    """Search Semantic Scholar by title (fallback)."""
    url = f"{S2_API}/paper/search"
    params = {"query": title, "limit": "5", "fields": DEFAULT_FIELDS}
    cache_key = hashlib.md5(f"search:{title}".encode()).hexdigest()
    cache_file = _cache_dir() / f"{cache_key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    try:
        resp = requests.get(url, params=params, headers=_get_headers(), timeout=30)
        resp.raise_for_status()
        results = resp.json().get("data", [])
        cache_file.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
        return results
    except Exception as e:
        logger.warning(f"Semantic Scholar search failed: {e}")
        return []


def _fetch_references(paper_id: str) -> list[dict] | None:
    """Fetch references for a single paper_id. Returns None on miss."""
    url = f"{S2_API}/paper/{paper_id}/references"
    params = {"fields": "externalIds,title", "limit": "1000"}
    data = _cached_get(url, params)
    if not data:
        return None
    refs = []
    for entry in data.get("data", []):
        cited = entry.get("citedPaper")
        if cited and cited.get("paperId"):
            refs.append(cited)
    return refs if refs else None


def get_references(paper_id: str, alt_ids: list[str] | None = None, title: str | None = None) -> list[dict]:
    """Get references for a paper from Semantic Scholar.

    Tries paper_id first, then alt_ids, then falls back to title search
    to resolve the S2 paperId.

    Args:
        paper_id: Primary S2 paper identifier (e.g. "DOI:10.xxx", "ARXIV:2401.xxx")
        alt_ids: Alternative identifiers to try if primary fails
        title: Paper title for fallback search

    Returns:
        List of referenced paper dicts with externalIds and title.
    """
    # Try primary
    refs = _fetch_references(paper_id)
    if refs:
        return refs

    # Try alternative IDs
    for alt in alt_ids or []:
        refs = _fetch_references(alt)
        if refs:
            return refs

    # Fallback: search by title to find paperId, then fetch references
    if title:
        results = search_s2_by_title(title)
        for r in results:
            pid = r.get("paperId")
            if pid:
                refs = _fetch_references(pid)
                if refs:
                    return refs

    return []


def extract_ids_from_s2(paper: dict) -> dict[str, str]:
    """Extract external IDs from a Semantic Scholar paper object."""
    ids = {}
    ext = paper.get("externalIds", {})
    if ext.get("DOI"):
        ids["doi"] = ext["DOI"]
    if ext.get("ArXiv"):
        ids["arxiv"] = ext["ArXiv"]
    if ext.get("CorpusId"):
        ids["s2"] = str(ext["CorpusId"])
    paper_id = paper.get("paperId")
    if paper_id and "s2" not in ids:
        ids["s2"] = paper_id
    return ids
