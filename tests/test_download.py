"""Tests for PDF download pipeline."""

import json
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from app.core.models import Item, ItemId, Job
from app.pipelines.downloader import download_pdf_for_item, download_pdfs, get_pdf_url


def _make_acl_item(session, title="Test Paper", item_id_val="2024.acl-long.1"):
    """Helper to create an ACL item."""
    item = Item(
        title=title,
        type="paper",
        year=2024,
        source_url=f"https://aclanthology.org/{item_id_val}",
    )
    session.add(item)
    session.flush()
    ext_id = ItemId(item_id=item.id, id_type="acl", id_value=item_id_val)
    session.add(ext_id)
    session.flush()
    return item


def test_get_pdf_url_acl(tmp_db):
    """ACL items should get PDF URL as source_url + '.pdf'."""
    item = _make_acl_item(tmp_db)
    url = get_pdf_url(item, tmp_db)
    assert url == "https://aclanthology.org/2024.acl-long.1.pdf"


def test_get_pdf_url_generic_pdf(tmp_db):
    """Generic items with .pdf source_url should use it directly."""
    item = Item(title="Generic", source_url="https://example.com/paper.pdf")
    tmp_db.add(item)
    tmp_db.flush()
    url = get_pdf_url(item, tmp_db)
    assert url == "https://example.com/paper.pdf"


def test_get_pdf_url_none(tmp_db):
    """Items without source_url should return None."""
    item = Item(title="No URL")
    tmp_db.add(item)
    tmp_db.flush()
    url = get_pdf_url(item, tmp_db)
    assert url is None


def test_download_idempotent(tmp_db, tmp_path):
    """Items with pdf_path already set should be skipped."""
    item = _make_acl_item(tmp_db)
    # Create a fake PDF file
    pdf_dir = tmp_path / "data" / "library" / "papers" / str(item.id)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    fake_pdf = pdf_dir / "source.pdf"
    fake_pdf.write_bytes(b"%PDF-fake")
    item.pdf_path = str(fake_pdf.relative_to(tmp_path))
    tmp_db.flush()

    result = download_pdf_for_item(tmp_db, item)
    assert result is False  # skipped


@patch("app.pipelines.downloader.requests.get")
def test_download_success(mock_get, tmp_db, tmp_path):
    """Successful download should create file and set pdf_path."""
    item = _make_acl_item(tmp_db)

    # Mock response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"%PDF-1.4 fake content"]
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    result = download_pdf_for_item(tmp_db, item)
    assert result is True
    assert item.pdf_path is not None
    assert "source.pdf" in item.pdf_path


@patch("app.pipelines.downloader.requests.get")
def test_download_failure_recorded(mock_get, tmp_db):
    """Failed downloads should record a Job with status=failed."""
    item = _make_acl_item(tmp_db)

    mock_get.side_effect = Exception("Connection timeout")

    result = download_pdfs(tmp_db, [item], sleep_sec=0)
    assert result["failed"] == 1
    assert result["downloaded"] == 0

    jobs = tmp_db.execute(select(Job).where(Job.job_type == "download_pdf", Job.status == "failed")).scalars().all()
    assert len(jobs) == 1
    payload = json.loads(jobs[0].payload_json)
    assert payload["item_id"] == item.id


@patch("app.pipelines.downloader.requests.get")
def test_download_batch(mock_get, tmp_db, tmp_path):
    """Batch download should process multiple items."""
    items = []
    for i in range(3):
        items.append(_make_acl_item(tmp_db, f"Paper {i}", f"2024.acl-long.{i+1}"))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_content.return_value = [b"%PDF-1.4 fake"]
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    result = download_pdfs(tmp_db, items, sleep_sec=0)
    assert result["downloaded"] == 3
    assert result["skipped"] == 0
    assert result["failed"] == 0
