"""Tests for P18 — Notes/citation workflow."""

from app.core.config import resolve_path
from app.core.models import Citation, Note
from app.core.service import _render_note_template, ensure_note, upsert_item


def test_note_template_rendering(tmp_db):
    """Test that template placeholders are replaced."""
    session = tmp_db

    # Create a template file
    template_path = resolve_path("configs/note_template.md")
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(
        "# {{title}}\n**Authors:** {{authors}}\n**Year:** {{year}}\n"
        "**Venue:** {{venue}}\n**Key:** `{{bibtex_key}}`\n",
        encoding="utf-8",
    )

    # Set up config to point to template
    import app.core.config as config_mod

    config_mod.get_config._cache["notes"] = {"template_path": "configs/note_template.md"}

    item, _ = upsert_item(
        session,
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year=2017,
        venue="NeurIPS",
        bibtex_key="vaswani2017attention",
    )
    session.commit()

    rendered = _render_note_template(item)
    assert "Attention Is All You Need" in rendered
    assert "Ashish Vaswani" in rendered
    assert "2017" in rendered
    assert "vaswani2017attention" in rendered


def test_note_template_fallback(tmp_db):
    """Test fallback when no template configured."""
    session = tmp_db

    import app.core.config as config_mod

    # Ensure no notes config
    config_mod.get_config._cache.pop("notes", None)

    item, _ = upsert_item(
        session,
        title="Test Paper",
        authors=["Author A"],
        year=2024,
    )
    session.commit()

    rendered = _render_note_template(item)
    assert "# Test Paper" in rendered
    assert "## Summary" in rendered


def test_ensure_note_no_overwrite(tmp_db):
    """Test that ensure_note doesn't overwrite existing note file."""
    session = tmp_db

    item, _ = upsert_item(
        session,
        title="Test Paper",
        authors=["Author A"],
        year=2024,
    )
    session.commit()

    # Get the note and modify file
    note = session.execute(__import__("sqlalchemy").select(Note).where(Note.item_id == item.id)).scalar_one()
    note_path = resolve_path(note.path)
    note_path.write_text("My custom notes", encoding="utf-8")

    # Call ensure_note again — should return existing, not overwrite
    note2 = ensure_note(session, item)
    assert note2.id == note.id
    assert note_path.read_text(encoding="utf-8") == "My custom notes"


def test_mentioned_in_notes(tmp_db):
    """Test detection of @citekey in notes."""
    session = tmp_db

    # Create two items
    item_a, _ = upsert_item(
        session,
        title="Paper A",
        authors=["Author A"],
        year=2024,
        bibtex_key="paperA2024",
    )
    item_b, _ = upsert_item(
        session,
        title="Paper B",
        authors=["Author B"],
        year=2024,
        bibtex_key="paperB2024",
    )
    session.commit()

    # Write @paperA2024 in paper B's note
    note_b = session.execute(__import__("sqlalchemy").select(Note).where(Note.item_id == item_b.id)).scalar_one()
    note_path = resolve_path(note_b.path)
    note_path.write_text("This relates to @paperA2024 work.\n", encoding="utf-8")

    # Scan for mentions
    pattern = f"@{item_a.bibtex_key}"
    from sqlalchemy import select

    all_notes = session.execute(select(Note)).scalars().all()
    mentioning_ids = []
    for note in all_notes:
        if note.item_id == item_a.id:
            continue
        path = resolve_path(note.path)
        if path.exists() and pattern in path.read_text(encoding="utf-8"):
            mentioning_ids.append(note.item_id)

    assert item_b.id in mentioning_ids


def test_manual_resolve_citation(tmp_db):
    """Test manual citation resolution sets dst_item_id."""
    session = tmp_db

    item_src, _ = upsert_item(
        session,
        title="Source Paper",
        authors=["Author S"],
        year=2024,
        bibtex_key="src2024",
    )
    item_dst, _ = upsert_item(
        session,
        title="Destination Paper",
        authors=["Author D"],
        year=2023,
        bibtex_key="dst2023",
    )

    cit = Citation(
        src_item_id=item_src.id,
        dst_item_id=None,
        raw_cite="Some reference text",
        dst_key="unknown_key",
        source="bibtex",
    )
    session.add(cit)
    session.flush()

    # Manually resolve
    cit.dst_item_id = item_dst.id
    session.flush()

    assert cit.dst_item_id == item_dst.id
