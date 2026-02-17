# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
