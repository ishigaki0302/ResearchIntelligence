"""Tests for P19 — dedup detect & merge."""

from app.core.models import Citation, CollectionItem, Item, ItemAuthor
from app.core.service import add_tag_to_item, get_or_create_author, get_or_create_collection, upsert_item
from app.pipelines.dedup import detect_duplicates, merge_items


def _make_item(session, title, authors, year, bibtex_key=None, doi=None):
    item, _ = upsert_item(
        session,
        title=title,
        authors=authors,
        year=year,
        bibtex_key=bibtex_key,
        external_ids={"doi": doi} if doi else None,
    )
    return item


def test_detect_by_title_year_author(tmp_db):
    """Items with same title+year+first_author should be detected."""
    session = tmp_db
    # Create two items with same title, year, and first author but different bibtex keys
    item_a = _make_item(session, "Exact Same Title", ["Alice Smith"], 2024, bibtex_key="dup2024a")
    item_b_raw = Item(
        type="paper",
        title="Exact Same Title",
        year=2024,
        bibtex_key="dup2024b",
        status="active",
    )
    session.add(item_b_raw)
    session.flush()
    # Add same first author
    author = get_or_create_author(session, "Alice Smith")
    session.add(ItemAuthor(item_id=item_b_raw.id, author_id=author.id, position=0))
    session.flush()

    dupes = detect_duplicates(session)
    found = [d for d in dupes if d["method"] == "title_year_author"]
    assert len(found) >= 1
    pair = found[0]
    assert {pair["item_a_id"], pair["item_b_id"]} == {item_a.id, item_b_raw.id}


def test_detect_by_title_year_no_author(tmp_db):
    """Items with same title+year but no authors should still match if first_author is empty."""
    session = tmp_db
    item_a_raw = Item(type="paper", title="No Author Paper", year=2024, bibtex_key="noauth2024a", status="active")
    item_b_raw = Item(type="paper", title="No Author Paper", year=2024, bibtex_key="noauth2024b", status="active")
    session.add_all([item_a_raw, item_b_raw])
    session.flush()

    dupes = detect_duplicates(session)
    found = [d for d in dupes if d["method"] == "title_year_author"]
    assert len(found) >= 1


def test_merge_dry_run(tmp_db):
    """Dry-run merge should not modify any data."""
    session = tmp_db
    src = _make_item(session, "Source Paper", ["Alice"], 2024, bibtex_key="src2024")
    dst = _make_item(session, "Dest Paper", ["Bob"], 2024, bibtex_key="dst2024")
    add_tag_to_item(session, src.id, "test-tag")
    session.flush()

    result = merge_items(session, src.id, dst.id, dry_run=True)
    assert result["dry_run"] is True
    assert result["moved"]["tags"] >= 1

    # Verify nothing actually changed
    refreshed_src = session.get(Item, src.id)
    assert refreshed_src.status != "merged"


def test_merge_apply(tmp_db):
    """Full merge should transfer entities and mark source as merged."""
    session = tmp_db
    src = _make_item(session, "Source Paper", ["Alice"], 2024, bibtex_key="src2024m")
    dst = _make_item(session, "Dest Paper", ["Bob"], 2024, bibtex_key="dst2024m")

    # Add tag to source
    add_tag_to_item(session, src.id, "transfer-tag")

    # Add collection membership to source
    coll = get_or_create_collection(session, "test-coll")
    session.add(CollectionItem(collection_id=coll.id, item_id=src.id))

    # Add citation from source
    cit = Citation(src_item_id=src.id, dst_key="some_key", source="bibtex")
    session.add(cit)
    session.flush()

    result = merge_items(session, src.id, dst.id, dry_run=False)
    session.flush()

    assert result["dry_run"] is False
    assert result["moved"]["tags"] >= 1
    assert result["moved"]["collections"] >= 1
    assert result["moved"]["citations_out"] >= 1

    # Verify source is marked as merged
    refreshed_src = session.get(Item, src.id)
    assert refreshed_src.status == "merged"
    assert refreshed_src.merged_into_id == dst.id


def test_merge_preserves_dst_data(tmp_db):
    """Merge should not overwrite destination data."""
    session = tmp_db
    src = _make_item(session, "Source", ["A"], 2024, bibtex_key="srcp2024")
    dst = _make_item(session, "Dest", ["B"], 2024, bibtex_key="dstp2024")

    # Both have the same tag — should not duplicate
    add_tag_to_item(session, src.id, "shared-tag")
    add_tag_to_item(session, dst.id, "shared-tag")
    session.flush()

    result = merge_items(session, src.id, dst.id, dry_run=False)
    session.flush()

    # shared-tag already on dst, so tags transferred should be 0
    assert result["moved"]["tags"] == 0

    # Dst title should be unchanged
    refreshed_dst = session.get(Item, dst.id)
    assert refreshed_dst.title == "Dest"
