"""Tests for reference extraction pipeline."""

from sqlalchemy import select

from app.core.models import Citation, Item, ItemId
from app.graph.citations import resolve_citations
from app.pipelines.references import extract_references_for_item, extract_references_from_text

SAMPLE_TEXT = """
This is the abstract of the paper. We propose a new method.

1 Introduction
Some introduction text here.

2 Method
Our method is based on transformers.

References

[1] Vaswani, A., Shazeer, N., et al. Attention is all you need.
In NeurIPS, 2017. arXiv: 1706.03762

[2] Devlin, J., Chang, M., Lee, K., Toutanova, K. BERT: Pre-training
of deep bidirectional transformers for language understanding.
In NAACL, 2019. 10.18653/v1/N19-1423

[3] Brown, T., et al. Language models are few-shot learners.
In NeurIPS, 2020.
"""

NUMBERED_DOT_TEXT = """
This is a paper about NLP.

References

1. Vaswani, A., Shazeer, N., et al. Attention is all you need. NeurIPS, 2017.

2. Devlin, J., et al. BERT: Pre-training of deep bidirectional transformers. NAACL, 2019.

3. Brown, T., et al. Language models are few-shot learners. NeurIPS, 2020.
"""

ACL_ID_TEXT = """
Some paper text.

References

[1] Smith, J. Some paper title. In Proceedings of ACL, 2023. P23-1042

[2] Jones, A. Another paper. In Findings of EMNLP, 2024.1234.56789
2024.emnlp-main.123
"""


def test_extract_references_section():
    """Given sample text with References section, entries should be found."""
    result = extract_references_from_text(SAMPLE_TEXT)
    assert len(result["entries"]) == 3
    assert result["raw_section"] != ""


def test_doi_extraction():
    """Entries with DOIs should have them extracted."""
    result = extract_references_from_text(SAMPLE_TEXT)
    entries = result["entries"]
    dois = [e["doi"] for e in entries if e["doi"]]
    assert any("10.18653" in d for d in dois)


def test_arxiv_extraction():
    """Entries with arXiv IDs should have them extracted."""
    result = extract_references_from_text(SAMPLE_TEXT)
    entries = result["entries"]
    arxivs = [e["arxiv"] for e in entries if e["arxiv"]]
    assert "1706.03762" in arxivs


def test_no_references_section():
    """Text without References section should return empty."""
    result = extract_references_from_text("Just some text without references.")
    assert result["entries"] == []


def test_numbered_dot_format():
    """Numbered-dot style references should be extracted."""
    result = extract_references_from_text(NUMBERED_DOT_TEXT)
    assert len(result["entries"]) == 3


def test_acl_id_extraction():
    """ACL Anthology IDs should be extracted from references."""
    result = extract_references_from_text(ACL_ID_TEXT)
    entries = result["entries"]
    acl_ids = [e["acl_id"] for e in entries if e.get("acl_id")]
    assert any("P23-1042" in aid for aid in acl_ids)


def test_all_ids_collected():
    """Entries should collect all found IDs in all_ids dict."""
    result = extract_references_from_text(SAMPLE_TEXT)
    entries = result["entries"]
    # Entry with DOI should have it in all_ids
    doi_entries = [e for e in entries if e["doi"]]
    assert len(doi_entries) > 0
    assert "doi" in doi_entries[0]["all_ids"]


def test_citations_inserted(tmp_db, tmp_path):
    """After extraction, citations table should have rows."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    text_dir = tmp_path / "data" / "library" / "papers" / str(item.id)
    text_dir.mkdir(parents=True, exist_ok=True)
    text_file = text_dir / "text.txt"
    text_file.write_text(SAMPLE_TEXT, encoding="utf-8")
    item.text_path = str(text_file.relative_to(tmp_path))
    tmp_db.flush()

    entries = extract_references_for_item(tmp_db, item)
    assert len(entries) == 3

    cits = tmp_db.execute(select(Citation).where(Citation.src_item_id == item.id)).scalars().all()
    assert len(cits) == 3
    assert all(c.source == "pdf" for c in cits)
    assert all(c.raw_cite_hash is not None for c in cits)


def test_citation_dedup_hash(tmp_db, tmp_path):
    """Re-extraction should not create duplicate citations (hash-based dedup)."""
    item = Item(title="Test Paper", year=2024)
    tmp_db.add(item)
    tmp_db.flush()

    text_dir = tmp_path / "data" / "library" / "papers" / str(item.id)
    text_dir.mkdir(parents=True, exist_ok=True)
    text_file = text_dir / "text.txt"
    text_file.write_text(SAMPLE_TEXT, encoding="utf-8")
    item.text_path = str(text_file.relative_to(tmp_path))
    tmp_db.flush()

    entries1 = extract_references_for_item(tmp_db, item)
    assert len(entries1) == 3

    entries2 = extract_references_for_item(tmp_db, item)
    assert len(entries2) == 0  # all already exist

    cits = tmp_db.execute(select(Citation).where(Citation.src_item_id == item.id)).scalars().all()
    assert len(cits) == 3


def test_resolve_by_doi(tmp_db):
    """Citation with DOI dst_key should resolve to item with matching item_id."""
    src = Item(title="Citing Paper", year=2024)
    tmp_db.add(src)
    tmp_db.flush()

    dst = Item(title="Cited Paper", year=2019, bibtex_key="devlin2019bert")
    tmp_db.add(dst)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=dst.id, id_type="doi", id_value="10.18653/v1/N19-1423"))
    tmp_db.flush()

    cit = Citation(
        src_item_id=src.id,
        dst_key="10.18653/v1/N19-1423",
        raw_cite="Devlin et al. BERT. 10.18653/v1/N19-1423",
        source="pdf",
        raw_cite_hash="test_hash_doi",
    )
    tmp_db.add(cit)
    tmp_db.flush()

    result = resolve_citations(tmp_db)
    assert result["resolved"] >= 1
    assert result["resolved_by_doi"] >= 1

    updated_cit = tmp_db.get(Citation, cit.id)
    assert updated_cit.dst_item_id == dst.id


def test_resolve_by_arxiv(tmp_db):
    """Citation with arXiv dst_key should resolve to item with matching item_id."""
    src = Item(title="Citing Paper", year=2024)
    tmp_db.add(src)
    tmp_db.flush()

    dst = Item(title="Attention Is All You Need", year=2017, bibtex_key="vaswani2017attention")
    tmp_db.add(dst)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=dst.id, id_type="arxiv", id_value="1706.03762"))
    tmp_db.flush()

    cit = Citation(
        src_item_id=src.id,
        dst_key="1706.03762",
        raw_cite="Vaswani et al. Attention is all you need. 1706.03762",
        source="pdf",
        raw_cite_hash="test_hash_arxiv",
    )
    tmp_db.add(cit)
    tmp_db.flush()

    result = resolve_citations(tmp_db)
    assert result["resolved"] >= 1
    updated_cit = tmp_db.get(Citation, cit.id)
    assert updated_cit.dst_item_id == dst.id


def test_resolve_by_title_fallback(tmp_db):
    """Citation should resolve via title normalization when no ID match."""
    src = Item(title="Citing Paper", year=2024)
    tmp_db.add(src)
    tmp_db.flush()

    dst = Item(title="Attention Is All You Need", year=2017)
    tmp_db.add(dst)
    tmp_db.flush()

    cit = Citation(
        src_item_id=src.id,
        raw_cite="Vaswani, A. et al. Attention Is All You Need. In NeurIPS, 2017.",
        source="pdf",
        raw_cite_hash="test_hash_title",
    )
    tmp_db.add(cit)
    tmp_db.flush()

    result = resolve_citations(tmp_db)
    assert result["resolved"] >= 1
    assert result["resolved_by_title"] >= 1

    updated_cit = tmp_db.get(Citation, cit.id)
    assert updated_cit.dst_item_id == dst.id


def test_resolve_stats(tmp_db):
    """Resolution should return method breakdown stats."""
    src = Item(title="Citing Paper", year=2024)
    tmp_db.add(src)
    tmp_db.flush()

    # Create two destinations with different ID types
    dst1 = Item(title="Paper One", year=2020)
    tmp_db.add(dst1)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=dst1.id, id_type="doi", id_value="10.1234/test"))
    tmp_db.flush()

    dst2 = Item(title="Paper Two", year=2021)
    tmp_db.add(dst2)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=dst2.id, id_type="arxiv", id_value="2101.00001"))
    tmp_db.flush()

    cit1 = Citation(
        src_item_id=src.id,
        dst_key="10.1234/test",
        raw_cite="Author. Paper One. 10.1234/test",
        source="pdf",
        raw_cite_hash="hash1",
    )
    cit2 = Citation(
        src_item_id=src.id,
        raw_cite="Author. Paper Two. 2101.00001",
        source="pdf",
        raw_cite_hash="hash2",
    )
    tmp_db.add_all([cit1, cit2])
    tmp_db.flush()

    result = resolve_citations(tmp_db)
    assert result["resolved"] == 2
    assert result["resolved_by_doi"] >= 1
    assert result["resolved_by_arxiv"] >= 1
    assert result["remaining"] == 0
