"""Batch download NLP2026 PDFs and extract text.

Covers Issue #55.

Usage:
    python scripts/download_nlp2026_pdfs.py [--workers N] [--limit N] [--skip-extract]
"""

import argparse
import csv
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import resolve_path
from app.core.db import get_session, init_db
from app.core.models import Item, ItemId
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://anlp.jp"
LIBRARY_DIR = "data/library/papers"
HEADERS = {"User-Agent": "ResearchIntelligence/0.9 (research purpose)"}


def get_nlp2026_items(session) -> list[Item]:
    """Fetch all NLP2026 items from DB."""
    rows = session.execute(
        select(Item, ItemId.id_value)
        .join(ItemId, (ItemId.item_id == Item.id) & (ItemId.id_type == "nlp2026"))
        .where(Item.venue_instance == "NLP2026", Item.status == "active")
        .order_by(ItemId.id_value)
    ).all()
    return [(item, session_id) for item, session_id in rows]


def download_pdf(item: Item, session_id: str, timeout: int = 30) -> dict:
    """Download PDF for one item. Returns status dict."""
    if item.pdf_path:
        pdf_file = resolve_path(item.pdf_path)
        if pdf_file.exists():
            return {"session_id": session_id, "item_id": item.id, "status": "already_exists",
                    "path": str(pdf_file)}

    pdf_url = item.source_url or f"{BASE_URL}/proceedings/annual_meeting/2026/pdf_dir/{session_id}.pdf"

    dest_dir = resolve_path(LIBRARY_DIR) / str(item.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "paper.pdf"

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=timeout, stream=True)
        resp.raise_for_status()

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)

        size = dest.stat().st_size
        if size < 1024:
            dest.unlink()
            return {"session_id": session_id, "item_id": item.id, "status": "too_small",
                    "url": pdf_url, "size": size}

        rel_path = str(dest.relative_to(resolve_path(".")))
        return {"session_id": session_id, "item_id": item.id, "status": "ok",
                "path": rel_path, "size": size}

    except requests.HTTPError as e:
        return {"session_id": session_id, "item_id": item.id, "status": f"http_error_{e.response.status_code}",
                "url": pdf_url}
    except Exception as e:
        return {"session_id": session_id, "item_id": item.id, "status": "error", "error": str(e),
                "url": pdf_url}


def extract_text(item: Item, pdf_path: str, session) -> bool:
    """Extract text from PDF and save to text_path."""
    try:
        from app.pipelines.extract import extract_pdf_text

        text = extract_pdf_text(resolve_path(pdf_path))
        if not text.strip():
            return False

        text_dir = resolve_path(LIBRARY_DIR) / str(item.id)
        text_dir.mkdir(parents=True, exist_ok=True)
        text_file = text_dir / "paper.txt"
        text_file.write_text(text, encoding="utf-8")

        rel_text = str(text_file.relative_to(resolve_path(".")))
        item.text_path = rel_text

        # Extract abstract-like content (first 1000 chars after stripping header)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        # Find abstract section
        abstract_lines = []
        in_abstract = False
        for line in lines:
            lower = line.lower()
            if "abstract" in lower or "概要" in line or "はじめに" in line:
                in_abstract = True
                continue
            if in_abstract:
                if any(kw in lower for kw in ["introduction", "1 ", "1.", "keywords", "1は"]):
                    break
                abstract_lines.append(line)
                if len(" ".join(abstract_lines)) > 800:
                    break

        if abstract_lines and not item.abstract:
            item.abstract = " ".join(abstract_lines)[:1000]

        return True
    except Exception as e:
        logger.debug(f"Text extraction failed for item {item.id}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="NLP2026 PDF batch downloader")
    parser.add_argument("--workers", type=int, default=8, help="Parallel download workers")
    parser.add_argument("--limit", type=int, default=0, help="Max papers (0=all)")
    parser.add_argument("--skip-extract", action="store_true", help="Skip text extraction")
    parser.add_argument("--skip-index", action="store_true", help="Skip FAISS index rebuild")
    parser.add_argument("--output", default="data/nlp2026_download_report.csv", help="Output CSV path")
    args = parser.parse_args()

    init_db()
    session = get_session()

    pairs = get_nlp2026_items(session)
    if args.limit:
        pairs = pairs[:args.limit]

    print(f"NLP2026 論文: {len(pairs)} 件を処理")

    # Parallel download
    results = []
    success = already = failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_pdf, item, sid): (item, sid) for item, sid in pairs}

        for i, future in enumerate(as_completed(futures), 1):
            item, sid = futures[future]
            res = future.result()
            results.append(res)

            status = res["status"]
            if status == "ok":
                success += 1
                # Update DB
                item.pdf_path = res["path"]
                session.flush()
            elif status == "already_exists":
                already += 1
            else:
                failed += 1

            if i % 50 == 0 or i == len(pairs):
                print(f"  [{i}/{len(pairs)}] 成功:{success} 既存:{already} 失敗:{failed}")

            # Polite crawl
            time.sleep(0.5)

    session.commit()

    # Text extraction
    if not args.skip_extract:
        print("\nテキスト抽出中...")
        extracted = 0
        items_with_pdf = [(item, sid) for item, sid in pairs
                          if item.pdf_path and resolve_path(item.pdf_path).exists()
                          and not item.text_path]
        for i, (item, sid) in enumerate(items_with_pdf, 1):
            if extract_text(item, item.pdf_path, session):
                extracted += 1
            if i % 50 == 0:
                session.commit()
                print(f"  [{i}/{len(items_with_pdf)}] 抽出済み:{extracted}")

        session.commit()
        print(f"  テキスト抽出完了: {extracted}/{len(items_with_pdf)} 件")

    session.close()

    # Save report
    report_path = resolve_path(args.output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["session_id", "item_id", "status", "path", "size", "url", "error"])
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in writer.fieldnames})

    print(f"\n=== 完了 ===")
    print(f"  ダウンロード成功: {success} 件")
    print(f"  既存スキップ: {already} 件")
    print(f"  失敗: {failed} 件")
    print(f"  レポート: {report_path}")

    # Rebuild index
    if not args.skip_index and (success + already) > 0:
        print("\nインデックス再構築中...")
        from app.core.db import get_session as gs
        from app.indexing.engine import rebuild_fts, rebuild_faiss

        s2 = gs()
        rebuild_fts(s2)
        rebuild_faiss(s2)
        s2.close()
        print("  FTS5 / FAISS インデックス更新完了")


if __name__ == "__main__":
    main()
