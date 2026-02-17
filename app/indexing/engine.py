"""Search indexing engine combining FTS5 (BM25) and FAISS (vector)."""

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Item, Note

logger = logging.getLogger(__name__)


# ── FTS5 (BM25) ─────────────────────────────────────────────────────────

def rebuild_fts(session: Session):
    """Rebuild the FTS5 index for items and notes."""
    conn = session.connection()

    # Clear existing FTS data
    conn.execute(text("DELETE FROM items_fts"))
    conn.execute(text("DELETE FROM notes_fts"))

    # Index items
    items = session.execute(select(Item)).scalars().all()
    for item in items:
        content = ""
        if item.text_path:
            text_file = resolve_path(item.text_path)
            if text_file.exists():
                content = text_file.read_text(encoding="utf-8")[:50000]  # limit size

        conn.execute(
            text("INSERT INTO items_fts(rowid, title, abstract, content) VALUES (:rid, :title, :abstract, :content)"),
            {"rid": item.id, "title": item.title or "", "abstract": item.abstract or "", "content": content},
        )

    # Index notes
    notes = session.execute(select(Note)).scalars().all()
    for note in notes:
        body = ""
        note_path = resolve_path(note.path)
        if note_path.exists():
            body = note_path.read_text(encoding="utf-8")[:50000]
        conn.execute(
            text("INSERT INTO notes_fts(rowid, title, body) VALUES (:rid, :title, :body)"),
            {"rid": note.id, "title": note.title or "", "body": body},
        )

    session.commit()
    logger.info(f"FTS5 indexed {len(items)} items, {len(notes)} notes")


def search_fts(session: Session, query: str, top_k: int = 20) -> list[dict]:
    """Search items using FTS5 BM25.

    Returns list of {"item_id": int, "rank": float, "snippet": str}.
    """
    conn = session.connection()
    rows = conn.execute(
        text("""
            SELECT rowid, rank, snippet(items_fts, 0, '<b>', '</b>', '...', 32) as snip
            FROM items_fts
            WHERE items_fts MATCH :q
            ORDER BY rank
            LIMIT :k
        """),
        {"q": query, "k": top_k},
    ).fetchall()

    results = []
    for row in rows:
        results.append({
            "item_id": row[0],
            "bm25_score": -row[1],  # FTS5 rank is negative (lower = better)
            "snippet": row[2],
        })
    return results


def search_notes_fts(session: Session, query: str, top_k: int = 10) -> list[dict]:
    """Search notes using FTS5."""
    conn = session.connection()
    rows = conn.execute(
        text("""
            SELECT rowid, rank, snippet(notes_fts, 1, '<b>', '</b>', '...', 32) as snip
            FROM notes_fts
            WHERE notes_fts MATCH :q
            ORDER BY rank
            LIMIT :k
        """),
        {"q": query, "k": top_k},
    ).fetchall()

    results = []
    for row in rows:
        results.append({
            "note_id": row[0],
            "bm25_score": -row[1],
            "snippet": row[2],
        })
    return results


# ── FAISS (Vector) ───────────────────────────────────────────────────────

_embedder = None


def _get_embedder():
    """Lazy-load the sentence-transformers model."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        cfg = get_config()
        model_name = cfg["embedding"]["model"]
        _embedder = SentenceTransformer(model_name)
        logger.info(f"Loaded embedding model: {model_name}")
    return _embedder


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a list of texts. Returns (N, dim) array."""
    model = _get_embedder()
    return model.encode(texts, show_progress_bar=False, convert_to_numpy=True)


def _faiss_paths() -> tuple[Path, Path]:
    cfg = get_config()
    idx_path = resolve_path(cfg["indexing"]["faiss_index_path"])
    map_path = resolve_path(cfg["indexing"]["faiss_id_map_path"])
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    return idx_path, map_path


def rebuild_faiss(session: Session):
    """Rebuild the FAISS index from items (title+abstract) and notes."""
    import faiss

    cfg = get_config()
    dim = cfg["embedding"]["dimension"]

    items = session.execute(select(Item)).scalars().all()
    notes = session.execute(select(Note)).scalars().all()

    texts = []
    id_map = []  # list of {"type": "item"|"note", "id": int}

    for item in items:
        # Combine title + abstract for embedding
        parts = [item.title or ""]
        if item.abstract:
            parts.append(item.abstract)
        if item.tldr:
            parts.append(item.tldr)
        text_str = " ".join(parts).strip()
        if text_str:
            texts.append(text_str)
            id_map.append({"type": "item", "id": item.id})

    for note in notes:
        note_path = resolve_path(note.path)
        if note_path.exists():
            body = note_path.read_text(encoding="utf-8")[:10000]
            if body.strip():
                texts.append(body)
                id_map.append({"type": "note", "id": note.id})

    if not texts:
        logger.warning("No texts to index for FAISS")
        return

    logger.info(f"Embedding {len(texts)} texts for FAISS...")
    embeddings = embed_texts(texts)

    # Build index
    index = faiss.IndexFlatIP(dim)  # inner product (cosine after normalization)
    # Normalize for cosine similarity
    faiss.normalize_L2(embeddings)
    index.add(embeddings)

    # Save
    idx_path, map_path = _faiss_paths()
    faiss.write_index(index, str(idx_path))
    map_path.write_text(json.dumps(id_map), encoding="utf-8")
    logger.info(f"FAISS index built: {index.ntotal} vectors, dim={dim}")


def search_faiss(query: str, top_k: int = 20) -> list[dict]:
    """Search using FAISS vector similarity.

    Returns list of {"type": "item"|"note", "id": int, "score": float}.
    """
    import faiss

    idx_path, map_path = _faiss_paths()
    if not idx_path.exists():
        logger.warning("FAISS index not found. Run 'ri index' first.")
        return []

    index = faiss.read_index(str(idx_path))
    id_map = json.loads(map_path.read_text(encoding="utf-8"))

    q_vec = embed_texts([query])
    faiss.normalize_L2(q_vec)

    scores, indices = index.search(q_vec, min(top_k, index.ntotal))

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue
        entry = id_map[idx]
        results.append({
            "type": entry["type"],
            "id": entry["id"],
            "vector_score": float(score),
        })
    return results


# ── Combined Search ──────────────────────────────────────────────────────

def rebuild_index(session: Session):
    """Rebuild both FTS5 and FAISS indices."""
    logger.info("Rebuilding FTS5 index...")
    rebuild_fts(session)
    logger.info("Rebuilding FAISS index...")
    rebuild_faiss(session)
    logger.info("Index rebuild complete.")


def hybrid_search(
    session: Session,
    query: str,
    top_k: int = 20,
    bm25_weight: float = 0.5,
    vector_weight: float = 0.5,
    filters: dict | None = None,
) -> list[dict]:
    """Combined BM25 + vector search with optional filters.

    Filters: {"year_from": int, "year_to": int, "venue": str, "type": str, "tag": str}
    Returns list of {"item_id", "score", "bm25_score", "vector_score", "snippet", "item"}.
    """
    # BM25 results
    bm25_results = search_fts(session, query, top_k=top_k * 2)

    # Vector results
    vector_results = search_faiss(query, top_k=top_k * 2)
    vector_items = {r["id"]: r for r in vector_results if r["type"] == "item"}

    # Merge scores
    all_item_ids = set()
    scores = {}

    for r in bm25_results:
        iid = r["item_id"]
        all_item_ids.add(iid)
        scores.setdefault(iid, {"bm25": 0.0, "vector": 0.0, "snippet": ""})
        scores[iid]["bm25"] = r["bm25_score"]
        scores[iid]["snippet"] = r.get("snippet", "")

    for iid, r in vector_items.items():
        all_item_ids.add(iid)
        scores.setdefault(iid, {"bm25": 0.0, "vector": 0.0, "snippet": ""})
        scores[iid]["vector"] = r["vector_score"]

    # Normalize scores
    max_bm25 = max((s["bm25"] for s in scores.values()), default=1.0) or 1.0
    max_vec = max((s["vector"] for s in scores.values()), default=1.0) or 1.0

    combined = []
    for iid in all_item_ids:
        s = scores[iid]
        norm_bm25 = s["bm25"] / max_bm25
        norm_vec = s["vector"] / max_vec
        total = bm25_weight * norm_bm25 + vector_weight * norm_vec
        combined.append({
            "item_id": iid,
            "score": total,
            "bm25_score": s["bm25"],
            "vector_score": s["vector"],
            "snippet": s["snippet"],
        })

    combined.sort(key=lambda x: x["score"], reverse=True)

    # Apply filters
    if filters:
        filtered = []
        for result in combined:
            item = session.get(Item, result["item_id"])
            if not item:
                continue

            if filters.get("year_from") and item.year and item.year < filters["year_from"]:
                continue
            if filters.get("year_to") and item.year and item.year > filters["year_to"]:
                continue
            if filters.get("venue") and item.venue and filters["venue"].lower() not in item.venue.lower():
                continue
            if filters.get("type") and item.type != filters["type"]:
                continue

            result["item"] = item
            filtered.append(result)

        return filtered[:top_k]

    # Load items
    for result in combined[:top_k]:
        result["item"] = session.get(Item, result["item_id"])

    return combined[:top_k]
