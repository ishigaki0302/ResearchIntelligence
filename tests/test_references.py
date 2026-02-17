"""Tests for reference extraction pipeline."""

from sqlalchemy import select

from app.core.models import Citation, Item, ItemId
from app.graph.citations import resolve_citations
from app.pipelines.references import extract_references_from_text, extract_references_for_item


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


def test_extract_references_section():
    """Given sample text with References section, entries should be found."""
    result = extract_references_from_text(SAMPLE_TEXT)
    assert len(result["entries"]) == 3
    assert result["raw_section"] != ""


def test_doi_extraction():
    """Entries with DOIs should have them extracted."""
    result = extract_references_from_text(SAMPLE_TEXT)
    entries = result["entries"]
    # Entry [2] has a DOI
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


def test_citations_inserted(tmp_db, tmp_path):
    """After extraction, citations table should have rows."""
    # Create item with text file
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

    cits = tmp_db.execute(
        select(Citation).where(Citation.src_item_id == item.id)
    ).scalars().all()
    assert len(cits) == 3
    assert all(c.source == "pdf" for c in cits)


def test_idempotent(tmp_db, tmp_path):
    """Running extraction twice should not create duplicate citations."""
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
    assert len(entries2) == 0  # idempotent: already processed

    cits = tmp_db.execute(
        select(Citation).where(Citation.src_item_id == item.id)
    ).scalars().all()
    assert len(cits) == 3  # still only 3


def test_resolve_by_doi(tmp_db):
    """Citation with DOI dst_key should resolve to item with matching item_id."""
    # Create a source item
    src = Item(title="Citing Paper", year=2024)
    tmp_db.add(src)
    tmp_db.flush()

    # Create a destination item with DOI
    dst = Item(title="Cited Paper", year=2019, bibtex_key="devlin2019bert")
    tmp_db.add(dst)
    tmp_db.flush()
    tmp_db.add(ItemId(item_id=dst.id, id_type="doi", id_value="10.18653/v1/N19-1423"))
    tmp_db.flush()

    # Create unresolved citation with DOI as dst_key
    cit = Citation(
        src_item_id=src.id,
        dst_key="10.18653/v1/N19-1423",
        source="pdf",
    )
    tmp_db.add(cit)
    tmp_db.flush()

    result = resolve_citations(tmp_db)
    assert result["resolved"] == 1

    # Verify the citation is now resolved
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
        source="pdf",
    )
    tmp_db.add(cit)
    tmp_db.flush()

    result = resolve_citations(tmp_db)
    assert result["resolved"] == 1
    updated_cit = tmp_db.get(Citation, cit.id)
    assert updated_cit.dst_item_id == dst.id
