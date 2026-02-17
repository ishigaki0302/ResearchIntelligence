"""Tests for import idempotency and core functionality."""

import textwrap

from app.core.bibtex import (
    generate_bibtex_key,
    normalize_name,
    parse_author_string,
    parse_bibtex_string,
)
from app.core.service import upsert_item


class TestBibtexParsing:
    def test_parse_author_string(self):
        result = parse_author_string("Smith, John and Jane Doe and Bob Johnson")
        assert result == ["John Smith", "Jane Doe", "Bob Johnson"]

    def test_parse_author_last_first(self):
        result = parse_author_string("Wang, Wei and Li, Ming")
        assert result == ["Wei Wang", "Ming Li"]

    def test_normalize_name(self):
        assert normalize_name("José García") == "jose garcia"
        assert normalize_name("  John   Smith  ") == "john smith"

    def test_generate_bibtex_key_basic(self):
        key = generate_bibtex_key(["John Smith"], 2024, "Long Context Transformers")
        assert key == "smith2024long"

    def test_generate_bibtex_key_collision(self):
        existing = {"smith2024long"}
        key = generate_bibtex_key(["John Smith"], 2024, "Long Context Transformers", existing)
        assert key == "smith2024longa"

    def test_generate_bibtex_key_no_authors(self):
        key = generate_bibtex_key([], 2024, "Some Paper")
        assert key == "unknown2024some"

    def test_parse_bibtex_string(self):
        bib = textwrap.dedent("""\
            @inproceedings{smith2024test,
              title = {Test Paper on NLP},
              author = {Smith, John and Doe, Jane},
              year = {2024},
              booktitle = {ACL},
            }
        """)
        entries = parse_bibtex_string(bib)
        assert len(entries) == 1
        assert entries[0]["title"] == "Test Paper on NLP"
        assert entries[0]["ID"] == "smith2024test"


class TestImportIdempotency:
    def test_upsert_creates_new_item(self, tmp_db):
        session = tmp_db
        item, created = upsert_item(
            session,
            title="Test Paper",
            authors=["Alice Smith"],
            year=2024,
            venue="ACL",
            bibtex_key="smith2024test",
        )
        session.commit()
        assert created is True
        assert item.id is not None
        assert item.title == "Test Paper"
        assert item.bibtex_key == "smith2024test"

    def test_upsert_idempotent_by_bibtex_key(self, tmp_db):
        session = tmp_db
        item1, c1 = upsert_item(
            session,
            title="Test Paper",
            authors=["Alice Smith"],
            year=2024,
            bibtex_key="smith2024test",
        )
        session.commit()

        item2, c2 = upsert_item(
            session,
            title="Test Paper",
            authors=["Alice Smith"],
            year=2024,
            bibtex_key="smith2024test",
        )
        session.commit()

        assert c1 is True
        assert c2 is False
        assert item1.id == item2.id

    def test_upsert_idempotent_by_external_id(self, tmp_db):
        session = tmp_db
        item1, c1 = upsert_item(
            session,
            title="Paper A",
            authors=["Bob"],
            year=2024,
            external_ids={"acl": "2024.acl-long.1"},
        )
        session.commit()

        item2, c2 = upsert_item(
            session,
            title="Paper A",
            authors=["Bob"],
            year=2024,
            external_ids={"acl": "2024.acl-long.1"},
        )
        session.commit()

        assert c1 is True
        assert c2 is False
        assert item1.id == item2.id

    def test_upsert_idempotent_by_title_year(self, tmp_db):
        session = tmp_db
        item1, c1 = upsert_item(
            session,
            title="Exact Same Title",
            year=2024,
        )
        session.commit()

        item2, c2 = upsert_item(
            session,
            title="Exact Same Title",
            year=2024,
        )
        session.commit()

        assert c1 is True
        assert c2 is False
        assert item1.id == item2.id

    def test_upsert_updates_missing_fields(self, tmp_db):
        session = tmp_db
        item1, _ = upsert_item(
            session,
            title="Paper X",
            year=2024,
            bibtex_key="x2024paper",
        )
        session.commit()
        assert item1.abstract is None

        item2, created = upsert_item(
            session,
            title="Paper X",
            year=2024,
            bibtex_key="x2024paper",
            abstract="This is the abstract.",
        )
        session.commit()

        assert created is False
        assert item2.abstract == "This is the abstract."

    def test_note_created_on_upsert(self, tmp_db):
        session = tmp_db
        item, _ = upsert_item(
            session,
            title="Paper With Note",
            year=2024,
        )
        session.commit()

        assert len(item.notes) == 1
        assert item.notes[0].title == "main"
