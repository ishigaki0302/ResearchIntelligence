"""Corpus Embedding + UMAP 2D Projection Pipeline.

Embeds all corpus items (type='corpus') using sentence-transformers,
saves vectors to data/corpus/embeddings.npz, and runs UMAP to produce
2D coordinates saved to data/corpus/umap2d.json.

Idempotent: without --rebuild, existing cached vectors are reused.
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
    """Return (and create) the corpus data directory."""
    cfg = get_config()
    base = resolve_path(cfg["storage"]["base_dir"])
    d = base / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _embeddings_path() -> Path:
    return _corpus_dir() / "embeddings.npz"


def _umap_path() -> Path:
    return _corpus_dir() / "umap2d.json"


def _text_for_item(item: Item) -> str:
    """Return the text to embed for an item (title + abstract + first 500 chars of fulltext)."""
    parts = [item.title or ""]
    if item.abstract:
        parts.append(item.abstract)
    if item.text_path:
        text_file = resolve_path(item.text_path)
        if text_file.exists():
            body = text_file.read_text(encoding="utf-8", errors="ignore")[:500]
            parts.append(body)
    return " ".join(parts).strip()


def embed_corpus(session: Session, rebuild: bool = False) -> dict:
    """Embed all corpus items and save to embeddings.npz.

    Args:
        rebuild: If True, re-embed everything even if cache exists.

    Returns dict with keys: total, embedded, cached, dim.
    """
    emb_path = _embeddings_path()

    # Load existing cache
    cached_ids: list[int] = []
    cached_vecs: np.ndarray | None = None
    if emb_path.exists() and not rebuild:
        data = np.load(emb_path, allow_pickle=False)
        cached_ids = data["item_ids"].tolist()
        cached_vecs = data["vectors"]
        logger.info(f"Loaded {len(cached_ids)} cached embeddings from {emb_path}")

    # Query corpus items
    items = session.execute(select(Item).where(Item.type == "corpus")).scalars().all()
    total = len(items)
    if total == 0:
        logger.info("No corpus items found — nothing to embed.")
        return {"total": 0, "embedded": 0, "cached": 0, "dim": 0}

    cached_id_set = set(cached_ids)
    new_items = [it for it in items if it.id not in cached_id_set] if not rebuild else items

    new_ids: list[int] = []
    new_vecs: np.ndarray | None = None

    if new_items:
        logger.info(f"Embedding {len(new_items)} new corpus items...")
        texts = [_text_for_item(it) for it in new_items]
        new_vecs = embed_texts(texts)
        new_ids = [it.id for it in new_items]

    # Merge cached + new
    if rebuild or cached_vecs is None:
        all_ids = new_ids
        all_vecs = new_vecs if new_vecs is not None else np.empty((0, 0), dtype=np.float32)
    else:
        all_ids = cached_ids + new_ids
        if new_vecs is not None:
            all_vecs = np.vstack([cached_vecs, new_vecs])
        else:
            all_ids = cached_ids
            all_vecs = cached_vecs

    np.savez_compressed(emb_path, item_ids=np.array(all_ids), vectors=all_vecs)
    logger.info(f"Saved {len(all_ids)} embeddings to {emb_path}")

    dim = int(all_vecs.shape[1]) if all_vecs.ndim == 2 and all_vecs.shape[0] > 0 else 0
    return {
        "total": total,
        "embedded": len(new_ids),
        "cached": len(cached_ids) if not rebuild else 0,
        "dim": dim,
    }


def compute_umap(rebuild: bool = False) -> dict:
    """Run UMAP on saved embeddings and write umap2d.json.

    umap2d.json format: {"<item_id>": [x, y], ...}

    Returns dict with keys: total, output_path.
    """
    emb_path = _embeddings_path()
    umap_path = _umap_path()

    if not emb_path.exists():
        raise FileNotFoundError(f"Embeddings not found at {emb_path}. Run 'ri corpus embed' first.")

    if umap_path.exists() and not rebuild:
        logger.info(f"UMAP coordinates already exist at {umap_path}, skipping (use --rebuild to force).")
        data = json.loads(umap_path.read_text())
        return {"total": len(data), "output_path": str(umap_path)}

    data = np.load(emb_path, allow_pickle=False)
    item_ids: list[int] = data["item_ids"].tolist()
    vectors: np.ndarray = data["vectors"]

    n = len(item_ids)
    if n == 0:
        logger.warning("No embeddings to project.")
        return {"total": 0, "output_path": str(umap_path)}

    logger.info(f"Running UMAP on {n} vectors (dim={vectors.shape[1]})...")

    try:
        import umap

        # n_neighbors must be < n_samples
        n_neighbors = min(15, n - 1) if n > 1 else 1
        reducer = umap.UMAP(n_components=2, n_neighbors=n_neighbors, random_state=42, low_memory=True)
        coords = reducer.fit_transform(vectors)
    except ImportError:
        logger.warning("umap-learn not installed — using PCA as fallback for 2D projection.")
        from sklearn.decomposition import PCA

        pca = PCA(n_components=min(2, vectors.shape[1]))
        coords = pca.fit_transform(vectors)

    umap_data = {str(iid): [float(coords[i, 0]), float(coords[i, 1])] for i, iid in enumerate(item_ids)}
    umap_path.write_text(json.dumps(umap_data, ensure_ascii=False), encoding="utf-8")
    logger.info(f"UMAP coordinates saved to {umap_path}")

    return {"total": n, "output_path": str(umap_path)}
