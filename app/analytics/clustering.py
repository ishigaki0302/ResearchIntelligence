"""Topic clustering using TF-IDF + KMeans."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import Item

logger = logging.getLogger(__name__)


def cluster_items(
    session: Session,
    n_clusters: int = 5,
    cache_dir: str | None = None,
) -> dict:
    """Cluster items by title + abstract using TF-IDF + KMeans.

    Returns: {"clusters": [{"id": int, "size": int, "top_terms": [...], "representative_items": [...]}], ...}
    """
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    items = session.execute(select(Item).where(Item.title.is_not(None))).scalars().all()

    texts = []
    valid_items = []
    for item in items:
        text = (item.title or "") + " " + (item.abstract or "") + " " + (item.tldr or "")
        text = text.strip()
        if text:
            texts.append(text)
            valid_items.append(item)

    if len(texts) < n_clusters:
        n_clusters = max(1, len(texts))

    if not texts:
        return {"clusters": [], "total_items": 0, "n_clusters": 0}

    max_df = 0.9 if len(texts) > 2 else 1.0
    vectorizer = TfidfVectorizer(
        max_features=1000,
        ngram_range=(1, 2),
        stop_words="english",
        max_df=max_df,
        min_df=1,
    )
    tfidf_matrix = vectorizer.fit_transform(texts)
    feature_names = vectorizer.get_feature_names_out()

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(tfidf_matrix)

    # Build cluster info
    clusters = []
    for c_id in range(n_clusters):
        indices = [i for i, label in enumerate(labels) if label == c_id]
        if not indices:
            continue

        # Top terms: centroid weights
        centroid = kmeans.cluster_centers_[c_id]
        top_term_indices = centroid.argsort()[::-1][:10]
        top_terms = [feature_names[i] for i in top_term_indices]

        # Representative items: closest to centroid
        import numpy as np

        cluster_vectors = tfidf_matrix[indices]
        distances = np.linalg.norm(cluster_vectors.toarray() - centroid, axis=1)
        closest = np.argsort(distances)[:5]

        representative = []
        for ci in closest:
            item = valid_items[indices[ci]]
            representative.append(
                {
                    "id": item.id,
                    "title": item.title,
                    "year": item.year,
                    "venue": item.venue,
                }
            )

        clusters.append(
            {
                "id": c_id,
                "size": len(indices),
                "top_terms": top_terms,
                "representative_items": representative,
            }
        )

    result = {
        "clusters": clusters,
        "total_items": len(valid_items),
        "n_clusters": n_clusters,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Cache results
    if cache_dir:
        cache_path = Path(cache_dir)
    else:
        from app.core.config import resolve_path

        cache_path = resolve_path("data/cache/analytics")
    cache_path.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    cache_file = cache_path / f"cluster_{date_str}.json"
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result
