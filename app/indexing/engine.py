"""Search indexing engine combining FTS5 (BM25) and FAISS (vector)."""

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Chunk, Item, Note

logger = logging.getLogger(__name__)


# ── FTS5 (BM25) ─────────────────────────────────────────────────────────


def rebuild_fts(session: Session):
    """Rebuild the FTS5 index for items and notes."""
    conn = session.connection()

    # Contentless FTS5 tables don't support DELETE — drop and recreate
    conn.execute(text("DROP TABLE IF EXISTS items_fts"))
    conn.execute(text("DROP TABLE IF EXISTS notes_fts"))
    conn.execute(
        text(
            "CREATE VIRTUAL TABLE items_fts USING fts5(" "title, abstract, content, content='', content_rowid='rowid')"
        )
    )
    conn.execute(text("CREATE VIRTUAL TABLE notes_fts USING fts5(title, body, content='', content_rowid='rowid')"))

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
        results.append(
            {
                "item_id": row[0],
                "bm25_score": -row[1],  # FTS5 rank is negative (lower = better)
                "snippet": row[2],
            }
        )
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
        results.append(
            {
                "note_id": row[0],
                "bm25_score": -row[1],
                "snippet": row[2],
            }
        )
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


def _faiss_chunk_paths() -> tuple[Path, Path]:
    cfg = get_config()
    default_idx = "data/cache/embeddings/faiss_chunks.index"
    default_map = "data/cache/embeddings/faiss_chunks_ids.json"
    idx_path = resolve_path(cfg["indexing"].get("faiss_chunk_index_path", default_idx))
    map_path = resolve_path(cfg["indexing"].get("faiss_chunk_id_map_path", default_map))
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


def rebuild_faiss_chunks(session: Session):
    """Rebuild the FAISS index for text chunks."""
    import faiss

    cfg = get_config()
    dim = cfg["embedding"]["dimension"]

    chunks = session.execute(select(Chunk)).scalars().all()
    if not chunks:
        logger.info("No chunks to index for FAISS chunks")
        return

    texts = []
    id_map = []

    for chunk in chunks:
        if chunk.text and chunk.text.strip():
            texts.append(chunk.text)
            id_map.append({"chunk_id": chunk.id, "item_id": chunk.item_id})

    if not texts:
        logger.warning("No chunk texts to embed")
        return

    logger.info(f"Embedding {len(texts)} chunks for FAISS...")
    embeddings = embed_texts(texts)

    index = faiss.IndexFlatIP(dim)
    faiss.normalize_L2(embeddings)
    index.add(embeddings)

    idx_path, map_path = _faiss_chunk_paths()
    faiss.write_index(index, str(idx_path))
    map_path.write_text(json.dumps(id_map), encoding="utf-8")
    logger.info(f"FAISS chunk index built: {index.ntotal} vectors, dim={dim}")


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
        results.append(
            {
                "type": entry["type"],
                "id": entry["id"],
                "vector_score": float(score),
            }
        )
    return results


def search_faiss_chunks(query: str, top_k: int = 20) -> list[dict]:
    """Search chunks using FAISS vector similarity.

    Returns list of {"chunk_id": int, "item_id": int, "score": float}.
    """
    import faiss

    idx_path, map_path = _faiss_chunk_paths()
    if not idx_path.exists():
        logger.warning("FAISS chunk index not found. Run 'ri index --chunks' first.")
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
        results.append(
            {
                "chunk_id": entry["chunk_id"],
                "item_id": entry["item_id"],
                "score": float(score),
            }
        )
    return results


# ── Combined Search ──────────────────────────────────────────────────────


def rebuild_index(session: Session, include_chunks: bool = False):
    """Rebuild both FTS5 and FAISS indices."""
    logger.info("Rebuilding FTS5 index...")
    rebuild_fts(session)
    logger.info("Rebuilding FAISS index...")
    rebuild_faiss(session)
    if include_chunks:
        logger.info("Rebuilding FAISS chunk index...")
        rebuild_faiss_chunks(session)
    logger.info("Index rebuild complete.")


def _compute_text_hash(item: Item) -> str:
    """Compute SHA256 hash of title+abstract for change detection."""
    text_str = f"{item.title or ''}\n{item.abstract or ''}"
    return hashlib.sha256(text_str.encode("utf-8")).hexdigest()


def incremental_index(session: Session, include_chunks: bool = False) -> dict:
    """Incrementally re-index only changed items.

    Compares stored text_hash vs computed; only re-embeds changed items.
    FTS5 is always fully rebuilt (contentless limitation).
    Returns {total, changed, unchanged}.
    """
    items = session.execute(select(Item)).scalars().all()
    total = len(items)
    changed = 0
    unchanged = 0

    # Detect which items changed
    changed_items = []
    for item in items:
        new_hash = _compute_text_hash(item)
        if item.text_hash != new_hash:
            item.text_hash = new_hash
            changed_items.append(item)
            changed += 1
        else:
            unchanged += 1

    session.flush()

    # FTS5 must always be fully rebuilt (contentless tables don't support partial updates)
    logger.info("Rebuilding FTS5 index (full rebuild required)...")
    rebuild_fts(session)

    if changed_items:
        # Rebuild full FAISS index (simpler and safer than partial update)
        logger.info(f"Re-embedding {len(changed_items)} changed items (rebuilding FAISS)...")
        rebuild_faiss(session)
        if include_chunks:
            rebuild_faiss_chunks(session)
    else:
        logger.info("No items changed — skipping FAISS rebuild.")

    return {"total": total, "changed": changed, "unchanged": unchanged}


def hybrid_search(
    session: Session,
    query: str,
    top_k: int = 20,
    bm25_weight: float = 0.5,
    vector_weight: float = 0.5,
    filters: dict | None = None,
    scope: str = "item",
) -> list[dict]:
    """Combined BM25 + vector search with optional filters.

    Args:
        scope: "item" (item-level only), "chunk" (chunk-level only), or "both" (merged).

    Filters: {"year_from": int, "year_to": int, "venue": str, "type": str, "tag": str}
    Returns list of {"item_id", "score", "bm25_score", "vector_score", "snippet", "item", "matched_chunks"}.
    """
    # BM25 results
    bm25_results = search_fts(session, query, top_k=top_k * 2)

    # Vector results (item-level)
    vector_results = search_faiss(query, top_k=top_k * 2)
    vector_items = {r["id"]: r for r in vector_results if r["type"] == "item"}

    # Merge scores
    all_item_ids = set()
    scores = {}

    for r in bm25_results:
        iid = r["item_id"]
        all_item_ids.add(iid)
        scores.setdefault(iid, {"bm25": 0.0, "vector": 0.0, "snippet": "", "matched_chunks": []})
        scores[iid]["bm25"] = r["bm25_score"]
        scores[iid]["snippet"] = r.get("snippet", "")

    if scope in ("item", "both"):
        for iid, r in vector_items.items():
            all_item_ids.add(iid)
            scores.setdefault(iid, {"bm25": 0.0, "vector": 0.0, "snippet": "", "matched_chunks": []})
            scores[iid]["vector"] = r["vector_score"]

    # Chunk-level search
    if scope in ("chunk", "both"):
        chunk_results = search_faiss_chunks(query, top_k=top_k * 3)
        # Group by item_id, keep best chunk score per item
        chunk_by_item: dict[int, list[dict]] = {}
        for cr in chunk_results:
            chunk_by_item.setdefault(cr["item_id"], []).append(cr)

        for item_id, crs in chunk_by_item.items():
            all_item_ids.add(item_id)
            scores.setdefault(item_id, {"bm25": 0.0, "vector": 0.0, "snippet": "", "matched_chunks": []})
            best_chunk_score = max(c["score"] for c in crs)
            # Use chunk score as vector boost if higher
            if scope == "chunk":
                scores[item_id]["vector"] = best_chunk_score
            elif scope == "both":
                scores[item_id]["vector"] = max(scores[item_id]["vector"], best_chunk_score)

            # Attach matched chunks (top 3 per item)
            sorted_crs = sorted(crs, key=lambda x: x["score"], reverse=True)[:3]
            for cr in sorted_crs:
                chunk = session.get(Chunk, cr["chunk_id"])
                if chunk:
                    scores[item_id]["matched_chunks"].append(
                        {
                            "chunk_id": cr["chunk_id"],
                            "text": chunk.text[:300],
                            "score": cr["score"],
                            "start_char": chunk.start_char,
                            "end_char": chunk.end_char,
                        }
                    )

    # Normalize scores
    max_bm25 = max((s["bm25"] for s in scores.values()), default=1.0) or 1.0
    max_vec = max((s["vector"] for s in scores.values()), default=1.0) or 1.0

    combined = []
    for iid in all_item_ids:
        s = scores[iid]
        norm_bm25 = s["bm25"] / max_bm25
        norm_vec = s["vector"] / max_vec
        total = bm25_weight * norm_bm25 + vector_weight * norm_vec
        combined.append(
            {
                "item_id": iid,
                "score": total,
                "bm25_score": s["bm25"],
                "vector_score": s["vector"],
                "snippet": s["snippet"],
                "matched_chunks": s["matched_chunks"],
            }
        )

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
            if filters.get("tag"):
                from app.core.models import ItemTag, Tag

                tag_match = session.execute(
                    select(ItemTag)
                    .join(Tag)
                    .where(ItemTag.item_id == item.id, Tag.name == filters["tag"])
                ).first()
                if not tag_match:
                    continue
            if filters.get("author"):
                author_query = filters["author"].lower()
                if not any(author_query in name.lower() for name in item.author_names):
                    continue

            result["item"] = item
            filtered.append(result)

        return filtered[:top_k]

    # Load items
    for result in combined[:top_k]:
        result["item"] = session.get(Item, result["item_id"])

    return combined[:top_k]
