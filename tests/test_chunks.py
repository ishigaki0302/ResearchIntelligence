"""Tests for text chunking and chunk-based search."""

from sqlalchemy import select

from app.core.models import Chunk, Item
from app.indexing.chunker import chunk_item, chunk_text

SAMPLE_TEXT = """# Introduction

This is the introduction section of a research paper about machine learning.
We discuss various approaches to natural language processing and their applications.
The field has seen tremendous growth in recent years with the advent of transformer models.

# Method

Our method builds upon the transformer architecture. We propose a novel attention mechanism
that allows for more efficient processing of long documents. The key innovation is the use
of sparse attention patterns that reduce the quadratic complexity to linear.

We evaluate our approach on multiple benchmarks including GLUE, SuperGLUE, and SQuAD.
Results show consistent improvements over baseline methods across all tasks.

# Results

Table 1 shows our main results. Our model achieves state-of-the-art performance on 8 out of
10 benchmarks tested. The improvements are particularly notable on tasks requiring long-range
dependencies, where our sparse attention mechanism provides the greatest benefit.

We also conduct ablation studies to understand the contribution of each component. Removing
the sparse attention reduces performance by 3.2 points on average.

# Conclusion

We have presented a novel sparse attention mechanism for transformer models. Our approach
achieves strong results while maintaining computational efficiency. Future work will explore
applications to other domains including vision and speech.

References

[1] Vaswani et al. Attention is all you need. NeurIPS 2017.
[2] Devlin et al. BERT: Pre-training of deep bidirectional transformers. NAACL 2019.
"""


def test_chunk_text_basic():
    """Chunking should produce multiple chunks from a multi-section document."""
    chunks = chunk_text(SAMPLE_TEXT, target_size=500)
    assert len(chunks) >= 2
    # Each chunk should have required fields
    for c in chunks:
        assert "text" in c
        assert "start_char" in c
        assert "end_char" in c
        assert "chunk_index" in c
        assert len(c["text"]) > 0


def test_chunk_text_headings():
    """Chunks should respect heading boundaries."""
    chunks = chunk_text(SAMPLE_TEXT, target_size=500)
    # With heading-aware splitting, we should get multiple sections
    assert len(chunks) >= 3
    # Check that chunks are ordered by index
    indices = [c["chunk_index"] for c in chunks]
    assert indices == sorted(indices)


def test_chunk_text_empty():
    """Empty or whitespace text should return no chunks."""
    assert chunk_text("") == []
    assert chunk_text("   ") == []
    assert chunk_text("\n\n") == []


def test_chunk_text_short():
    """Short text within target size should produce a single chunk."""
    chunks = chunk_text("This is a short text.", target_size=1000)
    assert len(chunks) == 1
    assert chunks[0]["text"].strip() == "This is a short text."


def test_chunk_item_creates_db_rows(tmp_db, tmp_path):
    """chunk_item should create Chunk rows in the database."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    text_dir = tmp_path / "data" / "library" / "papers" / str(item.id)
    text_dir.mkdir(parents=True, exist_ok=True)
    text_file = text_dir / "text.txt"
    text_file.write_text(SAMPLE_TEXT, encoding="utf-8")
    item.text_path = str(text_file.relative_to(tmp_path))
    tmp_db.flush()

    chunks = chunk_item(tmp_db, item)
    assert len(chunks) > 0

    db_chunks = tmp_db.execute(select(Chunk).where(Chunk.item_id == item.id)).scalars().all()
    assert len(db_chunks) == len(chunks)


def test_chunk_item_idempotent(tmp_db, tmp_path):
    """Running chunk_item twice should produce the same number of chunks (replaces old)."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    text_dir = tmp_path / "data" / "library" / "papers" / str(item.id)
    text_dir.mkdir(parents=True, exist_ok=True)
    text_file = text_dir / "text.txt"
    text_file.write_text(SAMPLE_TEXT, encoding="utf-8")
    item.text_path = str(text_file.relative_to(tmp_path))
    tmp_db.flush()

    chunks1 = chunk_item(tmp_db, item)
    count1 = len(chunks1)

    chunks2 = chunk_item(tmp_db, item)
    count2 = len(chunks2)

    assert count1 == count2

    # Verify DB has exactly count2 rows
    db_chunks = tmp_db.execute(select(Chunk).where(Chunk.item_id == item.id)).scalars().all()
    assert len(db_chunks) == count2


def test_chunk_item_no_text(tmp_db):
    """Item without text_path should return empty list."""
    item = Item(title="No Text Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    chunks = chunk_item(tmp_db, item)
    assert chunks == []
