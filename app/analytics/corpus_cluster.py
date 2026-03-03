"""Topic Atlas: Clustering + LLM labeling for corpus papers.

Reads embeddings.npz and umap2d.json, runs HDBSCAN/KMeans clustering,
optionally labels clusters with an LLM, and writes cluster_summary.json.

cluster_summary.json format:
[
  {
    "cluster_id": 0,
    "label_en": "Neural Machine Translation",
    "label_ja": "ニューラル機械翻訳",
    "keywords": ["transformer", "attention", "BLEU"],
    "paper_ids": [1, 4, 7, ...],
    "centroid_xy": [x, y],
    "representative_ids": [4, 1, 7]
  },
  ...
]
"""

import json
import logging
from pathlib import Path

import numpy as np

from app.core.config import get_config, resolve_path
from app.core.models import Item

logger = logging.getLogger(__name__)


def _corpus_dir() -> Path:
    cfg = get_config()
    d = resolve_path(cfg["storage"]["base_dir"]) / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _embeddings_path() -> Path:
    return _corpus_dir() / "embeddings.npz"


def _umap_path() -> Path:
    return _corpus_dir() / "umap2d.json"


def _cluster_summary_path() -> Path:
    return _corpus_dir() / "cluster_summary.json"


def _extract_keywords(texts: list[str], top_n: int = 5) -> list[str]:
    """Simple TF-IDF keyword extraction for a cluster."""
    import re
    from collections import Counter

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
        "new",
        "show",
        "propose",
        "proposed",
        "paper",
        "model",
        "models",
        "method",
        "methods",
        "approach",
        "approaches",
        "result",
        "results",
        "performance",
        "high",
        "large",
        "pre",
        "trained",
        "language",
        "work",
        "task",
        "tasks",
        "data",
        "dataset",
        "datasets",
    }
    words = []
    for text in texts:
        tokens = re.findall(r"[a-z]{3,}", text.lower())
        words.extend(t for t in tokens if t not in _STOPWORDS)
    freq = Counter(words)
    return [w for w, _ in freq.most_common(top_n)]


def _llm_label_cluster(representative_titles: list[str], representative_abstracts: list[str]) -> dict:
    """Ask the LLM to generate a cluster label. Returns {"en": ..., "ja": ...}.

    Falls back to empty strings if LLM unavailable.
    """
    try:
        from app.gpu.llm import generate_single  # type: ignore

        context = "\n".join(f"- {t}: {a[:200]}" for t, a in zip(representative_titles, representative_abstracts) if t)
        prompt = (
            "以下の論文グループにラベルをつけてください。\n"
            'JSON のみ出力: {"en": "<English label>", "ja": "<日本語ラベル>"}\n\n'
            f"論文一覧:\n{context}"
        )
        text = generate_single(prompt, max_new_tokens=80, temperature=0.1)
        # Extract JSON
        import re

        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.debug(f"LLM labeling skipped: {e}")
    return {"en": "", "ja": ""}


def cluster_corpus(
    session,
    method: str = "hdbscan",
    n_clusters: int = 10,
    rebuild: bool = False,
) -> list[dict]:
    """Run clustering on corpus embeddings and return cluster summary list.

    Args:
        session: SQLAlchemy session (used to fetch item titles/abstracts).
        method: "hdbscan" or "kmeans".
        n_clusters: Number of clusters (only used for kmeans).
        rebuild: If True, overwrite existing cluster_summary.json.

    Returns list of cluster dicts (also written to cluster_summary.json).
    """
    summary_path = _cluster_summary_path()
    if summary_path.exists() and not rebuild:
        logger.info(f"Cluster summary already exists at {summary_path}, skipping (use --rebuild).")
        return json.loads(summary_path.read_text())

    emb_path = _embeddings_path()
    if not emb_path.exists():
        raise FileNotFoundError(f"Embeddings not found: {emb_path}. Run 'ri corpus embed' first.")

    data = np.load(emb_path, allow_pickle=False)
    item_ids: list[int] = data["item_ids"].tolist()
    vectors: np.ndarray = data["vectors"]
    n = len(item_ids)

    if n == 0:
        logger.warning("No embeddings — nothing to cluster.")
        summary_path.write_text("[]", encoding="utf-8")
        return []

    # Load UMAP coords (optional — used for centroid_xy)
    umap_coords: dict[str, list[float]] = {}
    umap_path = _umap_path()
    if umap_path.exists():
        umap_coords = json.loads(umap_path.read_text())

    # --- Clustering ---
    labels = _run_clustering(vectors, method=method, n_clusters=min(n_clusters, max(n - 1, 1)))

    # --- Build cluster → item mapping ---
    clusters: dict[int, list[int]] = {}
    for iid, label in zip(item_ids, labels):
        clusters.setdefault(int(label), []).append(int(iid))

    # Fetch items from DB
    from sqlalchemy import select

    all_items: dict[int, Item] = {
        it.id: it for it in session.execute(select(Item).where(Item.type == "corpus")).scalars().all()
    }

    # --- Per-cluster summary ---
    cluster_summaries = []
    for cid, paper_ids in sorted(clusters.items()):
        if cid == -1:
            # HDBSCAN noise — skip or make a separate cluster
            continue

        # Centroid in UMAP space
        xy_list = [umap_coords.get(str(pid)) for pid in paper_ids if str(pid) in umap_coords]
        centroid_xy = (
            [float(np.mean([xy[0] for xy in xy_list])), float(np.mean([xy[1] for xy in xy_list]))]
            if xy_list
            else [0.0, 0.0]
        )

        # Representative papers: closest to centroid in embedding space
        cluster_indices = [item_ids.index(pid) for pid in paper_ids if pid in item_ids]
        cluster_vecs = vectors[cluster_indices]
        cluster_mean = cluster_vecs.mean(axis=0)
        dists = np.linalg.norm(cluster_vecs - cluster_mean, axis=1)
        top3_local = np.argsort(dists)[:3].tolist()
        representative_ids = [paper_ids[i] for i in top3_local]

        # Keywords from titles
        titles = [all_items[pid].title or "" for pid in paper_ids if pid in all_items]
        abstracts = [all_items[pid].abstract or "" for pid in paper_ids if pid in all_items]
        keywords = _extract_keywords(titles + abstracts)

        # LLM label
        rep_titles = [all_items[rid].title or "" for rid in representative_ids if rid in all_items]
        rep_abstracts = [all_items[rid].abstract or "" for rid in representative_ids if rid in all_items]
        llm_label = _llm_label_cluster(rep_titles[:3], rep_abstracts[:3])

        label_en = llm_label.get("en") or f"Cluster {cid}"
        label_ja = llm_label.get("ja") or f"クラスタ {cid}"

        cluster_summaries.append(
            {
                "cluster_id": cid,
                "label_en": label_en,
                "label_ja": label_ja,
                "keywords": keywords,
                "paper_ids": paper_ids,
                "centroid_xy": centroid_xy,
                "representative_ids": representative_ids,
            }
        )

    summary_path.write_text(json.dumps(cluster_summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Cluster summary written: {len(cluster_summaries)} clusters -> {summary_path}")
    return cluster_summaries


def _run_clustering(vectors: np.ndarray, method: str, n_clusters: int) -> np.ndarray:
    """Run clustering and return integer label array (length = n_samples)."""
    if method == "hdbscan":
        try:
            import hdbscan

            min_cluster_size = max(2, len(vectors) // 20)
            clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean")
            return clusterer.fit_predict(vectors)
        except ImportError:
            logger.warning("hdbscan not installed — falling back to kmeans.")

    # KMeans fallback
    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    return km.fit_predict(vectors)
