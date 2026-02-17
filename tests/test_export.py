"""Tests for BibTeX export stability."""

import textwrap

from app.core.service import upsert_item
from app.pipelines.exporter import export_bibtex


class TestBibtexExport:
    def test_export_basic(self, tmp_db, tmp_path):
        session = tmp_db
        item, _ = upsert_item(
            session,
            title="Test Export Paper",
            authors=["Alice Smith", "Bob Jones"],
            year=2024,
            venue="ACL",
            venue_instance="ACL 2024",
            bibtex_key="smith2024test",
            abstract="A test abstract.",
        )
        session.commit()

        output = tmp_path / "output.bib"
        result = export_bibtex(session, output_path=str(output))

        assert result["count"] == 1
        assert output.exists()
        content = output.read_text()
        assert "smith2024test" in content
        assert "Test Export Paper" in content
        assert "Alice Smith" in content

    def test_export_with_raw_bibtex(self, tmp_db, tmp_path):
        session = tmp_db
        raw = textwrap.dedent("""\
            @inproceedings{doe2024raw,
              title = {Raw BibTeX Entry},
              author = {Doe, Jane},
              year = {2024},
              booktitle = {EMNLP},
            }""")
        item, _ = upsert_item(
            session,
            title="Raw BibTeX Entry",
            authors=["Jane Doe"],
            year=2024,
            bibtex_key="doe2024raw",
            bibtex_raw=raw,
        )
        session.commit()

        output = tmp_path / "output.bib"
        export_bibtex(session, output_path=str(output))

        content = output.read_text()
        assert "@inproceedings{doe2024raw," in content
        assert "EMNLP" in content

    def test_export_stable_across_runs(self, tmp_db, tmp_path):
        """Export should produce the same output when run twice."""
        session = tmp_db
        for i in range(3):
            upsert_item(
                session,
                title=f"Paper {i}",
                authors=[f"Author {i}"],
                year=2024,
                bibtex_key=f"author2024paper{i}",
            )
        session.commit()

        out1 = tmp_path / "out1.bib"
        out2 = tmp_path / "out2.bib"
        export_bibtex(session, output_path=str(out1))
        export_bibtex(session, output_path=str(out2))

        assert out1.read_text() == out2.read_text()

    def test_export_filtered_by_year(self, tmp_db, tmp_path):
        session = tmp_db
        upsert_item(session, title="Old Paper", year=2020, bibtex_key="old2020")
        upsert_item(session, title="New Paper", year=2024, bibtex_key="new2024")
        session.commit()

        output = tmp_path / "filtered.bib"
        result = export_bibtex(
            session,
            output_path=str(output),
            filters={"year_from": 2024, "year_to": 2024},
        )

        content = output.read_text()
        assert "new2024" in content
        assert "old2020" not in content
        assert result["count"] == 1
