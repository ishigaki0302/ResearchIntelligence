"""NLP2026 proceedings scraper and importer.

Fetches https://anlp.jp/proceedings/annual_meeting/2026/ and imports all papers into the DB.
"""

import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Project root setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import get_session, init_db
from app.core.service import upsert_item

BASE_URL = "https://anlp.jp"
PROCEEDINGS_URL = f"{BASE_URL}/proceedings/annual_meeting/2026/"
VENUE = "NLP"
VENUE_INSTANCE = "NLP2026"
YEAR = 2026


def fetch_page(url: str) -> str:
    headers = {"User-Agent": "ResearchIntelligence/0.9 (research purpose)"}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_authors(author_text: str) -> list[str]:
    """Parse author string like '○山田太郎, 鈴木花子 (所属A), 田中一郎 (所属B)' into names."""
    # Remove presenter marker ○ and ◊
    text = re.sub(r"[○◊◎]", "", author_text).strip()
    # Remove affiliation info in parentheses
    text = re.sub(r"\([^)]*\)", "", text)
    # Split by comma or Japanese comma
    parts = re.split(r"[,、，]", text)
    authors = [p.strip() for p in parts if p.strip()]
    return authors


def parse_session_type(session_id: str) -> str:
    """Map session ID prefix to type tag."""
    prefix = re.match(r"^([A-Z]+)", session_id)
    if not prefix:
        return "general"
    p = prefix.group(1)
    mapping = {
        "A": "oral",
        "B": "oral",
        "C": "oral",
        "P": "poster",
        "Q": "poster",
        "TS": "theme-session",
        "T": "tutorial",
        "I": "invited",
    }
    return mapping.get(p, "general")


def scrape_papers(html: str) -> list[dict]:
    """Parse the proceedings page and return list of paper dicts.

    HTML structure:
      <table>
        <tr>
          <td class="pid"><span id="B1-1">B1-1</span></td>
          <td><span class="title">タイトル</span></td>
        </tr>
        <tr>
          <td><a href="/proceedings/.../B1-1.pdf"><img ...></a></td>
          <td>○著者名 (所属)</td>
        </tr>
      </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    papers = []
    seen_ids = set()

    session_pattern = re.compile(r"^[A-Z]{1,2}\d+-\d+$")

    for span in soup.find_all("span", id=session_pattern):
        session_id = span.get("id", "")
        if session_id in seen_ids:
            continue
        seen_ids.add(session_id)

        # The span is inside <td class="pid"> which is in a <tr>
        pid_td = span.find_parent("td")
        if not pid_td:
            continue
        title_row = pid_td.find_parent("tr")
        if not title_row:
            continue

        # Title is in the sibling <td> of the same row
        title_td = pid_td.find_next_sibling("td")
        title_span = title_td.find("span", class_="title") if title_td else None
        title = title_span.get_text(strip=True) if title_span else None
        if not title_td and not title:
            title = title_td.get_text(strip=True) if title_td else None
        if not title:
            continue

        # Author/PDF row is the next <tr>
        author_row = title_row.find_next_sibling("tr")
        pdf_url = None
        author_line = None

        if author_row:
            # Find PDF link
            pdf_a = author_row.find("a", href=re.compile(r"\.pdf", re.I))
            if pdf_a:
                href = pdf_a["href"]
                pdf_url = href if href.startswith("http") else BASE_URL + href

            # Find author text (td without PDF link)
            for td in author_row.find_all("td"):
                text = td.get_text(strip=True)
                if "○" in text or "◊" in text or "◎" in text:
                    author_line = text
                    break

        authors = parse_authors(author_line) if author_line else []

        papers.append(
            {
                "session_id": session_id,
                "title": title,
                "authors": authors,
                "pdf_url": pdf_url or f"{BASE_URL}/proceedings/annual_meeting/2026/pdf_dir/{session_id}.pdf",
                "session_type": parse_session_type(session_id),
            }
        )

    return papers


def import_papers(papers: list[dict]) -> dict:
    init_db()
    session = get_session()
    imported = 0
    skipped = 0
    errors = 0

    try:
        for paper in papers:
            try:
                tags = [
                    VENUE_INSTANCE,
                    f"session/{paper['session_type']}",
                    f"nlp2026/{paper['session_id'].split('-')[0]}",  # e.g. nlp2026/B1
                ]

                bibtex_key = f"NLP2026_{paper['session_id'].replace('-', '_')}"

                _item, created = upsert_item(
                    session,
                    item_type="paper",
                    title=paper["title"],
                    authors=paper["authors"],
                    year=YEAR,
                    venue=VENUE,
                    venue_instance=VENUE_INSTANCE,
                    source_url=paper["pdf_url"],
                    bibtex_key=bibtex_key,
                    external_ids={"nlp2026": paper["session_id"]},
                    tags=tags,
                )

                if created:
                    imported += 1
                else:
                    skipped += 1

                if (imported + skipped) % 50 == 0:
                    session.commit()
                    print(f"  進捗: {imported + skipped}/{len(papers)} 件処理済み (新規: {imported}, スキップ: {skipped})")

            except Exception as e:
                errors += 1
                print(f"  ERROR [{paper['session_id']}] {paper['title'][:40]}: {e}")
                session.rollback()

        session.commit()
    finally:
        session.close()

    return {"imported": imported, "skipped": skipped, "errors": errors, "total": len(papers)}


def main():
    print("NLP2026 proceedings ページを取得中...")
    html = fetch_page(PROCEEDINGS_URL)
    print(f"  取得完了 ({len(html):,} bytes)")

    print("論文エントリーを解析中...")
    papers = scrape_papers(html)
    print(f"  {len(papers)} 件の論文を検出")

    if not papers:
        print("論文が見つかりませんでした。HTMLの構造を確認してください。")
        sys.exit(1)

    # Show first few for confirmation
    print("\n--- 検出サンプル (最初の5件) ---")
    for p in papers[:5]:
        print(f"  [{p['session_id']}] {p['title'][:60]}")
        print(f"    著者: {', '.join(p['authors'][:3])}")
        print(f"    PDF: {p['pdf_url']}")
    print()

    print("DBにインポート中...")
    result = import_papers(papers)

    print(f"\n=== 完了 ===")
    print(f"  新規登録: {result['imported']} 件")
    print(f"  スキップ (既存): {result['skipped']} 件")
    print(f"  エラー: {result['errors']} 件")
    print(f"  合計処理: {result['total']} 件")


if __name__ == "__main__":
    main()
