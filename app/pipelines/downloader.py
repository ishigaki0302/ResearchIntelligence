"""PDF download pipeline for fetching paper PDFs."""

import json
import logging
import time
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config, resolve_path
from app.core.models import Item, ItemId, Job

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
DEFAULT_USER_AGENT = "ResearchIntelligence/0.4"


def _get_download_config() -> dict:
    cfg = get_config()
    return cfg.get("download", {})


def get_pdf_url(item: Item, session: Session) -> str | None:
    """Resolve PDF URL from source_url or item_ids.

    ACL papers: source_url + '.pdf'
    Generic: if source_url ends with .pdf, use directly.
    """
    # Check if item has an ACL ID → ACL pattern
    acl_id = session.execute(
        select(ItemId).where(ItemId.item_id == item.id, ItemId.id_type == "acl")
    ).scalar_one_or_none()

    if acl_id and item.source_url:
        return item.source_url.rstrip("/") + ".pdf"

    # Generic: source_url ending in .pdf
    if item.source_url:
        if item.source_url.rstrip("/").endswith(".pdf"):
            return item.source_url
        # Try ACL-style even without explicit acl_id if it looks like aclanthology
        if "aclanthology.org" in item.source_url:
            return item.source_url.rstrip("/") + ".pdf"

    return None


def download_pdf_for_item(session: Session, item: Item, dest_dir: Path | None = None) -> bool:
    """Download PDF for a single item.

    Returns True if downloaded, False if skipped.
    Raises on failure (caller should handle).
    """
    # Skip if already has PDF
    if item.pdf_path:
        pdf_file = resolve_path(item.pdf_path)
        if pdf_file.exists():
            return False

    pdf_url = get_pdf_url(item, session)
    if not pdf_url:
        raise ValueError(f"Cannot determine PDF URL for item {item.id}")

    dl_cfg = _get_download_config()
    timeout = dl_cfg.get("timeout", DEFAULT_TIMEOUT)
    user_agent = dl_cfg.get("user_agent", DEFAULT_USER_AGENT)

    # Determine destination
    if dest_dir is None:
        cfg = get_config()
        lib_dir = resolve_path(cfg["storage"]["library_dir"])
        dest_dir = lib_dir / str(item.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / "source.pdf"

    # Skip if file already on disk (idempotent)
    if dest_file.exists():
        item.pdf_path = str(dest_file.relative_to(resolve_path(".")))
        session.flush()
        return False

    # Download with streaming
    resp = requests.get(
        pdf_url,
        timeout=timeout,
        stream=True,
        headers={"User-Agent": user_agent},
    )
    resp.raise_for_status()

    with open(dest_file, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    # Update item
    item.pdf_path = str(dest_file.relative_to(resolve_path(".")))
    session.flush()
    logger.info(f"Downloaded PDF for item {item.id}: {pdf_url}")
    return True


def download_pdfs(
    session: Session,
    items: list[Item],
    max_workers: int = 4,
    sleep_sec: float = 1.0,
) -> dict:
    """Download PDFs for a batch of items.

    Uses sequential downloading with sleep to be polite to servers.
    Returns {"downloaded": int, "skipped": int, "failed": int}.
    """
    downloaded = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(items):
        did_download = False
        try:
            did_download = download_pdf_for_item(session, item)
            if did_download:
                downloaded += 1
                logger.info(f"[{i+1}/{len(items)}] Downloaded: {item.title[:60]}")
            else:
                skipped += 1
                logger.debug(f"[{i+1}/{len(items)}] Skipped: {item.title[:60]}")
        except Exception as e:
            failed += 1
            logger.warning(f"[{i+1}/{len(items)}] Failed: {item.title[:60]} — {e}")
            # Record failure as Job
            job = Job(
                job_type="download_pdf",
                status="failed",
                payload_json=json.dumps({"item_id": item.id}),
                error=str(e),
            )
            session.add(job)
            session.flush()

        # Sleep between downloads (not after skip)
        if did_download:
            time.sleep(sleep_sec)

    session.commit()
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}
