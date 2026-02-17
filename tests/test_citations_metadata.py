"""Tests for BibTeX/metadata-based citation construction."""

from unittest.mock import patch

from app.core.models import Citation, Item, ItemId
from app.graph.citations import build_citations_from_metadata


def _make_item(session, title, year=2024, ext_ids=None):
    """Helper to create an item with external IDs."""
    item = Item(title=title, year=year, type="paper")
    session.add(item)
    session.flush()
    if ext_ids:
        for id_type, id_value in ext_ids.items():
            iid = ItemId(item_id=item.id, id_type=id_type, id_value=id_value)
            session.add(iid)
    session.flush()
    return item


def test_build_citations_creates_edges(tmp_db):
    """Test that build_citations_from_metadata creates citation edges."""
    session = tmp_db

    # Create source item with DOI
    src = _make_item(session, "Source Paper", ext_ids={"doi": "10.1234/src"})
    # Create target item that the source references
    dst = _make_item(session, "Target Paper", ext_ids={"doi": "10.1234/dst"})
    session.commit()

    # Mock S2 API to return dst as a reference of src
    mock_refs = [
        {
            "paperId": "abc123",
            "title": "Target Paper",
            "externalIds": {"DOI": "10.1234/dst"},
        }
    ]

    with patch("app.connectors.semantic_scholar.get_references", return_value=mock_refs):
        result = build_citations_from_metadata(session, items=[src])

    assert result["processed"] == 1
    assert result["citations_added"] == 1
    assert result["api_hits"] == 1

    # Verify citation row exists
    cit = session.query(Citation).filter_by(src_item_id=src.id, dst_item_id=dst.id).first()
    assert cit is not None
    assert cit.source == "metadata"


def test_build_citations_dedup(tmp_db):
    """Test that re-running does not create duplicate citations."""
    session = tmp_db

    src = _make_item(session, "Source", ext_ids={"doi": "10.1234/src2"})
    dst = _make_item(session, "Target", ext_ids={"doi": "10.1234/dst2"})
    session.commit()

    mock_refs = [
        {
            "paperId": "xyz",
            "title": "Target",
            "externalIds": {"DOI": "10.1234/dst2"},
        }
    ]

    with patch("app.connectors.semantic_scholar.get_references", return_value=mock_refs):
        r1 = build_citations_from_metadata(session, items=[src])
        r2 = build_citations_from_metadata(session, items=[src])

    assert r1["citations_added"] == 1
    assert r2["citations_added"] == 0

    count = session.query(Citation).filter_by(src_item_id=src.id).count()
    assert count == 1


def test_build_citations_skips_no_external_id_no_title(tmp_db):
    """Test that items without external IDs and no title are skipped."""
    session = tmp_db

    item = _make_item(session, "")
    session.commit()

    result = build_citations_from_metadata(session, items=[item])
    assert result["skipped"] == 1
    assert result["processed"] == 0


def test_build_citations_title_fallback_no_match(tmp_db):
    """Test that items without external IDs try title fallback."""
    session = tmp_db

    item = _make_item(session, "No IDs Paper")
    session.commit()

    with patch("app.connectors.semantic_scholar.search_s2_by_title", return_value=[]):
        result = build_citations_from_metadata(session, items=[item])
    # Processed but no hits (title search returned nothing)
    assert result["processed"] == 1
    assert result["api_misses"] == 1


def test_build_citations_arxiv_match(tmp_db):
    """Test matching via arXiv ID."""
    session = tmp_db

    src = _make_item(session, "Source arXiv", ext_ids={"arxiv": "2401.12345"})
    dst = _make_item(session, "Target arXiv", ext_ids={"arxiv": "2401.67890"})
    session.commit()

    mock_refs = [
        {
            "paperId": "def456",
            "title": "Target arXiv",
            "externalIds": {"ArXiv": "2401.67890"},
        }
    ]

    with patch("app.connectors.semantic_scholar.get_references", return_value=mock_refs):
        result = build_citations_from_metadata(session, items=[src])

    assert result["citations_added"] == 1
    cit = session.query(Citation).filter_by(src_item_id=src.id, dst_item_id=dst.id).first()
    assert cit is not None


def test_build_citations_no_self_citation(tmp_db):
    """Test that self-citations are not created."""
    session = tmp_db

    item = _make_item(session, "Self Ref Paper", ext_ids={"doi": "10.1234/self"})
    session.commit()

    mock_refs = [
        {
            "paperId": "self1",
            "title": "Self Ref Paper",
            "externalIds": {"DOI": "10.1234/self"},
        }
    ]

    with patch("app.connectors.semantic_scholar.get_references", return_value=mock_refs):
        result = build_citations_from_metadata(session, items=[item])

    assert result["citations_added"] == 0


def test_build_citations_api_miss(tmp_db):
    """Test handling when S2 API returns no references."""
    session = tmp_db

    item = _make_item(session, "No Refs", ext_ids={"doi": "10.1234/norefs"})
    session.commit()

    with patch("app.connectors.semantic_scholar.get_references", return_value=[]):
        result = build_citations_from_metadata(session, items=[item])

    assert result["api_misses"] == 1
    assert result["citations_added"] == 0
