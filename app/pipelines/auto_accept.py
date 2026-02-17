"""Auto-accept pipeline — quality flags, scoring, and batch acceptance of inbox items."""

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import InboxItem, Watch

logger = logging.getLogger(__name__)


@dataclass
class QualityFlags:
    """Quality flags for an inbox item."""

    too_short_abstract: bool = False
    missing_authors: bool = False
    suspicious_title: bool = False
    duplicate_high: bool = False
    non_cs_domain: bool = False

    def to_list(self) -> list[str]:
        flags = []
        if self.too_short_abstract:
            flags.append("too_short_abstract")
        if self.missing_authors:
            flags.append("missing_authors")
        if self.suspicious_title:
            flags.append("suspicious_title")
        if self.duplicate_high:
            flags.append("duplicate_high")
        if self.non_cs_domain:
            flags.append("non_cs_domain")
        return flags

    @property
    def count(self) -> int:
        return len(self.to_list())


def compute_quality_flags(session: Session, inbox_item: InboxItem) -> QualityFlags:
    """Compute quality flags for an inbox item."""
    flags = QualityFlags()

    # Too short abstract (<50 chars)
    if not inbox_item.abstract or len(inbox_item.abstract.strip()) < 50:
        flags.too_short_abstract = True

    # Missing authors
    authors = []
    if inbox_item.authors_json:
        try:
            authors = json.loads(inbox_item.authors_json)
        except (json.JSONDecodeError, TypeError):
            pass
    if not authors:
        flags.missing_authors = True

    # Suspicious title: all-caps or very short (<10 chars)
    title = inbox_item.title or ""
    if title and (title == title.upper() and len(title) > 3):
        flags.suspicious_title = True
    if len(title.strip()) < 10:
        flags.suspicious_title = True

    # Duplicate detection via embedding similarity (>0.95)
    try:
        flags.duplicate_high = _check_duplicate_high(session, inbox_item)
    except Exception:
        pass  # graceful degradation if FAISS not available

    # Non-CS domain heuristic
    if inbox_item.source_id_type == "arxiv" and inbox_item.source_id_value:
        # arXiv IDs from non-CS categories
        sid = inbox_item.source_id_value.lower()
        non_cs_prefixes = ["math.", "physics.", "astro-ph.", "hep-", "quant-ph", "cond-mat.", "nlin.", "stat."]
        for prefix in non_cs_prefixes:
            if prefix in sid:
                flags.non_cs_domain = True
                break

    return flags


def _check_duplicate_high(session: Session, inbox_item: InboxItem) -> bool:
    """Check if inbox item is a near-duplicate of existing items via embedding similarity."""
    from app.indexing.engine import search_faiss

    title = inbox_item.title or ""
    abstract = inbox_item.abstract or ""
    query_text = f"{title} {abstract}".strip()
    if not query_text:
        return False

    results = search_faiss(query_text, top_k=1)
    if results and results[0].get("vector_score", 0) > 0.95:
        return True
    return False


def compute_auto_accept_score(session: Session, inbox_item: InboxItem, flags: QualityFlags) -> float:
    """Compute auto-accept score for an inbox item.

    Starts from recommend_score, then adjusts based on watch keywords and quality flags.
    """
    score = inbox_item.recommend_score or 0.0

    # Boost if watch query keywords appear in title/abstract
    watch = session.get(Watch, inbox_item.watch_id)
    if watch and watch.query:
        keywords = watch.query.lower().split()
        title_abstract = f"{inbox_item.title or ''} {inbox_item.abstract or ''}".lower()
        for kw in keywords:
            if len(kw) > 3 and kw in title_abstract:
                score += 0.1
                break  # only one boost

    # Penalty per quality flag
    score -= 0.15 * flags.count

    return max(0.0, min(1.0, score))


def evaluate_auto_accept(
    session: Session,
    threshold: float = 0.75,
    limit: int | None = None,
) -> list[dict]:
    """Dry-run evaluation: returns candidates with scores/flags/reasons without modifying DB."""
    query = select(InboxItem).where(InboxItem.status == "new").order_by(InboxItem.discovered_at.desc())
    if limit:
        query = query.limit(limit)

    inbox_items = session.execute(query).scalars().all()
    results = []

    for inbox_item in inbox_items:
        flags = compute_quality_flags(session, inbox_item)
        score = compute_auto_accept_score(session, inbox_item, flags)

        eligible = score >= threshold and flags.count == 0
        results.append(
            {
                "inbox_id": inbox_item.id,
                "title": inbox_item.title,
                "recommend_score": inbox_item.recommend_score,
                "auto_accept_score": score,
                "quality_flags": flags.to_list(),
                "eligible": eligible,
                "threshold": threshold,
            }
        )

    return results


def apply_auto_accept(
    session: Session,
    threshold: float = 0.75,
    limit: int | None = None,
) -> dict:
    """Accept eligible inbox items. Returns {accepted, skipped, details}."""
    from app.pipelines.watch import accept_inbox_item

    query = select(InboxItem).where(InboxItem.status == "new").order_by(InboxItem.discovered_at.desc())
    if limit:
        query = query.limit(limit)

    inbox_items = session.execute(query).scalars().all()

    accepted = 0
    skipped = 0
    details = []

    for inbox_item in inbox_items:
        flags = compute_quality_flags(session, inbox_item)
        score = compute_auto_accept_score(session, inbox_item, flags)

        # Store computed values
        inbox_item.auto_accept_score = score
        inbox_item.quality_flags_json = json.dumps(flags.to_list(), ensure_ascii=False)

        eligible = score >= threshold and flags.count == 0

        if eligible:
            item = accept_inbox_item(session, inbox_item)
            inbox_item.auto_accept = True
            accepted += 1
            details.append(
                {
                    "inbox_id": inbox_item.id,
                    "item_id": item.id,
                    "title": inbox_item.title,
                    "score": score,
                    "action": "accepted",
                }
            )
        else:
            skipped += 1
            details.append(
                {
                    "inbox_id": inbox_item.id,
                    "title": inbox_item.title,
                    "score": score,
                    "flags": flags.to_list(),
                    "action": "skipped",
                }
            )

    session.flush()
    return {"accepted": accepted, "skipped": skipped, "details": details}
