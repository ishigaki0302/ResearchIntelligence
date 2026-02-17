"""Inbox recommendation scoring and auto-tagging."""

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.models import Author, InboxItem, Item, Watch

logger = logging.getLogger(__name__)


def _get_existing_venues(session: Session) -> set[str]:
    """Get set of normalized venue names from existing items."""
    venues = session.execute(select(Item.venue).where(Item.venue.is_not(None)).distinct()).scalars().all()
    return {v.lower() for v in venues if v}


def _get_existing_author_names(session: Session) -> set[str]:
    """Get set of normalized author names from existing items."""
    names = session.execute(select(Author.norm_name).distinct()).scalars().all()
    return set(names)


def _normalize_author(name: str) -> str:
    """Normalize an author name for matching."""
    return re.sub(r"\s+", " ", name.strip().lower())


def _watch_accept_rate(session: Session, watch_id: int) -> float:
    """Get acceptance rate for a watch (0-1)."""
    total = (
        session.execute(
            select(func.count(InboxItem.id)).where(
                InboxItem.watch_id == watch_id,
                InboxItem.status.in_(["accepted", "rejected"]),
            )
        ).scalar()
        or 0
    )
    if total == 0:
        return 0.5  # neutral default
    accepted = (
        session.execute(
            select(func.count(InboxItem.id)).where(
                InboxItem.watch_id == watch_id,
                InboxItem.status == "accepted",
            )
        ).scalar()
        or 0
    )
    return accepted / total


def recommend_inbox_items(session: Session, threshold: float = 0.6) -> dict:
    """Score and recommend inbox items.

    Returns {"recommended": int, "skipped": int}.
    """
    inbox_items = session.execute(select(InboxItem).where(InboxItem.status == "new")).scalars().all()

    if not inbox_items:
        return {"recommended": 0, "skipped": 0}

    existing_venues = _get_existing_venues(session)
    existing_authors = _get_existing_author_names(session)

    current_year = datetime.now(timezone.utc).year

    recommended = 0
    skipped = 0

    for inbox_item in inbox_items:
        score = 0.0
        reasons = []

        # Factor 1: Query match strength (connector score, normalized 0-1)
        if inbox_item.score is not None and inbox_item.score > 0:
            # Normalize assuming score range 0-100
            norm_score = min(inbox_item.score / 100.0, 1.0) if inbox_item.score > 1 else inbox_item.score
            score += 0.3 * norm_score
            if norm_score > 0.5:
                reasons.append(f"High relevance score ({inbox_item.score:.2f})")

        # Factor 2: Venue match
        if inbox_item.venue and inbox_item.venue.lower() in existing_venues:
            score += 0.3
            reasons.append(f"Known venue: {inbox_item.venue}")

        # Factor 3: Author overlap
        if inbox_item.authors_json:
            try:
                authors = json.loads(inbox_item.authors_json)
                for author in authors:
                    if _normalize_author(author) in existing_authors:
                        score += 0.2
                        reasons.append(f"Known author: {author}")
                        break  # only count once
            except (json.JSONDecodeError, TypeError):
                pass

        # Factor 4: Recency
        if inbox_item.year and inbox_item.year >= current_year - 1:
            score += 0.1
            reasons.append("Recent paper")

        # Factor 5: Watch acceptance history
        accept_rate = _watch_accept_rate(session, inbox_item.watch_id)
        if accept_rate > 0.6:
            score += 0.1
            reasons.append(f"Watch has high accept rate ({accept_rate:.0%})")

        # Generate auto-tags
        auto_tags = _generate_auto_tags(session, inbox_item)

        # Update inbox item
        inbox_item.recommend_score = score
        inbox_item.reasons_json = json.dumps(reasons, ensure_ascii=False)
        inbox_item.auto_tags_json = json.dumps(auto_tags, ensure_ascii=False)

        if score >= threshold:
            inbox_item.recommended = True
            recommended += 1
        else:
            skipped += 1

    session.commit()
    return {"recommended": recommended, "skipped": skipped}


def _generate_auto_tags(session: Session, inbox_item: InboxItem) -> list[str]:
    """Generate auto-tag suggestions for an inbox item."""
    tags = []

    # From watch name
    watch = session.get(Watch, inbox_item.watch_id)
    if watch:
        # Extract topic keyword from watch name
        watch_name = watch.name.lower()
        # Strip common prefixes/suffixes
        clean = re.sub(r"[-_]papers?$", "", watch_name)
        clean = re.sub(r"^watch[-_]", "", clean)
        if clean:
            tags.append(f"topic/{clean}")

    # From venue
    if inbox_item.venue:
        venue_short = inbox_item.venue.split()[0] if inbox_item.venue else ""
        if venue_short:
            tags.append(f"venue/{venue_short}")

    # From matched_query keywords
    if inbox_item.matched_query:
        words = inbox_item.matched_query.lower().split()
        # Take significant keywords (>3 chars, not common words)
        stopwords = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "has", "have", "been"}
        keywords = [w for w in words if len(w) > 3 and w not in stopwords]
        for kw in keywords[:2]:
            tag = f"query/{kw}"
            if tag not in tags:
                tags.append(tag)

    return tags


def apply_auto_tags_on_accept(session: Session, inbox_item: InboxItem, item: Item) -> list[str]:
    """Apply auto_tags from inbox_item to the accepted item. Returns applied tags."""
    if not inbox_item.auto_tags_json:
        return []

    try:
        auto_tags = json.loads(inbox_item.auto_tags_json)
    except (json.JSONDecodeError, TypeError):
        return []

    from app.core.service import add_tag_to_item

    applied = []
    for tag_name in auto_tags:
        try:
            add_tag_to_item(session, item.id, tag_name, source="auto")
            applied.append(tag_name)
        except Exception:
            pass

    return applied
