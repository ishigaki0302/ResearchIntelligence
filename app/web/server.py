"""FastAPI web server for the research index UI."""

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.core.config import resolve_path
from app.core.db import get_session, init_db
from app.core.models import Collection, InboxItem, Item, Note, Tag, Watch
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

        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "query": q,
                "results": results,
                "year": year or "",
                "venue": venue or "",
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
        return templates.TemplateResponse(
            "analytics.html",
            {
                "request": request,
                "data": data,
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
