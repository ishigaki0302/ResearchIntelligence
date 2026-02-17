"""FastAPI web server for the research index UI."""

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.core.config import resolve_path
from app.core.db import get_session, init_db
from app.core.models import Citation, Collection, InboxItem, Item, ItemId, Job, Note, Tag, Watch
from app.core.service import add_tag_to_item, list_tags_for_item, remove_tag_from_item
from app.graph.citations import get_citation_subgraph
from app.indexing.engine import hybrid_search

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Research Intelligence")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    session = get_session()
    try:
        item_count = session.execute(select(func.count(Item.id))).scalar()
        recent = session.execute(select(Item).order_by(Item.created_at.desc()).limit(10)).scalars().all()

        collections = session.execute(select(Collection).order_by(Collection.created_at.desc())).scalars().all()

        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "item_count": item_count,
                "recent_items": recent,
                "collections": collections,
            },
        )
    finally:
        session.close()


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = Query(""),
    year: Optional[str] = Query(None),
    venue: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    item_type: Optional[str] = Query(None, alias="type"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
):
    session = get_session()
    try:
        all_results = []
        if q:
            filters = {}
            if year:
                if ":" in year:
                    parts = year.split(":")
                    if parts[0]:
                        filters["year_from"] = int(parts[0])
                    if parts[1]:
                        filters["year_to"] = int(parts[1])
                else:
                    filters["year_from"] = int(year)
                    filters["year_to"] = int(year)
            if venue:
                filters["venue"] = venue
            if tag:
                filters["tag"] = tag
            if item_type:
                filters["type"] = item_type

            try:
                all_results = hybrid_search(
                    session,
                    q,
                    top_k=500,
                    filters=filters if filters else None,
                    scope="both",
                )
            except Exception:
                all_results = []

        total = len(all_results)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        results = all_results[start : start + per_page]

        all_tags = session.execute(select(Tag).order_by(Tag.name)).scalars().all()

        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "query": q,
                "results": results,
                "year": year or "",
                "venue": venue or "",
                "tag": tag or "",
                "all_tags": all_tags,
                "item_type": item_type or "",
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            },
        )
    finally:
        session.close()


@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_detail(request: Request, item_id: int):
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            return HTMLResponse("Item not found", status_code=404)

        # Get notes
        notes = session.execute(select(Note).where(Note.item_id == item_id)).scalars().all()

        note_contents = {}
        for note in notes:
            note_path = resolve_path(note.path)
            if note_path.exists():
                note_contents[note.id] = note_path.read_text(encoding="utf-8")
            else:
                note_contents[note.id] = ""

        # Get tags
        tags = list_tags_for_item(session, item_id)

        # Get citation subgraph
        graph = get_citation_subgraph(session, item_id)

        return templates.TemplateResponse(
            "item_detail.html",
            {
                "request": request,
                "item": item,
                "notes": notes,
                "note_contents": note_contents,
                "tags": tags,
                "graph": graph,
            },
        )
    finally:
        session.close()


@app.get("/item/{item_id}/pdf")
def item_pdf(item_id: int):
    """Serve the PDF file for an item."""
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item or not item.pdf_path:
            return HTMLResponse("PDF not found", status_code=404)
        pdf_file = resolve_path(item.pdf_path)
        if not pdf_file.exists():
            return HTMLResponse("PDF file missing", status_code=404)
        return FileResponse(
            pdf_file,
            media_type="application/pdf",
            filename=f"{item.bibtex_key or item_id}.pdf",
        )
    finally:
        session.close()


@app.post("/item/{item_id}/note/{note_id}/save")
def save_note(item_id: int, note_id: int, content: str = Form(...)):
    session = get_session()
    try:
        note = session.get(Note, note_id)
        if not note or note.item_id != item_id:
            return HTMLResponse("Note not found", status_code=404)

        note_path = resolve_path(note.path)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(content, encoding="utf-8")
        session.commit()

        return RedirectResponse(f"/item/{item_id}", status_code=303)
    finally:
        session.close()


@app.get("/graph/{item_id}", response_class=HTMLResponse)
def graph_view(request: Request, item_id: int, depth: int = Query(1)):
    session = get_session()
    try:
        graph = get_citation_subgraph(session, item_id, depth=depth)
        item = session.get(Item, item_id)
        return templates.TemplateResponse(
            "graph.html",
            {
                "request": request,
                "item": item,
                "graph": graph,
                "graph_json": json.dumps(graph),
                "depth": depth,
            },
        )
    finally:
        session.close()


@app.post("/item/{item_id}/tag")
def add_tag(item_id: int, tag_name: str = Form(...)):
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            return HTMLResponse("Item not found", status_code=404)
        add_tag_to_item(session, item_id, tag_name)
        session.commit()
        return RedirectResponse(f"/item/{item_id}", status_code=303)
    finally:
        session.close()


@app.post("/item/{item_id}/tag/{tag_name}/delete")
def delete_tag(item_id: int, tag_name: str):
    session = get_session()
    try:
        remove_tag_from_item(session, item_id, tag_name)
        session.commit()
        return RedirectResponse(f"/item/{item_id}", status_code=303)
    finally:
        session.close()


@app.get("/api/tags/suggest")
def suggest_tags(q: str = Query("")):
    session = get_session()
    try:
        if not q:
            return JSONResponse([])
        tags = session.execute(select(Tag).where(Tag.name.contains(q)).limit(10)).scalars().all()
        return JSONResponse([t.name for t in tags])
    finally:
        session.close()


@app.get("/watches", response_class=HTMLResponse)
def watches_page(request: Request):
    session = get_session()
    try:
        watches = session.execute(select(Watch).order_by(Watch.created_at.desc())).scalars().all()
        return templates.TemplateResponse(
            "watches.html",
            {
                "request": request,
                "watches": watches,
            },
        )
    finally:
        session.close()


@app.post("/watches")
def create_watch(
    name: str = Form(...),
    source: str = Form(...),
    query: str = Form(...),
    category: str = Form(""),
):
    import json as _json

    session = get_session()
    try:
        filters = {}
        if category:
            filters["category"] = category
        watch = Watch(
            name=name,
            source=source.lower(),
            query=query,
            filters_json=_json.dumps(filters) if filters else None,
        )
        session.add(watch)
        session.commit()
        return RedirectResponse("/watches", status_code=303)
    finally:
        session.close()


@app.post("/watches/{watch_id}/toggle")
def toggle_watch(watch_id: int):
    session = get_session()
    try:
        watch = session.get(Watch, watch_id)
        if not watch:
            return HTMLResponse("Watch not found", status_code=404)
        watch.enabled = not watch.enabled
        session.commit()
        return RedirectResponse("/watches", status_code=303)
    finally:
        session.close()


@app.post("/watches/{watch_id}/run")
def run_watch_web(watch_id: int):
    from app.pipelines.watch import run_watch

    session = get_session()
    try:
        watch = session.get(Watch, watch_id)
        if not watch:
            return HTMLResponse("Watch not found", status_code=404)
        run_watch(session, watch, since_days=14, limit=100)
        session.commit()
        return RedirectResponse("/inbox", status_code=303)
    finally:
        session.close()


@app.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, status: str = Query("new")):
    session = get_session()
    try:
        query = select(InboxItem).order_by(InboxItem.discovered_at.desc())
        if status == "recommended":
            query = query.where(InboxItem.recommended.is_(True), InboxItem.status == "new")
        elif status == "auto-accept":
            query = query.where(InboxItem.auto_accept.is_(True), InboxItem.status == "new")
        elif status != "all":
            query = query.where(InboxItem.status == status)
        items = session.execute(query).scalars().all()

        watches = session.execute(select(Watch)).scalars().all()

        return templates.TemplateResponse(
            "inbox.html",
            {
                "request": request,
                "inbox_items": items,
                "watches": watches,
                "current_status": status,
            },
        )
    finally:
        session.close()


@app.post("/inbox/recommend")
def recommend_inbox_web():
    from app.pipelines.inbox_recommend import recommend_inbox_items

    session = get_session()
    try:
        recommend_inbox_items(session)
        return RedirectResponse("/inbox?status=recommended", status_code=303)
    finally:
        session.close()


@app.post("/inbox/auto-accept")
def auto_accept_inbox_web():
    from app.pipelines.auto_accept import evaluate_auto_accept

    session = get_session()
    try:
        evaluate_auto_accept(session)
        return RedirectResponse("/inbox?status=auto-accept", status_code=303)
    finally:
        session.close()


@app.post("/inbox/{inbox_id}/accept")
def accept_inbox_web(inbox_id: int, apply_tags: str = Form("")):
    from app.pipelines.watch import accept_inbox_item

    session = get_session()
    try:
        inbox_item = session.get(InboxItem, inbox_id)
        if not inbox_item:
            return HTMLResponse("Inbox item not found", status_code=404)
        item = accept_inbox_item(session, inbox_item)
        if apply_tags and item:
            from app.pipelines.inbox_recommend import apply_auto_tags_on_accept

            apply_auto_tags_on_accept(session, inbox_item, item)
        session.commit()
        return RedirectResponse("/inbox", status_code=303)
    finally:
        session.close()


@app.post("/inbox/{inbox_id}/reject")
def reject_inbox_web(inbox_id: int):
    session = get_session()
    try:
        inbox_item = session.get(InboxItem, inbox_id)
        if not inbox_item:
            return HTMLResponse("Inbox item not found", status_code=404)
        inbox_item.status = "rejected"
        session.commit()
        return RedirectResponse("/inbox", status_code=303)
    finally:
        session.close()


@app.get("/api/item/{item_id}/similar")
def similar_items_api(item_id: int, top_k: int = Query(5)):
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            return JSONResponse([], status_code=404)

        query_text = f"{item.title or ''} {item.abstract or ''}".strip()
        if not query_text:
            return JSONResponse([])

        from app.indexing.engine import search_faiss

        results = search_faiss(query_text, top_k=top_k + 1)
        similar = []
        for r in results:
            if r.get("type") == "item" and r["id"] != item_id:
                sim_item = session.get(Item, r["id"])
                if sim_item:
                    similar.append(
                        {
                            "id": sim_item.id,
                            "title": sim_item.title,
                            "year": sim_item.year,
                            "venue": sim_item.venue or "",
                            "score": round(r["vector_score"], 3),
                        }
                    )
                if len(similar) >= top_k:
                    break
        return JSONResponse(similar)
    finally:
        session.close()


@app.get("/api/item/{item_id}/mentioned-in-notes")
def mentioned_in_notes_api(item_id: int):
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item or not item.bibtex_key:
            return JSONResponse([])

        # Scan all notes for @bibtex_key
        pattern = f"@{item.bibtex_key}"
        all_notes = session.execute(select(Note)).scalars().all()
        mentioning = []
        for note in all_notes:
            if note.item_id == item_id:
                continue
            note_path = resolve_path(note.path)
            if note_path.exists():
                content = note_path.read_text(encoding="utf-8")
                if pattern in content:
                    note_item = session.get(Item, note.item_id)
                    if note_item:
                        mentioning.append(
                            {
                                "id": note_item.id,
                                "title": note_item.title,
                                "year": note_item.year,
                            }
                        )
        return JSONResponse(mentioning)
    finally:
        session.close()


@app.post("/api/item/{item_id}/download-and-extract")
def download_and_extract_api(item_id: int):
    """Download PDF, extract text, extract references, and resolve citations."""
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            return JSONResponse({"error": "Item not found"}, status_code=404)

        result = {"item_id": item_id, "steps": {}}

        # Step 1: Download PDF
        try:
            from app.pipelines.downloader import download_pdf_for_item

            downloaded = download_pdf_for_item(session, item)
            session.commit()
            result["steps"]["download"] = {"ok": True, "downloaded": downloaded}
        except Exception as e:
            result["steps"]["download"] = {"ok": False, "error": str(e)}
            return JSONResponse(result)

        # Step 2: Extract text
        try:
            from app.pipelines.extract import extract_text_for_item

            extracted = extract_text_for_item(item, session)
            session.commit()
            result["steps"]["extract_text"] = {"ok": True, "extracted": extracted}
        except Exception as e:
            result["steps"]["extract_text"] = {"ok": False, "error": str(e)}

        # Step 3: Extract references
        try:
            from app.pipelines.references import extract_references_for_item

            entries = extract_references_for_item(session, item)
            session.commit()
            result["steps"]["extract_references"] = {"ok": True, "count": len(entries)}
        except Exception as e:
            result["steps"]["extract_references"] = {"ok": False, "error": str(e)}

        # Step 4: Resolve citations
        try:
            from app.graph.citations import resolve_citations

            res = resolve_citations(session)
            result["steps"]["resolve"] = {"ok": True, **res}
        except Exception as e:
            result["steps"]["resolve"] = {"ok": False, "error": str(e)}

        return JSONResponse(result)
    finally:
        session.close()


@app.post("/api/citation/{citation_id}/resolve")
def resolve_citation_api(citation_id: int, target_item_id: int = Form(...)):
    session = get_session()
    try:
        citation = session.get(Citation, citation_id)
        if not citation:
            return JSONResponse({"error": "Citation not found"}, status_code=404)
        target = session.get(Item, target_item_id)
        if not target:
            return JSONResponse({"error": "Target item not found"}, status_code=404)
        citation.dst_item_id = target_item_id
        session.commit()
        return JSONResponse({"ok": True, "citation_id": citation_id, "dst_item_id": target_item_id})
    finally:
        session.close()


@app.post("/api/citation/{citation_id}/import")
def import_citation_api(citation_id: int):
    """Create a new Item from an unresolved citation's S2 metadata and resolve the citation."""
    session = get_session()
    try:
        citation = session.get(Citation, citation_id)
        if not citation:
            return JSONResponse({"error": "Citation not found"}, status_code=404)
        if citation.dst_item_id:
            return JSONResponse({"error": "Citation already resolved", "item_id": citation.dst_item_id}, status_code=400)

        # Parse S2 metadata from context
        ctx = {}
        if citation.context:
            try:
                ctx = json.loads(citation.context)
            except (json.JSONDecodeError, TypeError):
                pass

        title = ctx.get("title") or citation.raw_cite or ""
        if not title:
            return JSONResponse({"error": "No title available"}, status_code=400)

        ext_ids = ctx.get("external_ids", {})

        # Check if item already exists by external IDs
        for id_type, s2_key in [("doi", "DOI"), ("arxiv", "ArXiv"), ("acl", "ACL")]:
            if ext_ids.get(s2_key):
                existing = session.execute(
                    select(ItemId).where(ItemId.id_type == id_type, ItemId.id_value == ext_ids[s2_key])
                ).scalar_one_or_none()
                if existing:
                    citation.dst_item_id = existing.item_id
                    session.commit()
                    return JSONResponse({"ok": True, "item_id": existing.item_id, "action": "resolved_existing"})

        # Create new item
        new_item = Item(
            title=title.strip(),
            type="paper",
            source_url=f"https://doi.org/{ext_ids['DOI']}" if ext_ids.get("DOI") else None,
        )
        session.add(new_item)
        session.flush()

        # Add external IDs
        for id_type, s2_key in [("doi", "DOI"), ("arxiv", "ArXiv"), ("acl", "ACL")]:
            if ext_ids.get(s2_key):
                iid = ItemId(item_id=new_item.id, id_type=id_type, id_value=ext_ids[s2_key])
                session.add(iid)
        s2_pid = ctx.get("s2_paper_id") or (str(ext_ids["CorpusId"]) if ext_ids.get("CorpusId") else None)
        if s2_pid:
            iid = ItemId(item_id=new_item.id, id_type="s2", id_value=s2_pid)
            session.add(iid)

        # Resolve the citation
        citation.dst_item_id = new_item.id
        session.commit()

        return JSONResponse({"ok": True, "item_id": new_item.id, "title": title, "action": "created"})
    finally:
        session.close()


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request):
    session = get_session()
    try:
        jobs = session.execute(select(Job).order_by(Job.created_at.desc()).limit(50)).scalars().all()
        return templates.TemplateResponse(
            "jobs.html",
            {
                "request": request,
                "jobs": jobs,
            },
        )
    finally:
        session.close()


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request):
    from app.analytics.trends import (
        items_by_year_collection,
        items_by_year_tag,
        items_by_year_venue,
        top_keyphrases_by_year,
        watch_collection_growth,
    )

    session = get_session()
    try:
        data = {
            "year_venue": items_by_year_venue(session),
            "year_collection": items_by_year_collection(session),
            "year_tag": items_by_year_tag(session),
            "watch_growth": watch_collection_growth(session),
            "keyphrases": top_keyphrases_by_year(session, top_n=15),
        }

        # Clustering (catch errors gracefully)
        cluster_data = None
        try:
            from app.analytics.clustering import cluster_items

            cluster_data = cluster_items(session, n_clusters=5)
        except Exception:
            pass

        # Citation network
        network_data = None
        try:
            from app.analytics.network import analyze_citation_network

            network_data = analyze_citation_network(session)
        except Exception:
            pass

        return templates.TemplateResponse(
            "analytics.html",
            {
                "request": request,
                "data": data,
                "cluster_data": cluster_data,
                "network_data": network_data,
            },
        )
    finally:
        session.close()


@app.get("/collections", response_class=HTMLResponse)
def collections_list(request: Request):
    session = get_session()
    try:
        collections = session.execute(select(Collection).order_by(Collection.created_at.desc())).scalars().all()
        return templates.TemplateResponse(
            "collections.html",
            {
                "request": request,
                "collections": collections,
            },
        )
    finally:
        session.close()
