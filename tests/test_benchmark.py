"""Lightweight search performance benchmarks (warn-only)."""

import time

from app.core.models import Item
from app.indexing.engine import rebuild_fts, search_fts


def test_fts_search_performance(tmp_db):
    """FTS5 search on 50 items should average under 2s per query."""
    # Create 50 dummy items
    for i in range(50):
        item = Item(
            title=f"Paper about machine learning topic number {i}",
            abstract=f"This paper discusses neural networks, transformers, and attention mechanisms in context {i}.",
            year=2024,
            type="paper",
        )
        tmp_db.add(item)
    tmp_db.flush()

    # Rebuild FTS index
    rebuild_fts(tmp_db)

    # Run search 5 times and measure
    queries = [
        "machine learning",
        "neural networks",
        "transformers",
        "attention mechanisms",
        "paper about topic",
    ]

    times = []
    for q in queries:
        start = time.time()
        results = search_fts(tmp_db, q, top_k=10)
        elapsed = time.time() - start
        times.append(elapsed)
        assert len(results) > 0, f"Expected results for query '{q}'"

    avg_time = sum(times) / len(times)
    # Generous threshold: warn if slow but don't fail CI
    if avg_time > 2.0:
        import warnings

        warnings.warn(f"FTS search averaging {avg_time:.3f}s per query (threshold: 2.0s)")
    # Hard fail only at very high threshold
    assert avg_time < 10.0, f"FTS search too slow: {avg_time:.3f}s average"
