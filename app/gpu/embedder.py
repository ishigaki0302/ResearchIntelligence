"""GPU-accelerated embedding backend.

Uses BAAI/bge-m3 or other high-quality multilingual models when CUDA is available.
Falls back transparently to the sentence-transformers backend when GPU is absent.

Usage:
    from app.gpu.embedder import get_gpu_embedder, gpu_embed_texts
    embedder = get_gpu_embedder()   # None on CPU-only machines
    vecs = gpu_embed_texts(texts)   # uses GPU if available, else CPU fallback
"""

import logging
from typing import Optional

import numpy as np

from app.core.config import get_config
from app.gpu import is_gpu_available

logger = logging.getLogger(__name__)

_gpu_embedder = None


def get_gpu_embedder():
    """Lazy-load the GPU embedding model.

    Returns the model instance or None if GPU is unavailable.
    """
    global _gpu_embedder
    if _gpu_embedder is not None:
        return _gpu_embedder

    if not is_gpu_available():
        logger.debug("GPU not available — GPU embedder not loaded")
        return None

    cfg = get_config()
    gpu_cfg = cfg.get("gpu", {})
    if not gpu_cfg.get("enabled", True):
        return None

    model_name = gpu_cfg.get("embedding", {}).get("model", "BAAI/bge-m3")

    try:
        from sentence_transformers import SentenceTransformer

        device = "cuda"
        logger.info(f"Loading GPU embedding model: {model_name}")
        _gpu_embedder = SentenceTransformer(model_name, device=device)
        logger.info(f"GPU embedder ready: {model_name} on {device}")
        return _gpu_embedder
    except Exception as e:
        logger.warning(f"Failed to load GPU embedder ({model_name}): {e}")
        return None


def gpu_embed_texts(
    texts: list[str],
    batch_size: Optional[int] = None,
    show_progress: bool = False,
) -> np.ndarray:
    """Embed texts using GPU model if available, else CPU sentence-transformers.

    Returns (N, dim) float32 array.
    """
    if not texts:
        return np.zeros((0, 1024), dtype=np.float32)

    embedder = get_gpu_embedder()

    if embedder is not None:
        cfg = get_config()
        bs = batch_size or cfg.get("gpu", {}).get("embedding", {}).get("batch_size", 256)
        vecs = embedder.encode(
            texts,
            batch_size=bs,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return vecs.astype(np.float32)

    # CPU fallback — use existing sentence-transformers engine
    from app.indexing.engine import embed_texts

    return embed_texts(texts)


def gpu_embedding_dim() -> int:
    """Return the embedding dimension for the active GPU model."""
    embedder = get_gpu_embedder()
    if embedder is not None:
        return embedder.get_sentence_embedding_dimension()

    cfg = get_config()
    return cfg.get("embedding", {}).get("dimension", 384)


def reset_gpu_embedder():
    """Reset cached GPU embedder (for testing or model switching)."""
    global _gpu_embedder
    _gpu_embedder = None
