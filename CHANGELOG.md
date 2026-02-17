# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.5.0] - 2026-02-17

### Added
- **Auto-sync pipeline (P12)**: Scheduled watch + recommend execution
  - `ri sync run [--since 7d] [--watch <name>] [--recommend] [--out digest.md]`
  - `ri sync status` shows recent sync jobs
  - `ri sync digest` displays latest digest
  - Job recording with summary (counts, duration, failures)
  - `.github/workflows/sync.yml` for GitHub Actions (schedule + workflow_dispatch)
  - Sync config in `config.yaml` (enable, default_since_days, output_dir, actions.mode)
- **Weekly digest (P13)**: Markdown report generation for discoveries
  - `ri digest weekly --since 7d --out digest.md`
  - `ri digest watch --name <name> --since 14d`
  - Summary: total discovered/recommended/accepted by watch
  - Top recommended papers per watch with scores
  - TF-IDF keyword extraction from inbox items
  - Outputs both Markdown and JSON
- **Advanced analytics (P14)**: Topic clustering and citation network analysis
  - `ri analytics cluster [--clusters N] [--out clusters.json]`
  - TF-IDF + KMeans clustering with top terms and representative papers
  - `ri analytics graph-stats [--out graph.json]`
  - Citation network: in-degree, out-degree, PageRank, community detection
  - `/analytics` now shows Cluster Overview, Influential Papers, and Communities
- **Operations quality (P15)**: Backup, migration, and observability
  - `ri backup create --out backup.zip [--no-pdf] [--no-cache]`
  - `ri backup restore <backup.zip>` (shows restore instructions)
  - `ri migrate` applies pending DB migrations
  - `schema_version` table tracks applied migrations
  - Jobs table now includes `summary_json`, `started_at`, `finished_at`
  - 19 new tests (88 total), all passing

### Changed
- Migration framework replaces ad-hoc `_migrate_add_columns` with versioned migrations
- Added `networkx>=3.1` dependency for citation network analysis
- Updated version to 0.5.0

## [0.4.0] - 2026-02-17

### Added
- **Chunk embeddings (P8)**: Fine-grained vector search over text chunks
  - `Chunk` model for splitting item texts into searchable segments
  - Heading-aware text chunking (`app/indexing/chunker.py`)
  - Separate FAISS index for chunk embeddings
  - `ri index --chunks` to build chunk index
  - `ri search --scope both` to search items and chunks together
  - Chunk hit preview in web search results and item detail
- **Citation quality improvements (P9)**: Better reference extraction and resolution
  - Multi-pattern reference extraction (bracket, numbered-dot, paragraph)
  - Multi-ID extraction: DOI, arXiv, ACL Anthology, OpenReview, URL, ISBN
  - Hash-based citation dedup (re-runnable without duplicates)
  - Enhanced resolution: bibtex_key, DOI, arXiv, ACL, URL, title fallback
  - Resolution stats with method breakdown
  - Depth-2 citation subgraph support
  - Tabbed References/Cited-by/Graph view on item detail
  - Unresolved references shown on item detail
- **Inbox automation (P10)**: Recommendation scoring and auto-tagging
  - `ri inbox recommend` scores inbox items by relevance, venue, author overlap, recency
  - Auto-tag suggestions from watch name, venue, and query keywords
  - "Recommended" filter in web inbox
  - Auto-tags applied on accept
- **DevOps quality (P11)**: CI/CD hardening
  - `CODEOWNERS` file
  - Dependabot for pip and GitHub Actions
  - Benchmark test in CI (warn-only)

### Changed
- `hybrid_search()` now accepts `scope` parameter: "item", "chunk", or "both"
- `extract_references_for_item()` uses hash-based dedup instead of skip-all-if-any-exist
- `resolve_citations()` returns detailed stats with method breakdown
- `get_citation_subgraph()` includes `unresolved_refs` and supports `depth` parameter
- Updated version to 0.4.0

## [0.3.0] - 2026-02-17

### Added
- Watchlist system for continuous paper ingestion (arXiv, OpenAlex)
- Inbox for reviewing discovered papers (accept/reject workflow)
- Trend analytics dashboard (year x venue, keyphrases, collection growth)
- CLI commands: `ri watch add/list/run`, `ri inbox list/accept/reject`, `ri analytics export`
- Web UI pages: `/watches`, `/inbox`, `/analytics`
- GitHub Actions CI (pytest, ruff, black) and tag-triggered releases
- Issue and PR templates

### Changed
- Updated version to 0.3.0

## [0.2.0] - 2025-01-01

### Added
- Core item management with idempotent upsert
- ACL Anthology connector with BibTeX import
- OpenAlex and Semantic Scholar enrichment
- BM25 (FTS5) + FAISS hybrid search
- PDF download pipeline
- Reference extraction and citation graph
- Tag management (CLI + web)
- BibTeX export with filters
- Web UI with search, item detail, notes, collections, citation graph
- 41 passing tests
