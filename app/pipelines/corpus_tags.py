"""LLM Tag Normalization Pipeline.

Extracts method/task/dataset/metric tags from corpus items using LLM,
falls back to TF-IDF keywords when LLM is unavailable.

Saves:
  data/corpus/tag_cooccurrence.json  — co-occurrence matrix (tag pairs → count)
  data/corpus/tag_patterns.json      — {top_pairs: [...], gaps: [...]}
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Item, ItemTag, Tag

logger = logging.getLogger(__name__)

_TAG_KINDS = ("corpus_method", "corpus_task", "corpus_dataset", "corpus_metric")

_LLM_SYSTEM = (
    "You are an NLP expert. Extract structured tags from a paper abstract.\n"
    "Respond with ONLY a JSON object:\n"
    '{"methods": [...], "tasks": [...], "datasets": [...], "metrics": [...]}\n'
    "Each list contains short English strings (1-3 words). Max 4 items per list.\n"
    "If not mentioned, use an empty list."
)


def _corpus_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["base_dir"]) / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _llm_extract_tags(title: str, abstract: str) -> dict[str, list[str]]:
    """Use LLM to extract tags from title+abstract. Returns {methods, tasks, datasets, metrics}."""
    try:
        from app.gpu.llm import generate_single  # type: ignore

        prompt = f"Title: {title}\nAbstract: {abstract[:600]}"
        out = generate_single(prompt, system_prompt=_LLM_SYSTEM, max_new_tokens=200, temperature=0.1)
        m = re.search(r"\{.*?\}", out, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return {
                "methods": [str(x) for x in data.get("methods", [])[:4]],
                "tasks": [str(x) for x in data.get("tasks", [])[:4]],
                "datasets": [str(x) for x in data.get("datasets", [])[:4]],
                "metrics": [str(x) for x in data.get("metrics", [])[:4]],
            }
    except Exception as e:
        logger.debug(f"LLM tag extraction failed: {e}")
    return {}


def _tfidf_extract_tags(title: str, abstract: str) -> dict[str, list[str]]:
    """Fallback: extract keywords with simple TF-IDF-like heuristics."""
    _STOPWORDS = {
        "the",
        "a",
        "an",
        "in",
        "of",
        "for",
        "and",
        "or",
        "to",
        "is",
        "are",
        "with",
        "on",
        "we",
        "our",
        "that",
        "this",
        "from",
        "by",
        "be",
        "as",
        "it",
        "its",
        "at",
        "not",
        "can",
        "has",
        "have",
        "also",
        "which",
        "using",
        "based",
        "show",
        "propose",
        "proposed",
        "paper",
        "present",
        "model",
        "method",
        "approach",
        "result",
        "results",
        "performance",
        "large",
        "new",
        "pre",
        "trained",
    }
    text = (title + " " + abstract).lower()
    words = [w for w in re.findall(r"[a-z]{4,}", text) if w not in _STOPWORDS]
    freq = Counter(words)
    top = [w for w, _ in freq.most_common(8)]
    return {"methods": top[:2], "tasks": top[2:4], "datasets": top[4:6], "metrics": top[6:8]}


def _upsert_tag(session: Session, name: str, kind: str) -> Tag:
    """Get or create a tag with the given name and kind."""
    tag = session.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name, kind=kind)
        session.add(tag)
        session.flush()
    return tag


def _add_tag_to_item(session: Session, item: Item, tag: Tag) -> bool:
    """Add tag to item if not already present. Returns True if added."""
    exists = session.execute(
        select(ItemTag).where(ItemTag.item_id == item.id, ItemTag.tag_id == tag.id)
    ).scalar_one_or_none()
    if exists:
        return False
    session.add(ItemTag(item_id=item.id, tag_id=tag.id, source="auto"))
    return True


def normalize_tags(session: Session, rebuild: bool = False) -> dict:
    """Run tag normalization on all corpus items.

    Returns dict: {tagged, skipped, total, tag_count}.
    """
    items = session.execute(select(Item).where(Item.type == "corpus")).scalars().all()
    total = len(items)
    if total == 0:
        return {"tagged": 0, "skipped": 0, "total": 0, "tag_count": 0}

    # If rebuild=False, skip items that already have corpus tags
    existing_tagged: set[int] = set()
    if not rebuild:
        for item in items:
            has_tag = session.execute(
                select(ItemTag).join(Tag).where(ItemTag.item_id == item.id, Tag.kind.in_(_TAG_KINDS))
            ).first()
            if has_tag:
                existing_tagged.add(item.id)

    tagged = skipped = 0
    kind_map = {
        "methods": "corpus_method",
        "tasks": "corpus_task",
        "datasets": "corpus_dataset",
        "metrics": "corpus_metric",
    }

    for item in items:
        if item.id in existing_tagged:
            skipped += 1
            continue

        text = (item.title or "") + " " + (item.abstract or "")
        if not text.strip():
            skipped += 1
            continue

        # Try LLM first, fall back to TF-IDF
        extracted = _llm_extract_tags(item.title or "", item.abstract or "")
        if not any(extracted.values()):
            extracted = _tfidf_extract_tags(item.title or "", item.abstract or "")

        for field, kind in kind_map.items():
            for tag_name in extracted.get(field, []):
                tag_name = tag_name.strip().lower()
                if not tag_name:
                    continue
                full_name = f"{kind}:{tag_name}"
                tag = _upsert_tag(session, full_name, kind)
                _add_tag_to_item(session, item, tag)

        session.flush()
        tagged += 1

    session.commit()

    # Count tags across corpus
    tag_count = session.execute(select(Tag).where(Tag.kind.in_(_TAG_KINDS))).scalars().all()

    return {"tagged": tagged, "skipped": skipped, "total": total, "tag_count": len(tag_count)}


def compute_tag_patterns(session: Session) -> dict:
    """Compute tag co-occurrence and detect patterns/gaps.

    Writes:
      data/corpus/tag_cooccurrence.json
      data/corpus/tag_patterns.json

    Returns the patterns dict.
    """
    corpus_dir = _corpus_dir()

    # Collect per-item tag sets (corpus_* kinds only)
    items = session.execute(select(Item).where(Item.type == "corpus")).scalars().all()

    item_tags: dict[int, set[str]] = {}
    for item in items:
        tags = (
            session.execute(select(Tag.name).join(ItemTag).where(ItemTag.item_id == item.id, Tag.kind.in_(_TAG_KINDS)))
            .scalars()
            .all()
        )
        if tags:
            item_tags[item.id] = set(tags)

    # Co-occurrence: count pairs
    cooc: Counter = Counter()
    for tags_set in item_tags.values():
        tag_list = sorted(tags_set)
        for i, t1 in enumerate(tag_list):
            for t2 in tag_list[i + 1 :]:
                cooc[(t1, t2)] += 1

    # Serialize co-occurrence (keys as "A|||B")
    cooc_json = {f"{t1}|||{t2}": cnt for (t1, t2), cnt in cooc.items()}
    (corpus_dir / "tag_cooccurrence.json").write_text(
        json.dumps(cooc_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Top patterns (most frequent pairs)
    top_pairs = [{"tag1": t1, "tag2": t2, "count": cnt} for (t1, t2), cnt in cooc.most_common(20)]

    # Gaps: tags that appear individually but never co-occur with common tags
    # Simple heuristic: tag pairs with frequency=0 among frequent tags
    freq = Counter(t for tags in item_tags.values() for t in tags)
    top_tags = [t for t, _ in freq.most_common(15)]
    gaps = []
    for i, t1 in enumerate(top_tags):
        for t2 in top_tags[i + 1 :]:
            pair_key = tuple(sorted([t1, t2]))
            if cooc[pair_key] == 0:
                gaps.append({"tag1": t1, "tag2": t2, "freq1": freq[t1], "freq2": freq[t2]})

    patterns = {"top_pairs": top_pairs, "gaps": gaps[:20]}
    (corpus_dir / "tag_patterns.json").write_text(json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(f"Tag patterns: {len(top_pairs)} top pairs, {len(gaps)} gap candidates")
    return patterns
