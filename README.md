# Research Index (ri) — v0.2

Local paper/article management + search + visualization system for LaTeX/BibTeX workflows.

## Features

- **Unified Import**: ACL Anthology (bulk), BibTeX files, PDFs, URLs
- **Idempotent Ingestion**: Same paper imported twice → no duplicates
- **Hybrid Search**: BM25 (SQLite FTS5) + vector similarity (FAISS + sentence-transformers)
- **BibTeX Export**: Filter by venue/year/tag/collection → ready-to-use `.bib`
- **Notes**: Auto-generated Markdown notes per paper, editable via Web UI
- **Citation Graph**: Local subgraph visualization (D3.js)
- **Web UI**: FastAPI + Jinja2 + HTMX — search, detail view, note editing
- **CLI**: `ri` command via Typer

### New in v0.2

- **PDF Download Pipeline**: Batch-download PDFs from ACL Anthology (or any source)
- **Reference Extraction**: Extract references from paper text → populate citation graph
- **Tag Management**: Add/remove tags via CLI and Web UI
- **External API Enrichment**: Enrich items with IDs from OpenAlex and Semantic Scholar

## Setup

```bash
cd repo
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

### 1. Import ACL 2024 papers

```bash
# Import main + findings tracks
ri import "acl:2024{main,findings}"

# Check stats
ri stats
```

### 2. Other import sources

```bash
# BibTeX file
ri import bib:/path/to/references.bib

# Single PDF
ri import pdf:/path/to/paper.pdf --title "My Paper" --year 2024

# URL (blog, etc.)
ri import url:https://example.com/blog-post --type blog
```

### 3. Build search index

```bash
ri index
```

This extracts text (PDF/URL), builds FTS5 full-text index, and creates FAISS vector embeddings.

### 4. Search

```bash
# Basic search
ri search "instruction tuning in long context"

# With filters
ri search "retrieval augmented generation" --year 2024 --venue ACL -k 10
```

### 5. Export BibTeX

```bash
# Export all
ri export-bib -o references.bib

# Filter by venue and year
ri export-bib --venue ACL --year 2024 -o acl2024.bib

# Filter by collection
ri export-bib --collection "ACL 2024" -o acl2024.bib
```

### 6. Download PDFs

```bash
# Download PDFs for a collection
ri download-pdf --collection "ACL 2024 (main,findings)" --max 10 --workers 2

# Download PDF for a single item
ri download-pdf --id 42

# Retry failed downloads
ri download-pdf --failed-only
```

### 7. Extract References

```bash
# Extract references from all items with text/PDF
ri extract-references --limit 50

# Extract for a single item
ri extract-references --id 42
```

This parses the References section, extracts DOIs/arXiv IDs, and creates citation links.

### 8. Manage Tags

```bash
# Add a tag
ri tag add 1 method/RAG

# List tags
ri tag ls 1

# Remove a tag
ri tag rm 1 method/RAG
```

Tags are also manageable from the web UI item detail page.

### 9. Enrich with External APIs

```bash
# Enrich items with OpenAlex/Semantic Scholar IDs
ri enrich --limit 10

# Enrich a specific item
ri enrich --id 42

# Also update metadata (title, year) from API
ri enrich --limit 10 --update-metadata
```

### 10. Web UI

```bash
ri serve
# Open http://127.0.0.1:8000
```

Features:
- Home: overview stats, recent items, collections
- Search: hybrid search with filters (year, venue, type)
- Detail: paper metadata, abstract, BibTeX, tags, note editor
- Graph: citation subgraph visualization (D3.js)

### End-to-End Workflow

```bash
ri import "acl:2024{main,findings}"     # Import papers
ri download-pdf --collection "ACL 2024 (main,findings)" --max 100  # Download PDFs
ri index                                  # Build search index
ri extract-references --limit 100         # Extract references → citation graph
ri enrich --limit 100                     # Add external IDs
ri serve                                  # Start web UI
```

## Project Structure

```
repo/
├── app/
│   ├── cli/main.py           # CLI commands (Typer)
│   ├── web/
│   │   ├── server.py          # FastAPI app
│   │   └── templates/         # Jinja2 templates (HTMX)
│   ├── core/
│   │   ├── config.py          # Config loader
│   │   ├── models.py          # SQLAlchemy ORM models
│   │   ├── db.py              # DB session management
│   │   ├── bibtex.py          # BibTeX parsing/generation
│   │   └── service.py         # Core CRUD + upsert logic
│   ├── connectors/
│   │   ├── acl.py             # ACL Anthology connector
│   │   ├── openalex.py        # OpenAlex API connector
│   │   └── semantic_scholar.py # Semantic Scholar API connector
│   ├── pipelines/
│   │   ├── importer.py        # Import orchestration
│   │   ├── exporter.py        # BibTeX export
│   │   ├── extract.py         # Text extraction (PDF/URL)
│   │   ├── downloader.py      # PDF download pipeline
│   │   ├── references.py      # Reference extraction
│   │   └── enricher.py        # External API enrichment
│   ├── indexing/
│   │   └── engine.py          # FTS5 + FAISS indexing
│   └── graph/
│       └── citations.py       # Citation graph queries
├── configs/config.yaml        # App configuration
├── data/
│   ├── library/papers/{id}/   # Per-paper files (PDF, text, notes)
│   └── cache/                 # Raw downloads, embeddings
├── db/app.sqlite              # SQLite database
├── tests/                     # pytest tests
├── pyproject.toml             # Python package config
└── README.md
```

## Configuration

Edit `configs/config.yaml`:

```yaml
storage:
  library_dir: "data/library/papers"
  db_path: "db/app.sqlite"

embedding:
  backend: "sentence-transformers"
  model: "all-MiniLM-L6-v2"   # swap for any ST model
  dimension: 384

download:
  max_workers: 4
  sleep_sec: 1.0
  timeout: 60

external:
  openalex:
    enabled: true
    email: ""            # polite pool
  semantic_scholar:
    enabled: true
    api_key: ""          # optional, for higher rate limits
  enrich:
    match_threshold: 0.85
    sleep_sec: 1.0

search:
  default_top_k: 20
  bm25_weight: 0.5
  vector_weight: 0.5
```

## Tests

```bash
pytest tests/ -v
```
