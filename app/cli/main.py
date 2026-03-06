"""CLI entry point for the research intelligence (ri) tool."""

import logging
import re
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

sync_app = typer.Typer(name="sync", help="Auto-sync pipeline")
app.add_typer(sync_app)

digest_app = typer.Typer(name="digest", help="Generate digest reports")
app.add_typer(digest_app)

dedup_app = typer.Typer(name="dedup", help="Detect and merge duplicates")
app.add_typer(dedup_app)

backup_app = typer.Typer(name="backup", help="Backup and restore")
app.add_typer(backup_app)

version_app = typer.Typer(name="version", help="Manage paper versions")
app.add_typer(version_app)

corpus_app = typer.Typer(name="corpus", help="Whole-corpus analysis pipeline")
app.add_typer(corpus_app)


def _collection_name_to_tag(name: str) -> str:
    """Convert a collection name to a tag.

    Examples:
        "watch:arxiv-daily" -> "watch/arxiv-daily"
        "ACL 2024 (main)"   -> "acl"
    """
    if name.lower().startswith("watch:"):
        return "watch/" + name[6:]
    # Extract first token and lowercase (e.g. "ACL 2024 (main)" -> "acl")
    m = re.match(r"^([A-Za-z]+)", name)
    return m.group(1).lower() if m else name.lower()


@app.command("migrate-collections")
def migrate_collections(
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Dry-run (default) or apply"),
):
    """Migrate collection memberships to tags and drop collection tables."""
    from sqlalchemy import text

    from app.core.db import get_engine, get_session, init_db
    from app.core.service import add_tag_to_item

    init_db()
    engine = get_engine()
    session = get_session()

    try:
        # Check if tables still exist
        with engine.connect() as conn:
            tables = [
                row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            ]

        if "collections" not in tables:
            typer.echo("collections table does not exist — nothing to migrate.")
            return

        # Read collection data via raw SQL
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.name, ci.item_id FROM collection_items ci"
                    " JOIN collections c ON c.id = ci.collection_id"
                )
            ).fetchall()

        if not rows:
            typer.echo("No collection items found.")
        else:
            typer.echo(f"Found {len(rows)} collection item(s) to migrate.")
            for coll_name, item_id in rows:
                tag = _collection_name_to_tag(coll_name)
                if dry_run:
                    typer.echo(f"  [dry-run] item {item_id}: collection '{coll_name}' -> tag '{tag}'")
                else:
                    add_tag_to_item(session, item_id, tag, source="migrate")
                    typer.echo(f"  Tagged item {item_id} with '{tag}' (from '{coll_name}')")

        if not dry_run:
            session.commit()
            with engine.connect() as conn:
                conn.execute(text("DROP TABLE IF EXISTS collection_items"))
                conn.execute(text("DROP TABLE IF EXISTS collections"))
                conn.commit()
            typer.echo("Dropped collection_items and collections tables.")
        else:
            typer.echo("\nRun with --apply to apply changes.")
    finally:
        session.close()


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


@tag_app.command("migrate-kinds")
def tag_migrate_kinds(
    dry_run: bool = typer.Option(True, "--dry-run/--apply"),
):
    """既存タグを命名規則で kind 分類する。"""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Tag
    from app.core.service import infer_tag_kind

    init_db()
    session = get_session()
    try:
        tags = session.execute(select(Tag)).scalars().all()
        changes = [(t, infer_tag_kind(t.name)) for t in tags if t.kind != infer_tag_kind(t.name)]
        if not changes:
            typer.echo("変更なし。")
            return
        for tag, new_kind in changes:
            typer.echo(f"  '{tag.name}'  {tag.kind!r} -> {new_kind!r}")
        if dry_run:
            typer.echo(f"\n--apply で {len(changes)} 件を適用")
        else:
            for tag, new_kind in changes:
                tag.kind = new_kind
            session.commit()
            typer.echo(f"{len(changes)} 件の kind を更新しました。")
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
    tags: Optional[str] = typer.Option(None, "--tags", help="Comma-separated tags to add (e.g. 'to-read,survey')"),
):
    """Import papers from various sources."""
    from app.core.db import init_db
    from app.pipelines.importer import (
        import_acl,
        import_bibtex,
        import_by_title,
        import_pdf,
        import_url,
        parse_import_spec,
    )

    init_db()

    parsed = parse_import_spec(spec)
    src_type = parsed["type"]
    args = parsed["args"]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if src_type == "acl":
        typer.echo(
            f"Importing from ACL Anthology: {args['event'].upper()} {args['year']} volumes={args.get('volumes', 'all')}"
        )
        result = import_acl(**args)
        typer.echo(f"Done: {result['imported']} imported, {result['skipped']} skipped, {result['total']} total")
        typer.echo(f"Tag: {result['event_tag']}")

    elif src_type == "bib":
        typer.echo(f"Importing BibTeX: {args['path']}")
        result = import_bibtex(args["path"])
        typer.echo(f"Done: {result['imported']} imported, {result['skipped']} skipped, {result['total']} total")

    elif src_type == "pdf":
        typer.echo(f"Importing PDF: {args['path']}")
        result = import_pdf(args["path"], title=title, year=year, tags=tag_list)
        typer.echo(
            f"{'Created' if result['created'] else 'Already exists'}: {result['title']} (id={result['item_id']})"
        )

    elif src_type == "url":
        typer.echo(f"Importing URL: {args['url']}")
        result = import_url(args["url"], item_type=item_type, title=title, year=year, tags=tag_list)
        typer.echo(
            f"{'Created' if result['created'] else 'Already exists'}: {result['title']} (id={result['item_id']})"
        )

    elif src_type == "title":
        typer.echo(f"Searching for: {args['query']!r}")
        result = import_by_title(args["query"], tags=tag_list)
        source_label = {"arxiv": "arXiv BibTeX", "s2": "Semantic Scholar", "placeholder": "placeholder (no match)"}.get(
            result["source"], result["source"]
        )
        status = "Created" if result["created"] else "Already exists"
        typer.echo(f"{status} [{source_label}]: {result['title']} (id={result['item_id']})")
        if result["source"] == "placeholder":
            typer.echo("Warning: no Semantic Scholar match found — imported as placeholder.", err=True)

    else:
        typer.echo(f"Unknown import type: {src_type}", err=True)
        raise typer.Exit(1)


@app.command()
def index(
    chunks: bool = typer.Option(False, "--chunks", help="Also chunk texts and build chunk FAISS index"),
    incremental: bool = typer.Option(False, "--incremental", help="Only re-index changed items"),
):
    """Rebuild search indices (FTS5 + FAISS)."""
    from app.core.db import get_session, init_db
    from app.indexing.engine import incremental_index, rebuild_index
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

        if incremental:
            typer.echo("Running incremental index...")
            result = incremental_index(session, include_chunks=chunks)
            session.commit()
            typer.echo(
                f"Incremental index: {result['total']} total, "
                f"{result['changed']} changed, {result['unchanged']} unchanged"
            )
        else:
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


@app.command("build-citations")
def build_citations(
    limit: Optional[int] = typer.Option(None, "--limit", help="Max items to process"),
    item_id: Optional[int] = typer.Option(None, "--id", help="Process a single item"),
):
    """Build citation relationships from metadata via Semantic Scholar API."""
    from app.core.db import get_session, init_db
    from app.core.models import Item
    from app.graph.citations import build_citations_from_metadata, resolve_citations

    init_db()
    session = get_session()

    try:
        items = None
        if item_id:
            item = session.get(Item, item_id)
            if not item:
                typer.echo(f"Item {item_id} not found", err=True)
                raise typer.Exit(1)
            items = [item]

        typer.echo("Building citations from metadata (Semantic Scholar API)...")
        result = build_citations_from_metadata(session, items=items, limit=limit)
        typer.echo(
            f"Done: {result['processed']} processed, {result['citations_added']} citations added, "
            f"{result['api_hits']} API hits, {result['api_misses']} API misses, "
            f"{result['skipped']} skipped (no external ID)"
        )

        typer.echo("Resolving citations...")
        res = resolve_citations(session)
        typer.echo(f"Resolved {res['resolved']} citations, {res['remaining']} remaining unresolved")
    finally:
        session.close()


@app.command("download-pdf")
def download_pdf(
    max_items: Optional[int] = typer.Option(None, "--max", help="Max items to download"),
    workers: int = typer.Option(4, "--workers", help="Parallel workers (currently sequential)"),
    failed_only: bool = typer.Option(False, "--failed-only", help="Retry only previously failed downloads"),
    item_id: Optional[int] = typer.Option(None, "--id", help="Download PDF for a single item"),
    extract: bool = typer.Option(False, "--extract", help="Also extract text and references after download"),
):
    """Download PDFs for items in the database."""
    import json

    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Item, Job
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

            if extract:
                from app.graph.citations import resolve_citations
                from app.pipelines.extract import extract_text_for_item
                from app.pipelines.references import extract_references_for_item

                typer.echo("Extracting text...")
                extracted = extract_text_for_item(item, session)
                session.commit()
                typer.echo(f"  Text extracted: {extracted}")

                typer.echo("Extracting references...")
                entries = extract_references_for_item(session, item)
                session.commit()
                typer.echo(f"  References extracted: {len(entries)}")

                typer.echo("Resolving citations...")
                res = resolve_citations(session)
                typer.echo(f"  Resolved {res['resolved']}, {res['remaining']} remaining")
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
    limit: Optional[int] = typer.Option(None, "--limit", help="Max items to enrich"),
    item_id: Optional[int] = typer.Option(None, "--id", help="Enrich a single item"),
    update_metadata: bool = typer.Option(False, "--update-metadata", help="Update title/year from API"),
):
    """Enrich items with external IDs from OpenAlex and Semantic Scholar."""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Item
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

        items = session.execute(select(Item)).scalars().all()
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
    host: str = typer.Option(None, "--host", help="Host to bind to"),
    port: int = typer.Option(None, "--port", help="Port to listen on"),
):
    """Start the web UI server."""
    import uvicorn

    from app.core.config import get_config
    from app.core.db import init_db

    cfg = get_config().get("web", {})
    host = host or cfg.get("host", "0.0.0.0")
    port = port or cfg.get("port", 8502)
    init_db()
    uvicorn.run("app.web.server:app", host=host, port=port, reload=True)


@app.command()
def stats():
    """Show database statistics."""
    from sqlalchemy import func, select

    from app.core.db import get_session, init_db
    from app.core.models import Author, Citation, Item, Note

    init_db()
    session = get_session()

    try:
        item_count = session.execute(select(func.count(Item.id))).scalar()
        author_count = session.execute(select(func.count(Author.id))).scalar()
        note_count = session.execute(select(func.count(Note.id))).scalar()
        cite_count = session.execute(select(func.count(Citation.id))).scalar()

        typer.echo(f"Items:     {item_count}")
        typer.echo(f"Authors:   {author_count}")
        typer.echo(f"Notes:     {note_count}")
        typer.echo(f"Citations: {cite_count}")

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


@inbox_app.command("auto-accept")
def inbox_auto_accept(
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Dry-run (default) or apply"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Max items to process"),
    threshold: float = typer.Option(0.75, "--threshold", help="Score threshold for auto-accept"),
):
    """Auto-accept inbox items based on quality and relevance scoring."""
    from app.core.db import get_session, init_db
    from app.pipelines.auto_accept import apply_auto_accept, evaluate_auto_accept

    init_db()
    session = get_session()
    try:
        if dry_run:
            results = evaluate_auto_accept(session, threshold=threshold, limit=limit)
            if not results:
                typer.echo("No inbox items to evaluate.")
                return
            for r in results:
                status = "ELIGIBLE" if r["eligible"] else "SKIP"
                flags = ", ".join(r["quality_flags"]) if r["quality_flags"] else "none"
                typer.echo(
                    f"[{status}] [{r['inbox_id']}] {r['title'][:60]} "
                    f"score={r['auto_accept_score']:.2f} flags={flags}"
                )
            eligible = sum(1 for r in results if r["eligible"])
            typer.echo(f"\nSummary: {eligible} eligible, {len(results) - eligible} skipped")
            typer.echo("Run with --apply to accept eligible items.")
        else:
            result = apply_auto_accept(session, threshold=threshold, limit=limit)
            session.commit()
            typer.echo(f"Auto-accept: {result['accepted']} accepted, {result['skipped']} skipped")
            for d in result["details"]:
                if d["action"] == "accepted":
                    typer.echo(f"  Accepted: [{d['inbox_id']}] {d['title'][:60]} (item_id={d['item_id']})")
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
        items_by_year_tag,
        items_by_year_venue,
        top_keyphrases_by_year,
    )
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        data = {
            "items_by_year_venue": items_by_year_venue(session),
            "items_by_year_tag": items_by_year_tag(session),
            "top_keyphrases_by_year": top_keyphrases_by_year(session),
        }
        Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"Exported analytics to {out}")
    finally:
        session.close()


@dedup_app.command("detect")
def dedup_detect():
    """Detect duplicate items in the database."""
    from app.core.db import get_session, init_db
    from app.pipelines.dedup import detect_duplicates

    init_db()
    session = get_session()
    try:
        dupes = detect_duplicates(session)
        if not dupes:
            typer.echo("No duplicates found.")
            return
        typer.echo(f"Found {len(dupes)} potential duplicate(s):\n")
        for d in dupes:
            from app.core.models import Item

            a = session.get(Item, d["item_a_id"])
            b = session.get(Item, d["item_b_id"])
            typer.echo(
                f"  [{d['confidence']:.2f}] {d['method']}: "
                f"#{d['item_a_id']} '{(a.title if a else '?')[:50]}' "
                f"<-> #{d['item_b_id']} '{(b.title if b else '?')[:50]}'"
            )
            if d["details"]:
                typer.echo(f"    {d['details']}")
    finally:
        session.close()


@dedup_app.command("merge")
def dedup_merge(
    src_id: int = typer.Argument(..., help="Source item ID (will be marked as merged)"),
    dst_id: int = typer.Argument(..., help="Destination item ID (will receive entities)"),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Dry-run (default) or apply"),
):
    """Merge source item into destination item."""
    from app.core.db import get_session, init_db
    from app.pipelines.dedup import merge_items

    init_db()
    session = get_session()
    try:
        result = merge_items(session, src_id, dst_id, dry_run=dry_run)
        if "error" in result:
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)
        if dry_run:
            typer.echo(f"Dry-run merge #{src_id} -> #{dst_id}:")
        else:
            typer.echo(f"Merged #{src_id} -> #{dst_id}:")
            session.commit()
        for entity, count in result["moved"].items():
            typer.echo(f"  {entity}: {count}")
    finally:
        session.close()


@backup_app.command("create")
def backup_create(
    out: str = typer.Option("backup.zip", "--out", "-o", help="Output zip file path"),
    no_pdf: bool = typer.Option(False, "--no-pdf", help="Exclude PDF files"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Exclude cache files"),
):
    """Create a backup of the database and data files."""
    from app.pipelines.backup import create_backup

    result = create_backup(output_path=out, no_pdf=no_pdf, no_cache=no_cache)
    size_mb = result["size_bytes"] / (1024 * 1024)
    typer.echo(f"Backup created: {result['path']} ({result['files']} files, {size_mb:.1f} MB)")


@backup_app.command("restore")
def backup_restore(
    from_path: str = typer.Argument(..., help="Path to backup.zip"),
):
    """Show restore instructions for a backup file."""
    import zipfile

    p = Path(from_path)
    if not p.exists():
        typer.echo(f"File not found: {from_path}", err=True)
        raise typer.Exit(1)

    with zipfile.ZipFile(p, "r") as zf:
        names = zf.namelist()
        typer.echo(f"Backup contains {len(names)} files:")
        for n in names[:20]:
            typer.echo(f"  {n}")
        if len(names) > 20:
            typer.echo(f"  ... and {len(names) - 20} more")

    typer.echo("\nTo restore, extract the backup into the repo root:")
    typer.echo(f"  unzip -o {from_path} -d <repo_root>")
    typer.echo("  ri migrate  # apply any pending migrations")


@app.command()
def migrate():
    """Run database migrations to latest version."""
    from app.core.db import SCHEMA_VERSION, get_engine, get_schema_version, init_db, run_migrations

    engine = get_engine()
    from app.core.models import Base

    Base.metadata.create_all(engine)
    applied = run_migrations(engine)
    current = get_schema_version(engine)
    # Also ensure FTS tables exist
    init_db()
    if applied:
        typer.echo(f"Applied {len(applied)} migration(s): {applied}")
    typer.echo(f"Schema version: {current} (latest: {SCHEMA_VERSION})")


@sync_app.command("run")
def sync_run(
    since: str = typer.Option("7d", "--since", help="Look back period (e.g. 7d, 14d)"),
    watch_name: Optional[str] = typer.Option(None, "--watch", help="Run specific watch"),
    limit: int = typer.Option(100, "--limit", help="Max results per watch"),
    recommend: bool = typer.Option(True, "--recommend/--no-recommend", help="Run inbox recommend after"),
    out: Optional[str] = typer.Option(None, "--out", help="Output digest file path"),
):
    """Run sync pipeline: watch run + inbox recommend + digest."""
    from app.pipelines.sync import run_sync

    result = run_sync(
        since=since,
        watch_name=watch_name,
        limit=limit,
        run_recommend=recommend,
        output_path=out,
    )
    typer.echo(
        f"Sync complete: {result['watches_run']} watches, "
        f"{result['total_added']} new items, {result['recommended']} recommended"
    )
    if result.get("digest_path"):
        typer.echo(f"Digest saved: {result['digest_path']}")


@sync_app.command("status")
def sync_status():
    """Show recent sync job status."""
    import json

    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Job

    init_db()
    session = get_session()
    try:
        jobs = (
            session.execute(select(Job).where(Job.job_type == "sync").order_by(Job.created_at.desc()).limit(5))
            .scalars()
            .all()
        )
        if not jobs:
            typer.echo("No sync jobs found.")
            return
        for j in jobs:
            summary = json.loads(j.summary_json) if j.summary_json else {}
            typer.echo(
                f"[{j.id}] {j.status} at {j.created_at} — "
                f"{summary.get('watches_run', '?')} watches, "
                f"{summary.get('total_added', '?')} added"
            )
            if summary.get("digest_path"):
                typer.echo(f"     Digest: {summary['digest_path']}")
    finally:
        session.close()


@sync_app.command("digest")
def sync_digest():
    """Show the latest sync digest."""
    from app.core.config import get_config, resolve_path

    cfg = get_config()
    sync_cfg = cfg.get("sync", {})
    output_dir = resolve_path(sync_cfg.get("output_dir", "data/cache/sync"))
    if not output_dir.exists():
        typer.echo("No sync output directory found.")
        return
    # Find latest digest.md
    md_files = sorted(output_dir.glob("digest_*.md"), reverse=True)
    if not md_files:
        typer.echo("No digest files found.")
        return
    typer.echo(md_files[0].read_text(encoding="utf-8"))


@digest_app.command("weekly")
def digest_weekly(
    since: str = typer.Option("7d", "--since", help="Look back period (e.g. 7d, 14d)"),
    out: Optional[str] = typer.Option(None, "--out", help="Output markdown file path"),
):
    """Generate a weekly digest report."""
    from app.analytics.digest import generate_digest
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        result = generate_digest(session, since=since, output_path=out)
        if result.get("output_path"):
            typer.echo(f"Digest written to {result['output_path']}")
        else:
            typer.echo(result["markdown"])
    finally:
        session.close()


@digest_app.command("watch")
def digest_watch(
    name: str = typer.Option(..., "--name", help="Watch name"),
    since: str = typer.Option("14d", "--since", help="Look back period"),
):
    """Generate a digest for a specific watch."""
    from app.analytics.digest import generate_digest
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        result = generate_digest(session, since=since, watch_name=name)
        typer.echo(result["markdown"])
    finally:
        session.close()


@analytics_app.command("cluster")
def analytics_cluster(
    n_clusters: int = typer.Option(5, "--clusters", "-n", help="Number of clusters"),
    out: Optional[str] = typer.Option(None, "--out", help="Output JSON file path"),
):
    """Run topic clustering on items."""
    import json

    from app.analytics.clustering import cluster_items
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        result = cluster_items(session, n_clusters=n_clusters)
        if out:
            Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            typer.echo(f"Cluster results written to {out}")
        else:
            for c in result["clusters"]:
                typer.echo(f"\nCluster {c['id']} ({c['size']} items): {', '.join(c['top_terms'][:5])}")
                for item in c["representative_items"][:3]:
                    typer.echo(f"  - {item['title'][:80]}")
    finally:
        session.close()


@analytics_app.command("graph-stats")
def analytics_graph_stats(
    out: Optional[str] = typer.Option(None, "--out", help="Output JSON file path"),
):
    """Analyze the citation network."""
    import json

    from app.analytics.network import analyze_citation_network
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        result = analyze_citation_network(session)
        if out:
            Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            typer.echo(f"Graph stats written to {out}")
        else:
            typer.echo(f"Nodes: {result['node_count']}, Edges: {result['edge_count']}")
            typer.echo("\nTop cited (in-degree):")
            for item in result["top_in_degree"][:5]:
                typer.echo(f"  [{item['in_degree']}] {item['title'][:70]}")
            typer.echo("\nTop PageRank:")
            for item in result["top_pagerank"][:5]:
                typer.echo(f"  [{item['pagerank']:.4f}] {item['title'][:70]}")
            typer.echo(f"\nCommunities: {result['community_count']}")
    finally:
        session.close()


@version_app.command("link")
def version_link(
    id1: int = typer.Argument(..., help="First item ID"),
    id2: int = typer.Argument(..., help="Second item ID"),
):
    """Link two items as versions of the same paper."""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Item

    init_db()
    session = get_session()
    try:
        item1 = session.get(Item, id1)
        item2 = session.get(Item, id2)
        if not item1:
            typer.echo(f"Item {id1} not found", err=True)
            raise typer.Exit(1)
        if not item2:
            typer.echo(f"Item {id2} not found", err=True)
            raise typer.Exit(1)

        # Determine group id: prefer existing group, otherwise use lowest item id
        group_id = item1.version_group_id or item2.version_group_id or min(id1, id2)

        # Update all items in both groups to share the same group_id
        old_groups = {g for g in [item1.version_group_id, item2.version_group_id] if g}
        items_to_update = [item1, item2]
        if old_groups:
            others = session.execute(select(Item).where(Item.version_group_id.in_(old_groups))).scalars().all()
            items_to_update.extend(others)

        seen = set()
        for it in items_to_update:
            if it.id in seen:
                continue
            seen.add(it.id)
            it.version_group_id = group_id
            if not it.version_label:
                it.version_label = it.venue_instance or it.venue or f"item #{it.id}"

        session.commit()
        typer.echo(f"Linked items {id1} and {id2} in version group {group_id}")
    finally:
        session.close()


@version_app.command("list")
def version_list(
    item_id: int = typer.Argument(..., help="Item ID"),
):
    """List all versions of a paper."""
    from sqlalchemy import select

    from app.core.db import get_session, init_db
    from app.core.models import Item

    init_db()
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            typer.echo(f"Item {item_id} not found", err=True)
            raise typer.Exit(1)

        if not item.version_group_id:
            typer.echo(f"Item {item_id} is not part of a version group.")
            return

        versions = (
            session.execute(
                select(Item)
                .where(Item.version_group_id == item.version_group_id)
                .order_by(Item.version_date.asc().nulls_last(), Item.id.asc())
            )
            .scalars()
            .all()
        )

        typer.echo(f"Version group {item.version_group_id} ({len(versions)} versions):\n")
        for v in versions:
            marker = " <--" if v.id == item_id else ""
            typer.echo(
                f"  [{v.id}] {v.version_label or '?'} | "
                f"{v.venue_instance or v.venue or '?'} | "
                f"{v.version_date or v.date or '?'}{marker}"
            )
    finally:
        session.close()


@version_app.command("unlink")
def version_unlink(
    item_id: int = typer.Argument(..., help="Item ID to remove from version group"),
):
    """Remove an item from its version group."""
    from app.core.db import get_session, init_db
    from app.core.models import Item

    init_db()
    session = get_session()
    try:
        item = session.get(Item, item_id)
        if not item:
            typer.echo(f"Item {item_id} not found", err=True)
            raise typer.Exit(1)

        if not item.version_group_id:
            typer.echo(f"Item {item_id} is not part of a version group.")
            return

        item.version_group_id = None
        session.commit()
        typer.echo(f"Removed item {item_id} from its version group.")
    finally:
        session.close()


gpu_app = typer.Typer(name="gpu", help="GPU-accelerated analysis (requires CUDA)")
app.add_typer(gpu_app)

llm_app = typer.Typer(name="llm-analyze", help="LLM-based paper analysis")
app.add_typer(llm_app)


@gpu_app.command("status")
def gpu_status():
    """Show GPU availability and device info."""
    from app.gpu import gpu_device_info, is_gpu_available

    if is_gpu_available():
        info = gpu_device_info()
        typer.echo(f"GPU: 利用可能 ({info['count']} デバイス)")
        for d in info["devices"]:
            typer.echo(f"  [{d['index']}] {d['name']} ({d['memory_gb']} GB)")
    else:
        typer.echo("GPU: 利用不可 (CUDA なし)")


@gpu_app.command("embed")
def gpu_embed(
    rebuild: bool = typer.Option(True, "--rebuild/--no-rebuild", help="Rebuild FAISS index with GPU embeddings"),
    venue: str = typer.Option("", "--venue", help="Filter by venue_instance"),
):
    """Rebuild FAISS index using GPU embedding model (BGE-M3)."""
    from app.gpu import is_gpu_available
    from app.gpu.embedder import get_gpu_embedder

    if not is_gpu_available():
        typer.echo("GPU が利用できません。通常の sentence-transformers を使用してください。", err=True)
        raise typer.Exit(1)

    embedder = get_gpu_embedder()
    if embedder is None:
        typer.echo("GPU 埋め込みモデルのロードに失敗しました。", err=True)
        raise typer.Exit(1)

    typer.echo(f"GPU 埋め込みモデル準備完了: dim={embedder.get_sentence_embedding_dimension()}")

    if rebuild:
        from app.core.db import get_session, init_db
        from app.indexing.engine import rebuild_faiss

        init_db()
        session = get_session()
        try:
            typer.echo("FAISS インデックスを GPU 埋め込みで再構築中...")
            rebuild_faiss(session)
            typer.echo("完了")
        finally:
            session.close()


@llm_app.command("tldr")
def llm_tldr(
    venue: str = typer.Option("NLP2026", "--venue", help="Target venue_instance"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing TLDRs"),
    limit: int = typer.Option(0, "--limit", help="Max items (0=all)"),
):
    """Generate TLDRs for papers using LLM (Qwen2.5)."""
    from app.core.db import get_session, init_db
    from app.pipelines.llm_analyze import generate_tldr_batch

    init_db()
    session = get_session()
    try:
        typer.echo(f"TLDR 生成開始: venue={venue}, overwrite={overwrite}")
        item_ids = None
        if limit:
            from sqlalchemy import select

            from app.core.models import Item

            items = (
                session.execute(
                    select(Item.id).where(Item.venue_instance == venue, Item.status == "active").limit(limit)
                )
                .scalars()
                .all()
            )
            item_ids = list(items)

        result = generate_tldr_batch(session, venue_instance=venue, item_ids=item_ids, overwrite=overwrite)
        typer.echo(f"完了: 生成={result['processed']}, スキップ={result['skipped']}, 失敗={result['failed']}")
        if not result.get("gpu_available"):
            typer.echo("※ GPU なし環境のためスキップされました", err=True)
    finally:
        session.close()


@llm_app.command("extract-entities")
def llm_extract_entities(
    venue: str = typer.Option("NLP2026", "--venue", help="Target venue_instance"),
    limit: int = typer.Option(0, "--limit", help="Max items (0=all)"),
    batch_size: int = typer.Option(4, "--batch-size", help="Items per LLM batch (reduce if OOM)"),
):
    """Extract NLP tasks/methods/datasets from papers using LLM."""
    from app.core.db import get_session, init_db
    from app.pipelines.llm_analyze import extract_entities_batch

    init_db()
    session = get_session()
    try:
        typer.echo(f"エンティティ抽出開始: venue={venue}, batch_size={batch_size}")
        item_ids = None
        if limit:
            from sqlalchemy import select

            from app.core.models import Item

            items = (
                session.execute(
                    select(Item.id).where(Item.venue_instance == venue, Item.status == "active").limit(limit)
                )
                .scalars()
                .all()
            )
            item_ids = list(items)

        result = extract_entities_batch(session, venue_instance=venue, item_ids=item_ids, batch_size=batch_size)
        typer.echo(f"完了: 処理={result['processed']}, タグ追加={result['tags_added']}, 失敗={result['failed']}")
    finally:
        session.close()


@llm_app.command("all")
def llm_all(
    venue: str = typer.Option("NLP2026", "--venue", help="Target venue_instance"),
    overwrite_tldr: bool = typer.Option(False, "--overwrite-tldr", help="Overwrite existing TLDRs"),
):
    """Run all LLM analyses (TLDR + entity extraction)."""
    from app.core.db import get_session, init_db
    from app.pipelines.llm_analyze import run_full_analysis

    init_db()
    session = get_session()
    try:
        typer.echo(f"LLM 全分析開始: venue={venue}")
        result = run_full_analysis(session, venue_instance=venue, overwrite_tldr=overwrite_tldr)
        for key, val in result.items():
            typer.echo(f"  {key}: {val}")
    finally:
        session.close()


@corpus_app.command("ingest")
def corpus_ingest(
    path: str = typer.Argument(..., help="Directory containing PDF files"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Disable progress bar"),
):
    """Ingest a directory of PDFs into the corpus (idempotent).

    Extracts title, abstract and full text from each PDF and registers
    them as items in the database. Re-running is safe — already-registered
    PDFs are skipped.
    """
    from app.core.db import get_session, init_db
    from app.pipelines.corpus_ingest import ingest_directory

    init_db()
    session = get_session()
    try:
        result = ingest_directory(
            pdf_dir=path,
            session=session,
            show_progress=not no_progress,
        )
        typer.echo(
            f"Done — created: {result['created']}, "
            f"skipped: {result['skipped']}, "
            f"failed: {result['failed']}, "
            f"total: {result['total']}"
        )
    finally:
        session.close()


@corpus_app.command("embed")
def corpus_embed(
    rebuild: bool = typer.Option(False, "--rebuild", help="Force re-embed all items (ignore cache)"),
    umap: bool = typer.Option(True, "--umap/--no-umap", help="Run UMAP projection after embedding"),
):
    """Embed all corpus items and compute UMAP 2D coordinates.

    Embeddings are cached in data/corpus/embeddings.npz.
    UMAP coordinates are saved to data/corpus/umap2d.json.
    Re-running without --rebuild only embeds new items.
    """
    from app.core.db import get_session, init_db
    from app.pipelines.corpus_embed import compute_umap, embed_corpus

    init_db()
    session = get_session()
    try:
        result = embed_corpus(session, rebuild=rebuild)
        typer.echo(
            f"Embedding — total: {result['total']}, new: {result['embedded']}, "
            f"cached: {result['cached']}, dim: {result['dim']}"
        )
        if umap and result["total"] > 0:
            umap_result = compute_umap(rebuild=rebuild)
            typer.echo(f"UMAP — projected {umap_result['total']} items -> {umap_result['output_path']}")
    finally:
        session.close()


@corpus_app.command("cluster")
def corpus_cluster(
    method: str = typer.Option("hdbscan", "--method", help="Clustering method: hdbscan or kmeans"),
    n_clusters: int = typer.Option(10, "--clusters", "-n", help="Number of clusters (kmeans only)"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Overwrite existing cluster_summary.json"),
):
    """Cluster corpus embeddings and label clusters with LLM (Topic Atlas).

    Reads data/corpus/embeddings.npz + umap2d.json,
    runs HDBSCAN or KMeans, optionally labels with LLM,
    and writes data/corpus/cluster_summary.json.
    """
    from app.analytics.corpus_cluster import cluster_corpus
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        clusters = cluster_corpus(session, method=method, n_clusters=n_clusters, rebuild=rebuild)
        typer.echo(f"Done — {len(clusters)} clusters written to data/corpus/cluster_summary.json")
        for c in clusters[:5]:
            label = c.get("label_en") or f"Cluster {c['cluster_id']}"
            typer.echo(f"  [{c['cluster_id']}] {label} ({len(c['paper_ids'])} papers)")
        if len(clusters) > 5:
            typer.echo(f"  ... and {len(clusters) - 5} more")
    finally:
        session.close()


@corpus_app.command("normalize-tags")
def corpus_normalize_tags(
    rebuild: bool = typer.Option(False, "--rebuild", help="Re-tag even already-tagged items"),
    patterns: bool = typer.Option(True, "--patterns/--no-patterns", help="Compute co-occurrence patterns"),
):
    """Extract method/task/dataset/metric tags from corpus items.

    Uses LLM when available, falls back to TF-IDF keywords.
    Writes tag_cooccurrence.json and tag_patterns.json.
    """
    from app.core.db import get_session, init_db
    from app.pipelines.corpus_tags import compute_tag_patterns, normalize_tags

    init_db()
    session = get_session()
    try:
        result = normalize_tags(session, rebuild=rebuild)
        typer.echo(
            f"Done — tagged: {result['tagged']}, skipped: {result['skipped']}, "
            f"total: {result['total']}, unique tags: {result['tag_count']}"
        )
        if patterns and result["tagged"] > 0:
            pat = compute_tag_patterns(session)
            typer.echo(f"Patterns — top pairs: {len(pat['top_pairs'])}, gap candidates: {len(pat['gaps'])}")
    finally:
        session.close()


@corpus_app.command("personalize")
def corpus_personalize(
    profile: str = typer.Argument(..., help="Profile text or path to .txt/.bib file"),
    top_k: int = typer.Option(30, "--top", help="Number of top papers to return"),
    no_explain: bool = typer.Option(False, "--no-explain", help="Skip LLM explanation"),
):
    """Rank corpus papers by similarity to your research profile.

    Reads data/corpus/embeddings.npz, embeds the profile, and ranks
    papers by cosine similarity. Saves top-K to data/corpus/personalized_top30.json.
    """
    from app.core.db import get_session, init_db
    from app.pipelines.corpus_personalize import personalize

    init_db()
    session = get_session()
    try:
        results = personalize(session, profile=profile, top_k=top_k, explain=not no_explain)
        typer.echo(f"Top {len(results)} papers for your profile:\n")
        for r in results[:10]:
            typer.echo(f"  [{r['rank']:2d}] {r['title'][:70]} (score={r['score']:.3f})")
            if r.get("reason"):
                typer.echo(f"       {r['reason'][:100]}")
        if len(results) > 10:
            typer.echo(f"\n  ... and {len(results) - 10} more (see data/corpus/personalized_top30.json)")
    finally:
        session.close()


@corpus_app.command("gaps")
def corpus_gaps(
    top_n: int = typer.Option(10, "--top", help="Number of top gaps to detect"),
):
    """Detect research gaps and generate experiment proposals (Top-10).

    Reads tag_patterns.json (from 'ri corpus normalize-tags') and
    personalized_top30.json (from 'ri corpus personalize').
    Writes data/corpus/gaps_top10.json.
    """
    from app.analytics.corpus_gaps import detect_gaps
    from app.core.db import get_session, init_db

    init_db()
    session = get_session()
    try:
        gaps = detect_gaps(session, top_n=top_n)
        typer.echo(f"Top {len(gaps)} research gaps:\n")
        for g in gaps:
            typer.echo(f"  [{g['rank']:2d}] {g['description']}")
            if g.get("experiment"):
                typer.echo(f"       → {g['experiment'][:100]}")
    finally:
        session.close()


@corpus_app.command("report")
def corpus_report(
    output: Optional[str] = typer.Option(None, "--output", help="Output directory (default: data/corpus/reports/)"),
    fmt: str = typer.Option("html", "--format", help="Output format: html or markdown"),
):
    """Generate the MVP corpus analysis report (HTML or Markdown).

    Combines:
      1. Topic Atlas (cluster map)
      2. Personalized Top30 papers
      3. Research Gaps Top10

    Output: data/corpus/reports/report_<timestamp>/index.html
    """
    from pathlib import Path

    from app.pipelines.corpus_report import generate_report

    out_dir = Path(output) if output else None
    out_path = generate_report(output_dir=out_dir, fmt=fmt)
    typer.echo(f"Report written to: {out_path}")


def main():
    app()


if __name__ == "__main__":
    main()
