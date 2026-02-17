"""CLI entry point for the research intelligence (ri) tool."""

import logging
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="ri",
    help="Research Intelligence — local paper management CLI",
    add_completion=False,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)


tag_app = typer.Typer(name="tag", help="Manage tags on items")
app.add_typer(tag_app)

watch_app = typer.Typer(name="watch", help="Manage paper watches")
app.add_typer(watch_app)

inbox_app = typer.Typer(name="inbox", help="Review discovered papers")
app.add_typer(inbox_app)

analytics_app = typer.Typer(name="analytics", help="Trend analytics")
app.add_typer(analytics_app)


@tag_app.command("add")
def tag_add(
    item_id: int = typer.Argument(..., help="Item ID"),
    tag_name: str = typer.Argument(..., help="Tag name (e.g. method/RAG)"),
):
    """Add a tag to an item."""
    from app.core.db import get_session, init_db
    from app.core.models import Item
    from app.core.service import add_tag_to_item

    init_db()
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            typer.echo(f"Item {item_id} not found", err=True)
            raise typer.Exit(1)
        add_tag_to_item(session, item_id, tag_name)
        session.commit()
        typer.echo(f"Tagged item {item_id} with '{tag_name}'")
    finally:
        session.close()


@tag_app.command("rm")
def tag_rm(
    item_id: int = typer.Argument(..., help="Item ID"),
    tag_name: str = typer.Argument(..., help="Tag name to remove"),
):
    """Remove a tag from an item."""
    from app.core.db import get_session, init_db
    from app.core.models import Item
    from app.core.service import remove_tag_from_item

    init_db()
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            typer.echo(f"Item {item_id} not found", err=True)
            raise typer.Exit(1)
        removed = remove_tag_from_item(session, item_id, tag_name)
        session.commit()
        if removed:
            typer.echo(f"Removed tag '{tag_name}' from item {item_id}")
        else:
            typer.echo(f"Tag '{tag_name}' not found on item {item_id}")
    finally:
        session.close()


@tag_app.command("ls")
def tag_ls(
    item_id: int = typer.Argument(..., help="Item ID"),
):
    """List tags on an item."""
    from app.core.db import get_session, init_db
    from app.core.models import Item
    from app.core.service import list_tags_for_item

    init_db()
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            typer.echo(f"Item {item_id} not found", err=True)
            raise typer.Exit(1)
        tags = list_tags_for_item(session, item_id)
        if tags:
            for t in tags:
                typer.echo(t)
        else:
            typer.echo("(no tags)")
    finally:
        session.close()


@app.command("import")
def import_cmd(
    spec: str = typer.Argument(
        ..., help="Import spec, e.g. acl:2024{main,findings}, bib:/path, pdf:/path, url:https://..."
    ),
    title: Optional[str] = typer.Option(None, "--title", help="Title (for pdf/url imports)"),
    year: Optional[int] = typer.Option(None, "--year", help="Year (for pdf/url imports)"),
    item_type: str = typer.Option("blog", "--type", help="Item type for url imports"),
):
    """Import papers from various sources."""
    from app.core.db import init_db
    from app.pipelines.importer import import_acl, import_bibtex, import_pdf, import_url, parse_import_spec

    init_db()

    parsed = parse_import_spec(spec)
    src_type = parsed["type"]
    args = parsed["args"]

    if src_type == "acl":
        typer.echo(
            f"Importing from ACL Anthology: {args['event'].upper()} {args['year']} volumes={args.get('volumes', 'all')}"
        )
        result = import_acl(**args)
        typer.echo(f"Done: {result['imported']} imported, {result['skipped']} skipped, {result['total']} total")
        typer.echo(f"Collection: {result['collection']}")

    elif src_type == "bib":
        typer.echo(f"Importing BibTeX: {args['path']}")
        result = import_bibtex(args["path"])
        typer.echo(f"Done: {result['imported']} imported, {result['skipped']} skipped, {result['total']} total")

    elif src_type == "pdf":
        typer.echo(f"Importing PDF: {args['path']}")
        result = import_pdf(args["path"], title=title, year=year)
        typer.echo(
            f"{'Created' if result['created'] else 'Already exists'}: {result['title']} (id={result['item_id']})"
        )

    elif src_type == "url":
        typer.echo(f"Importing URL: {args['url']}")
        result = import_url(args["url"], item_type=item_type, title=title, year=year)
        typer.echo(
            f"{'Created' if result['created'] else 'Already exists'}: {result['title']} (id={result['item_id']})"
        )

    else:
        typer.echo(f"Unknown import type: {src_type}", err=True)
        raise typer.Exit(1)


@app.command()
def index(
    chunks: bool = typer.Option(False, "--chunks", help="Also chunk texts and build chunk FAISS index"),
):
    """Rebuild search indices (FTS5 + FAISS)."""
    from app.core.db import get_session, init_db
    from app.indexing.engine import rebuild_index
    from app.pipelines.extract import extract_all

    init_db()
    session = get_session()

    try:
        typer.echo("Extracting text from items...")
        ext_result = extract_all(session)
        typer.echo(f"  Extracted: {ext_result['extracted']}, Failed: {ext_result['failed']}")

        if chunks:
            from app.indexing.chunker import chunk_all_items

            typer.echo("Chunking item texts...")
            chunk_result = chunk_all_items(session)
            typer.echo(
                f"  Chunked: {chunk_result['chunked']}, "
                f"Skipped: {chunk_result['skipped']}, Failed: {chunk_result['failed']}"
            )

        typer.echo("Building indices...")
        rebuild_index(session, include_chunks=chunks)
        typer.echo("Index rebuild complete.")
    finally:
        session.close()


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    top_k: int = typer.Option(20, "--top-k", "-k", help="Number of results"),
    year: Optional[str] = typer.Option(None, "--year", help="Year filter, e.g. 2023 or 2023:2024"),
    venue: Optional[str] = typer.Option(None, "--venue", help="Venue filter"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Tag filter"),
    item_type: Optional[str] = typer.Option(None, "--type", help="Item type filter"),
    scope: str = typer.Option("item", "--scope", help="Search scope: item, chunk, or both"),
):
    """Search the paper index."""
    from app.core.db import get_session, init_db
    from app.indexing.engine import hybrid_search

    init_db()
    session = get_session()

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
        results = hybrid_search(session, query, top_k=top_k, filters=filters if filters else None, scope=scope)

        if not results:
            typer.echo("No results found.")
            return

        typer.echo(f"\n{'='*80}")
        typer.echo(f"Search results for: {query}")
        typer.echo(f"{'='*80}\n")

        for i, r in enumerate(results, 1):
            item = r.get("item")
            if not item:
                continue
            score = r["score"]
            authors = ", ".join(item.author_names[:3])
            if len(item.author_names) > 3:
                authors += " et al."
            typer.echo(f"[{i}] {item.title}")
            typer.echo(f"    Authors: {authors}")
            typer.echo(
                f"    Year: {item.year or '?'} | Venue: {item.venue_instance or item.venue or '?'} | Score: {score:.3f}"
            )
            if r.get("snippet"):
                typer.echo(f"    Snippet: {r['snippet'][:200]}")
            if r.get("matched_chunks"):
                typer.echo(f"    Chunk hits: {len(r['matched_chunks'])}")
                for mc in r["matched_chunks"][:2]:
                    typer.echo(f"      [{mc['score']:.3f}] {mc['text'][:120]}...")
            typer.echo(f"    Key: {item.bibtex_key}")
            typer.echo()
    finally:
        session.close()


@app.command("export-bib")
def export_bib(
    output: str = typer.Option("export.bib", "--output", "-o", help="Output .bib file path"),
    venue: Optional[str] = typer.Option(None, "--venue", help="Filter by venue"),
    year: Optional[str] = typer.Option(None, "--year", help="Filter by year or range"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filter by tag"),
    collection: Optional[str] = typer.Option(None, "--collection", help="Filter by collection name"),
):
    """Export items as a .bib file."""
    from app.core.db import get_session, init_db
    from app.pipelines.exporter import export_bibtex

    init_db()
    session = get_session()

    filters = {}
    if venue:
        filters["venue"] = venue
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
    if tag:
        filters["tag"] = tag
    if collection:
        filters["collection"] = collection

    try:
        result = export_bibtex(session, output_path=output, filters=filters if filters else None)
        typer.echo(f"Exported {result['count']} entries to {output}")
    finally:
        session.close()


@app.command("extract-references")
def extract_references(
    limit: Optional[int] = typer.Option(None, "--limit", help="Max items to process"),
    item_id: Optional[int] = typer.Option(None, "--id", help="Process a single item"),
):
    """Extract references from paper text and create citation links."""
    from app.core.db import get_session, init_db
    from app.core.models import Item
    from app.graph.citations import resolve_citations
    from app.pipelines.references import extract_all_references, extract_references_for_item

    init_db()
    session = get_session()

    try:
        if item_id:
            item = session.get(Item, item_id)
            if not item:
                typer.echo(f"Item {item_id} not found", err=True)
                raise typer.Exit(1)
            entries = extract_references_for_item(session, item)
            session.commit()
            typer.echo(f"Extracted {len(entries)} references from: {item.title[:80]}")
        else:
            result = extract_all_references(session, limit=limit)
            typer.echo(
                f"Done: {result['extracted']} items processed, {result['skipped']} skipped, {result['failed']} failed"
            )

        # Resolve citations
        typer.echo("Resolving citations...")
        res = resolve_citations(session)
        typer.echo(f"Resolved {res['resolved']} citations, {res['remaining']} remaining unresolved")
    finally:
        session.close()


@app.command("download-pdf")
def download_pdf(
    collection: Optional[str] = typer.Option(None, "--collection", help="Filter by collection name"),
    max_items: Optional[int] = typer.Option(None, "--max", help="Max items to download"),
    workers: int = typer.Option(4, "--workers", help="Parallel workers (currently sequential)"),
    failed_only: bool = typer.Option(False, "--failed-only", help="Retry only previously failed downloads"),
    item_id: Optional[int] = typer.Option(None, "--id", help="Download PDF for a single item"),
):
    """Download PDFs for items in the database."""
    import json

    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Collection, CollectionItem, Item, Job
    from app.pipelines.downloader import download_pdf_for_item, download_pdfs

    init_db()
    session = get_session()

    try:
        if item_id:
            item = session.get(Item, item_id)
            if not item:
                typer.echo(f"Item {item_id} not found", err=True)
                raise typer.Exit(1)
            try:
                result = download_pdf_for_item(session, item)
                session.commit()
                if result:
                    typer.echo(f"Downloaded PDF for: {item.title[:80]}")
                else:
                    typer.echo(f"Skipped (already exists): {item.title[:80]}")
            except Exception as e:
                typer.echo(f"Failed: {e}", err=True)
                raise typer.Exit(1)
            return

        # Build query
        query = select(Item)

        if failed_only:
            # Get item IDs from failed download jobs
            failed_jobs = (
                session.execute(select(Job).where(Job.job_type == "download_pdf", Job.status == "failed"))
                .scalars()
                .all()
            )
            failed_ids = []
            for job in failed_jobs:
                payload = json.loads(job.payload_json) if job.payload_json else {}
                if "item_id" in payload:
                    failed_ids.append(payload["item_id"])
            if not failed_ids:
                typer.echo("No failed downloads to retry.")
                return
            query = query.where(Item.id.in_(failed_ids))
            typer.echo(f"Retrying {len(failed_ids)} failed downloads...")
        else:
            # Only items without PDF
            query = query.where(Item.pdf_path.is_(None))

        if collection:
            coll = session.execute(select(Collection).where(Collection.name == collection)).scalar_one_or_none()
            if not coll:
                typer.echo(f"Collection '{collection}' not found", err=True)
                raise typer.Exit(1)
            coll_item_ids = (
                session.execute(select(CollectionItem.item_id).where(CollectionItem.collection_id == coll.id))
                .scalars()
                .all()
            )
            query = query.where(Item.id.in_(coll_item_ids))

        items = session.execute(query).scalars().all()

        if max_items:
            items = items[:max_items]

        if not items:
            typer.echo("No items to download.")
            return

        typer.echo(f"Downloading PDFs for {len(items)} items...")
        from app.core.config import get_config

        cfg = get_config()
        dl_cfg = cfg.get("download", {})
        sleep_sec = dl_cfg.get("sleep_sec", 1.0)

        result = download_pdfs(session, items, max_workers=workers, sleep_sec=sleep_sec)
        typer.echo(f"Done: {result['downloaded']} downloaded, {result['skipped']} skipped, {result['failed']} failed")
    finally:
        session.close()


@app.command()
def enrich(
    collection: Optional[str] = typer.Option(None, "--collection", help="Filter by collection name"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max items to enrich"),
    item_id: Optional[int] = typer.Option(None, "--id", help="Enrich a single item"),
    update_metadata: bool = typer.Option(False, "--update-metadata", help="Update title/year from API"),
):
    """Enrich items with external IDs from OpenAlex and Semantic Scholar."""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Collection, CollectionItem, Item
    from app.pipelines.enricher import enrich_item, enrich_items

    init_db()
    session = get_session()

    try:
        if item_id:
            item = session.get(Item, item_id)
            if not item:
                typer.echo(f"Item {item_id} not found", err=True)
                raise typer.Exit(1)
            result = enrich_item(session, item, update_metadata=update_metadata)
            session.commit()
            if result["ids_added"]:
                typer.echo(f"Enriched: +{result['ids_added']} (source: {result['source']})")
            else:
                typer.echo("No new IDs found.")
            return

        query = select(Item)
        if collection:
            coll = session.execute(select(Collection).where(Collection.name == collection)).scalar_one_or_none()
            if not coll:
                typer.echo(f"Collection '{collection}' not found", err=True)
                raise typer.Exit(1)
            coll_item_ids = (
                session.execute(select(CollectionItem.item_id).where(CollectionItem.collection_id == coll.id))
                .scalars()
                .all()
            )
            query = query.where(Item.id.in_(coll_item_ids))

        items = session.execute(query).scalars().all()
        if limit:
            items = items[:limit]

        if not items:
            typer.echo("No items to enrich.")
            return

        typer.echo(f"Enriching {len(items)} items...")
        result = enrich_items(session, items, update_metadata=update_metadata)
        typer.echo(
            f"Done: {result['enriched']} enriched, {result['skipped']} skipped, "
            f"{result['failed']} failed, {result['ids_added']} IDs added"
        )
    finally:
        session.close()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", help="Port to listen on"),
):
    """Start the web UI server."""
    import uvicorn

    from app.core.db import init_db

    init_db()
    uvicorn.run("app.web.server:app", host=host, port=port, reload=True)


@app.command()
def stats():
    """Show database statistics."""
    from sqlalchemy import func, select

    from app.core.db import get_session, init_db
    from app.core.models import Author, Citation, Collection, Item, Note

    init_db()
    session = get_session()

    try:
        item_count = session.execute(select(func.count(Item.id))).scalar()
        author_count = session.execute(select(func.count(Author.id))).scalar()
        note_count = session.execute(select(func.count(Note.id))).scalar()
        coll_count = session.execute(select(func.count(Collection.id))).scalar()
        cite_count = session.execute(select(func.count(Citation.id))).scalar()

        typer.echo(f"Items:       {item_count}")
        typer.echo(f"Authors:     {author_count}")
        typer.echo(f"Notes:       {note_count}")
        typer.echo(f"Collections: {coll_count}")
        typer.echo(f"Citations:   {cite_count}")

        # Breakdown by type
        type_counts = session.execute(select(Item.type, func.count(Item.id)).group_by(Item.type)).all()
        if type_counts:
            typer.echo("\nBy type:")
            for t, c in type_counts:
                typer.echo(f"  {t}: {c}")

        # Breakdown by venue
        venue_counts = session.execute(
            select(Item.venue_instance, func.count(Item.id))
            .where(Item.venue_instance.is_not(None))
            .group_by(Item.venue_instance)
            .order_by(func.count(Item.id).desc())
            .limit(10)
        ).all()
        if venue_counts:
            typer.echo("\nTop venues:")
            for v, c in venue_counts:
                typer.echo(f"  {v}: {c}")
    finally:
        session.close()


@watch_app.command("add")
def watch_add(
    name: str = typer.Option(..., "--name", help="Watch name (unique)"),
    source: str = typer.Option(..., "--source", help="Source: arxiv or openalex"),
    query: str = typer.Option(..., "--query", help="Search query"),
    category: Optional[str] = typer.Option(None, "--category", help="arXiv category (e.g. cs.CL)"),
):
    """Add a new watch."""
    import json

    from app.core.db import get_session, init_db
    from app.core.models import Watch

    init_db()
    session = get_session()
    try:
        filters = {}
        if category:
            filters["category"] = category

        watch = Watch(
            name=name,
            source=source.lower(),
            query=query,
            filters_json=json.dumps(filters) if filters else None,
        )
        session.add(watch)
        session.commit()
        typer.echo(f"Created watch '{name}' (source={source}, query={query})")
    finally:
        session.close()


@watch_app.command("list")
def watch_list():
    """List all watches."""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Watch

    init_db()
    session = get_session()
    try:
        watches = session.execute(select(Watch).order_by(Watch.created_at.desc())).scalars().all()
        if not watches:
            typer.echo("No watches.")
            return
        for w in watches:
            status = "enabled" if w.enabled else "disabled"
            typer.echo(f"[{w.id}] {w.name} ({w.source}) query={w.query!r} [{status}]")
    finally:
        session.close()


@watch_app.command("run")
def watch_run(
    name: Optional[str] = typer.Option(None, "--name", help="Run a specific watch by name"),
    since: str = typer.Option("7d", "--since", help="Look back period (e.g. 7d, 14d)"),
    limit: int = typer.Option(100, "--limit", help="Max results per watch"),
):
    """Run watches to discover new papers."""
    import re

    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Watch
    from app.pipelines.watch import run_watch

    # Parse since
    m = re.match(r"^(\d+)d$", since)
    since_days = int(m.group(1)) if m else 7

    init_db()
    session = get_session()
    try:
        query = select(Watch).where(Watch.enabled.is_(True))
        if name:
            query = query.where(Watch.name == name)
        watches = session.execute(query).scalars().all()

        if not watches:
            typer.echo("No matching watches found.")
            return

        for w in watches:
            typer.echo(f"Running watch '{w.name}' ({w.source})...")
            result = run_watch(session, w, since_days=since_days, limit=limit)
            session.commit()
            typer.echo(f"  Fetched: {result['fetched']}, Added: {result['added']}, Skipped: {result['skipped']}")
    finally:
        session.close()


@inbox_app.command("list")
def inbox_list(
    status: str = typer.Option("new", "--status", help="Filter by status: new, accepted, rejected, all"),
):
    """List inbox items."""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import InboxItem

    init_db()
    session = get_session()
    try:
        query = select(InboxItem).order_by(InboxItem.discovered_at.desc())
        if status != "all":
            query = query.where(InboxItem.status == status)

        items = session.execute(query).scalars().all()
        if not items:
            typer.echo(f"No inbox items (status={status}).")
            return

        for it in items:
            typer.echo(f"[{it.id}] [{it.status}] {it.title[:80]}")
            typer.echo(f"     Source: {it.source_id_type}:{it.source_id_value} | Year: {it.year or '?'}")
    finally:
        session.close()


@inbox_app.command("accept")
def inbox_accept(
    inbox_id: int = typer.Argument(..., help="Inbox item ID to accept"),
):
    """Accept an inbox item into the main library."""
    from app.core.db import get_session, init_db
    from app.core.models import InboxItem
    from app.pipelines.watch import accept_inbox_item

    init_db()
    session = get_session()
    try:
        inbox_item = session.get(InboxItem, inbox_id)
        if not inbox_item:
            typer.echo(f"Inbox item {inbox_id} not found", err=True)
            raise typer.Exit(1)
        if inbox_item.status == "accepted":
            typer.echo(f"Already accepted (item_id={inbox_item.accepted_item_id})")
            return

        item = accept_inbox_item(session, inbox_item)
        session.commit()
        typer.echo(f"Accepted: {item.title[:80]} (item_id={item.id})")
    finally:
        session.close()


@inbox_app.command("recommend")
def inbox_recommend(
    threshold: float = typer.Option(0.6, "--threshold", help="Score threshold for recommendation"),
):
    """Score inbox items and mark recommendations."""
    from app.core.db import get_session, init_db
    from app.pipelines.inbox_recommend import recommend_inbox_items

    init_db()
    session = get_session()
    try:
        result = recommend_inbox_items(session, threshold=threshold)
        typer.echo(f"Recommended: {result['recommended']}, Skipped: {result['skipped']}")
    finally:
        session.close()


@inbox_app.command("reject")
def inbox_reject(
    inbox_id: int = typer.Argument(..., help="Inbox item ID to reject"),
):
    """Reject an inbox item."""
    from app.core.db import get_session, init_db
    from app.core.models import InboxItem

    init_db()
    session = get_session()
    try:
        inbox_item = session.get(InboxItem, inbox_id)
        if not inbox_item:
            typer.echo(f"Inbox item {inbox_id} not found", err=True)
            raise typer.Exit(1)
        inbox_item.status = "rejected"
        session.commit()
        typer.echo(f"Rejected: {inbox_item.title[:80]}")
    finally:
        session.close()


@analytics_app.command("export")
def analytics_export(
    out: str = typer.Option("trends.json", "--out", "-o", help="Output JSON file path"),
):
    """Export trend analytics as JSON."""
    import json

    from app.analytics.trends import (
        items_by_year_collection,
        items_by_year_tag,
        items_by_year_venue,
        top_keyphrases_by_year,
        watch_collection_growth,
    )
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        data = {
            "items_by_year_venue": items_by_year_venue(session),
            "items_by_year_collection": items_by_year_collection(session),
            "items_by_year_tag": items_by_year_tag(session),
            "watch_collection_growth": watch_collection_growth(session),
            "top_keyphrases_by_year": top_keyphrases_by_year(session),
        }
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"Exported analytics to {out}")
    finally:
        session.close()


def main():
    app()


if __name__ == "__main__":
    main()
