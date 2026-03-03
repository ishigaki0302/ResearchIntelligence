"""Personalized Retrieval Pipeline.

Given a user profile (free text, BibTeX, or .txt file), embed it and
rank corpus papers by cosine similarity. Optionally generate LLM
explanations for why each top paper is similar.

Output: data/corpus/personalized_top30.json
Format:
[
  {
    "rank": 1,
    "item_id": 42,
    "title": "...",
    "abstract": "...",
    "score": 0.87,
    "reason": "..."   # LLM explanation (empty if LLM unavailable)
  },
  ...
]
"""

import json
import logging
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Item
from app.indexing.engine import embed_texts

logger = logging.getLogger(__name__)


def _corpus_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["base_dir"]) / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _top30_path() -> Path:
    return _corpus_dir() / "personalized_top30.json"


def _load_embeddings() -> tuple[list[int], np.ndarray]:
    """Load cached embeddings. Raises FileNotFoundError if missing."""
    emb_path = _corpus_dir() / "embeddings.npz"
    if not emb_path.exists():
        raise FileNotFoundError(f"Embeddings not found at {emb_path}. Run 'ri corpus embed' first.")
    data = np.load(emb_path, allow_pickle=False)
    return data["item_ids"].tolist(), data["vectors"]


def _read_profile(profile_input: str) -> str:
    """Return profile text from a file path or inline string."""
    p = Path(profile_input)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8", errors="ignore")
    return profile_input


def _llm_explain(profile_text: str, title: str, abstract: str) -> str:
    """Ask LLM why a paper is close to the profile. Returns explanation or ''."""
    try:
        from app.gpu.llm import generate_single  # type: ignore

        prompt = (
            "あなたは研究者のアシスタントです。\n"
            f"研究者のプロファイル:\n{profile_text[:400]}\n\n"
            f"論文タイトル: {title}\n"
            f"要旨: {abstract[:400]}\n\n"
            "この論文がプロファイルに近い理由を日本語で2〜3文で説明してください。"
        )
        return generate_single(prompt, max_new_tokens=150, temperature=0.2).strip()
    except Exception as e:
        logger.debug(f"LLM explanation skipped: {e}")
        return ""


def personalize(
    session: Session,
    profile: str,
    top_k: int = 30,
    explain: bool = True,
) -> list[dict]:
    """Rank corpus papers by similarity to the given profile.

    Args:
        profile: Free text, BibTeX string, or path to a .txt/.bib file.
        top_k: Number of top papers to return.
        explain: Whether to generate LLM explanations (skipped if LLM unavailable).

    Returns list of result dicts (also written to personalized_top30.json).
    """
    profile_text = _read_profile(profile)
    if not profile_text.strip():
        logger.warning("Empty profile — no results.")
        return []

    item_ids, vectors = _load_embeddings()
    n = len(item_ids)
    if n == 0:
        logger.warning("No corpus embeddings — nothing to rank.")
        return []

    # Embed profile
    profile_vec = embed_texts([profile_text])  # (1, dim)

    # Cosine similarity (vectors may not be L2-normalised here, do it explicitly)
    def _cosine(a: np.ndarray, B: np.ndarray) -> np.ndarray:
        a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
        B_norm = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-9)
        return (a_norm @ B_norm.T).squeeze()

    scores = np.atleast_1d(_cosine(profile_vec, vectors))  # ensure (n,) even for n=1
    top_indices = np.argsort(scores)[::-1][: min(top_k, n)]

    # Load item metadata
    all_items: dict[int, Item] = {
        it.id: it for it in session.execute(select(Item).where(Item.type == "corpus")).scalars().all()
    }

    results = []
    for rank, idx in enumerate(top_indices, start=1):
        iid = int(item_ids[idx])
        score = float(scores[idx])
        item = all_items.get(iid)
        if not item:
            continue

        reason = ""
        if explain:
            reason = _llm_explain(profile_text, item.title or "", item.abstract or "")

        results.append(
            {
                "rank": rank,
                "item_id": iid,
                "title": item.title or "",
                "abstract": (item.abstract or "")[:300],
                "score": round(score, 4),
                "reason": reason,
            }
        )

    _top30_path().write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Personalized Top{len(results)} written to {_top30_path()}")
    return results
