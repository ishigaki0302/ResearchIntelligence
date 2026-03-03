"""Tests for corpus PDF ingest pipeline (Issue #57)."""

from pathlib import Path

import pytest

from app.pipelines.corpus_ingest import ingest_directory, ingest_pdf


def _make_fake_pdf(path: Path) -> Path:
    """Write a minimal but valid 1-page PDF so pdfplumber can open it."""
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 44>>
stream
BT /F1 12 Tf 100 700 Td (Hello World) Tj ET
endstream
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000348 00000 n

trailer<</Size 6/Root 1 0 R>>
startxref
444
%%EOF"""
    path.write_bytes(content)
    return path


class TestIngestPdf:
    def test_creates_item_for_new_pdf(self, tmp_db, tmp_path):
        pdf = _make_fake_pdf(tmp_path / "paper1.pdf")
        item, status = ingest_pdf(pdf, tmp_db)
        tmp_db.commit()

        assert status == "created"
        assert item is not None
        assert item.id is not None
        assert item.pdf_path == str(pdf.resolve())
        assert item.type == "corpus"

    def test_skips_duplicate_pdf(self, tmp_db, tmp_path):
        pdf = _make_fake_pdf(tmp_path / "paper_dup.pdf")
        item1, s1 = ingest_pdf(pdf, tmp_db)
        tmp_db.commit()
        item2, s2 = ingest_pdf(pdf, tmp_db)
        tmp_db.commit()

        assert s1 == "created"
        assert s2 == "skipped"
        assert item1.id == item2.id

    def test_broken_pdf_returns_failed(self, tmp_db, tmp_path):
        bad = tmp_path / "broken.pdf"
        bad.write_bytes(b"not a pdf")
        _item, status = ingest_pdf(bad, tmp_db)

        assert status == "failed"


class TestIngestDirectory:
    def test_empty_directory(self, tmp_db, tmp_path):
        result = ingest_directory(tmp_path, tmp_db, show_progress=False)
        assert result == {"created": 0, "skipped": 0, "failed": 0, "total": 0}

    def test_ingests_multiple_pdfs(self, tmp_db, tmp_path):
        for i in range(3):
            _make_fake_pdf(tmp_path / f"paper{i}.pdf")

        result = ingest_directory(tmp_path, tmp_db, show_progress=False)

        assert result["total"] == 3
        assert result["created"] == 3
        assert result["skipped"] == 0

    def test_idempotent_second_run(self, tmp_db, tmp_path):
        for i in range(2):
            _make_fake_pdf(tmp_path / f"paper{i}.pdf")

        r1 = ingest_directory(tmp_path, tmp_db, show_progress=False)
        r2 = ingest_directory(tmp_path, tmp_db, show_progress=False)

        assert r1["created"] == 2
        assert r2["created"] == 0
        assert r2["skipped"] == 2

    def test_missing_directory_raises(self, tmp_db, tmp_path):
        with pytest.raises(FileNotFoundError):
            ingest_directory(tmp_path / "nonexistent", tmp_db, show_progress=False)

    def test_broken_pdf_counted_as_failed(self, tmp_db, tmp_path):
        bad = tmp_path / "broken.pdf"
        bad.write_bytes(b"not a pdf")

        result = ingest_directory(tmp_path, tmp_db, show_progress=False)

        assert result["failed"] == 1
        assert result["created"] == 0
