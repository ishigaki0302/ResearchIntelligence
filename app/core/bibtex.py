"""BibTeX parsing and generation utilities."""

import re
import unicodedata
from pathlib import Path
from typing import Any

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode


def parse_bibtex_string(bib_str: str) -> list[dict[str, Any]]:
    """Parse a BibTeX string and return list of entry dicts."""
    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    bib_db = bibtexparser.loads(bib_str, parser=parser)
    return bib_db.entries


def parse_bibtex_file(path: str | Path) -> list[dict[str, Any]]:
    """Parse a .bib file and return list of entry dicts."""
    with open(path, encoding="utf-8") as f:
        return parse_bibtex_string(f.read())


def normalize_name(name: str) -> str:
    """Normalize an author name for dedup: lowercase, strip accents, collapse whitespace."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"\s+", " ", name)
    return name


def parse_author_string(author_str: str) -> list[str]:
    """Split a BibTeX author string ('A and B and C') into individual names."""
    names = re.split(r"\s+and\s+", author_str.strip())
    result = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        # Handle "Last, First" format → "First Last"
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            if len(parts) == 2:
                name = f"{parts[1]} {parts[0]}"
        result.append(name.strip())
    return result


def generate_bibtex_key(
    authors: list[str], year: int | str | None, title: str, existing_keys: set[str] | None = None
) -> str:
    """Generate a stable BibTeX key like 'smith2024longcontext'.

    Format: {first_author_last_name}{year}{first_content_word_of_title}
    Adds suffix -a, -b, ... on collision.
    """
    # First author last name
    if authors:
        first = authors[0]
        parts = first.strip().split()
        last_name = parts[-1] if parts else "unknown"
        last_name = normalize_name(last_name)
        last_name = re.sub(r"[^a-z]", "", last_name)
    else:
        last_name = "unknown"

    year_str = str(year) if year else ""

    # First meaningful word from title
    stop_words = {"a", "an", "the", "on", "in", "of", "for", "and", "to", "with", "by", "is", "are", "at", "from"}
    title_words = re.findall(r"[a-z]+", title.lower())
    title_word = ""
    for w in title_words:
        if w not in stop_words and len(w) > 1:
            title_word = w
            break

    base_key = f"{last_name}{year_str}{title_word}"
    if not base_key:
        base_key = "entry"

    if existing_keys is None:
        return base_key

    if base_key not in existing_keys:
        return base_key

    # Collision resolution
    for suffix_ord in range(ord("a"), ord("z") + 1):
        candidate = f"{base_key}{chr(suffix_ord)}"
        if candidate not in existing_keys:
            return candidate

    return f"{base_key}_{id(title) % 10000}"


def entry_to_bibtex(entry: dict[str, Any]) -> str:
    """Convert a dict entry back to a BibTeX string."""
    entry_type = entry.get("ENTRYTYPE", "inproceedings")
    key = entry.get("ID", "unknown")
    lines = [f"@{entry_type}{{{key},"]
    skip = {"ENTRYTYPE", "ID"}
    for k, v in sorted(entry.items()):
        if k in skip:
            continue
        v_str = str(v).strip()
        if v_str:
            lines.append(f"  {k} = {{{v_str}}},")
    lines.append("}")
    return "\n".join(lines)


def item_to_bibtex_entry(
    bibtex_key: str,
    title: str,
    authors: list[str],
    year: int | str | None,
    venue: str | None = None,
    url: str | None = None,
    abstract: str | None = None,
    entry_type: str = "inproceedings",
    extra: dict | None = None,
) -> str:
    """Build a BibTeX entry string from structured fields."""
    lines = [f"@{entry_type}{{{bibtex_key},"]
    lines.append(f"  title = {{{title}}},")
    if authors:
        lines.append(f"  author = {{{' and '.join(authors)}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if venue:
        lines.append(f"  booktitle = {{{venue}}},")
    if url:
        lines.append(f"  url = {{{url}}},")
    if abstract:
        # Escape braces in abstract
        clean_abs = abstract.replace("{", "\\{").replace("}", "\\}")
        lines.append(f"  abstract = {{{clean_abs}}},")
    if extra:
        for k, v in extra.items():
            lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)
