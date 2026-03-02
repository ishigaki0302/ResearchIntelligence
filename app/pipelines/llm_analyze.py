"""LLM-based analysis pipeline for paper collection.

GPU-optional: all functions gracefully skip when GPU/LLM is unavailable.
Covers Issues #47 (TLDR generation) and #48 (entity extraction).

CLI usage:
    ri llm-analyze tldr --venue NLP2026
    ri llm-analyze extract-entities --venue NLP2026
    ri llm-analyze all --venue NLP2026
"""

import json
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Item, Tag, ItemTag
from app.core.service import get_or_create_tag
from app.gpu import is_gpu_available

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────

_SYSTEM_TLDR = (
    "あなたは NLP 研究者です。論文タイトルと利用可能な情報から、"
    "研究内容を 1〜2 文の日本語で簡潔に要約してください。"
    "専門用語はそのまま使用し、余計な前置きは不要です。"
)

_SYSTEM_ENTITIES = (
    "あなたは NLP 研究の専門家です。論文情報から構造化エンティティを抽出し、"
    "必ず以下の JSON 形式のみで回答してください（説明文不要）:\n"
    '{"tasks": [...], "methods": [...], "datasets": [...], "metrics": [...], "models": [...]}'
)


def _build_tldr_prompt(item: Item) -> str:
    parts = [f"タイトル: {item.title}"]
    if item.abstract:
        parts.append(f"アブストラクト: {item.abstract[:1000]}")
    elif item.tldr:
        parts.append(f"既存要約: {item.tldr}")

    # Read extracted text if available
    if item.text_path:
        from app.core.config import resolve_path
        tp = resolve_path(item.text_path)
        if tp.exists():
            text = tp.read_text(encoding="utf-8")[:2000]
            parts.append(f"本文抜粋: {text}")

    parts.append("\n上記の論文を 1〜2 文の日本語で要約してください。")
    return "\n\n".join(parts)


def _build_entity_prompt(item: Item) -> str:
    parts = [f"タイトル: {item.title}"]
    if item.abstract:
        parts.append(f"アブストラクト: {item.abstract[:1500]}")
    if item.tldr:
        parts.append(f"要約: {item.tldr}")
    if item.text_path:
        from app.core.config import resolve_path
        tp = resolve_path(item.text_path)
        if tp.exists():
            parts.append(f"本文抜粋: {tp.read_text(encoding='utf-8')[:1500]}")

    parts.append(
        "\nこの論文から以下を抽出し JSON で返してください:\n"
        "- tasks: NLP タスク名（例: 機械翻訳, 文書要約, QA）\n"
        "- methods: 提案手法・アーキテクチャ名（例: RAG, LoRA, DPO）\n"
        "- datasets: 使用データセット名\n"
        "- metrics: 評価指標（例: BLEU, F1, Accuracy）\n"
        "- models: ベースモデル名（例: GPT-4, LLaMA-3, Qwen2.5）\n"
        "各リストは空でも可。リストの要素は簡潔な名称のみ（説明不要）。"
    )
    return "\n\n".join(parts)


# ── TLDR Generation ───────────────────────────────────────────────────────

def generate_tldr_batch(
    session: Session,
    venue_instance: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
    overwrite: bool = False,
    batch_size: int = 32,
) -> dict:
    """Generate TLDR for items using LLM.

    Args:
        session: DB session
        venue_instance: filter by venue_instance (e.g. "NLP2026")
        item_ids: specific item IDs to process
        overwrite: overwrite existing TLDRs
        batch_size: number of prompts per LLM batch

    Returns: {"processed": int, "skipped": int, "failed": int, "gpu_available": bool}
    """
    from app.gpu.llm import generate, get_backend

    gpu_ok = is_gpu_available()
    backend = get_backend()
    if backend == "none":
        logger.warning("LLM backend not available — TLDR generation skipped")
        return {"processed": 0, "skipped": 0, "failed": 0, "gpu_available": gpu_ok}

    # Fetch items
    query = select(Item).where(Item.status == "active")
    if venue_instance:
        query = query.where(Item.venue_instance == venue_instance)
    if item_ids:
        query = query.where(Item.id.in_(item_ids))
    if not overwrite:
        query = query.where(Item.tldr.is_(None))

    items = session.execute(query).scalars().all()
    logger.info(f"Generating TLDRs for {len(items)} items")

    processed = failed = 0

    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        prompts = [_build_tldr_prompt(it) for it in chunk]

        try:
            results = generate(prompts, system_prompt=_SYSTEM_TLDR, max_new_tokens=256, temperature=0.1)
        except Exception as e:
            logger.error(f"Batch generation error: {e}")
            failed += len(chunk)
            continue

        for item, tldr in zip(chunk, results):
            if tldr:
                item.tldr = tldr[:500]
                processed += 1
            else:
                failed += 1

        session.flush()
        session.commit()
        logger.info(f"  TLDR progress: {i + len(chunk)}/{len(items)}")

    return {"processed": processed, "skipped": len(items) - processed - failed,
            "failed": failed, "gpu_available": gpu_ok}


# ── Entity Extraction ─────────────────────────────────────────────────────

_ENTITY_CATEGORIES = {
    "tasks": "topic",
    "methods": "topic",
    "datasets": "topic",
    "metrics": "topic",
    "models": "topic",
}

_CATEGORY_PREFIX = {
    "tasks": "task/",
    "methods": "method/",
    "datasets": "dataset/",
    "metrics": "metric/",
    "models": "model/",
}


def extract_entities_batch(
    session: Session,
    venue_instance: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
    batch_size: int = 4,
) -> dict:
    """Extract NLP entities from papers and save as tags.

    Returns: {"processed": int, "tags_added": int, "failed": int}
    """
    from app.gpu.llm import generate, get_backend

    backend = get_backend()
    if backend == "none":
        logger.warning("LLM backend not available — entity extraction skipped")
        return {"processed": 0, "tags_added": 0, "failed": 0, "gpu_available": is_gpu_available()}

    query = select(Item).where(Item.status == "active")
    if venue_instance:
        query = query.where(Item.venue_instance == venue_instance)
    if item_ids:
        query = query.where(Item.id.in_(item_ids))

    items = session.execute(query).scalars().all()
    logger.info(f"Extracting entities for {len(items)} items")

    processed = failed = tags_added = 0

    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        prompts = [_build_entity_prompt(it) for it in chunk]

        try:
            results = generate(prompts, system_prompt=_SYSTEM_ENTITIES, max_new_tokens=256, temperature=0.0)
        except Exception as e:
            logger.error(f"Entity extraction batch error: {e}")
            failed += len(chunk)
            continue

        for item, raw in zip(chunk, results):
            if not raw:
                failed += 1
                continue
            try:
                data = json.loads(raw)
                for category, prefix in _CATEGORY_PREFIX.items():
                    for entity in data.get(category, []):
                        if not entity or not isinstance(entity, str):
                            continue
                        tag_name = prefix + entity.strip()[:100]
                        tag = get_or_create_tag(session, tag_name, kind="topic")
                        exists = session.execute(
                            select(ItemTag).where(ItemTag.item_id == item.id, ItemTag.tag_id == tag.id)
                        ).scalar_one_or_none()
                        if not exists:
                            session.add(ItemTag(item_id=item.id, tag_id=tag.id, source="llm"))
                            tags_added += 1
                processed += 1
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.debug(f"Entity parse error for item {item.id}: {e} | raw={raw[:100]}")
                failed += 1

        session.commit()
        logger.info(f"  Entity progress: {i + len(chunk)}/{len(items)}")

    return {"processed": processed, "tags_added": tags_added, "failed": failed,
            "gpu_available": is_gpu_available()}


# ── Combined Pipeline ─────────────────────────────────────────────────────

def run_full_analysis(
    session: Session,
    venue_instance: Optional[str] = None,
    item_ids: Optional[list[int]] = None,
    do_tldr: bool = True,
    do_entities: bool = True,
    overwrite_tldr: bool = False,
) -> dict:
    """Run all LLM analyses in sequence."""
    result = {}
    if do_tldr:
        result["tldr"] = generate_tldr_batch(
            session, venue_instance=venue_instance, item_ids=item_ids, overwrite=overwrite_tldr
        )
    if do_entities:
        result["entities"] = extract_entities_batch(
            session, venue_instance=venue_instance, item_ids=item_ids
        )
    return result
