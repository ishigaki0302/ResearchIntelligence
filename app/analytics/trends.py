"""Trend analytics — aggregation functions for items and keyphrases."""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.models import (
    Item,
    ItemTag,
    Tag,
)

logger = logging.getLogger(__name__)


def items_by_year_venue(session: Session) -> list[dict]:
    """Aggregate item counts by year and venue.

    Returns: [{"year": int, "venue": str, "count": int}, ...]
    """
    rows = session.execute(
        select(Item.year, Item.venue, func.count(Item.id))
        .where(Item.year.is_not(None), Item.venue.is_not(None))
        .group_by(Item.year, Item.venue)
        .order_by(Item.year, Item.venue)
    ).all()
    return [{"year": r[0], "venue": r[1], "count": r[2]} for r in rows]


def items_by_year_tag(session: Session) -> list[dict]:
    """Aggregate item counts by year and tag.

    Returns: [{"year": int, "tag": str, "count": int}, ...]
    """
    rows = session.execute(
        select(Item.year, Tag.name, func.count(Item.id))
        .join(ItemTag, ItemTag.item_id == Item.id)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(Item.year.is_not(None))
        .group_by(Item.year, Tag.name)
        .order_by(Item.year, Tag.name)
    ).all()
    return [{"year": r[0], "tag": r[1], "count": r[2]} for r in rows]


def top_keyphrases_by_year(session: Session, top_n: int = 20) -> list[dict]:
    """Extract top keyphrases per year using TF-IDF on title + abstract.

    Returns: [{"year": int, "phrase": str, "score": float}, ...]
    """
    # Group items by year
    items = session.execute(select(Item.year, Item.title, Item.abstract).where(Item.year.is_not(None))).all()

    if not items:
        return []

    # Group texts by year
    year_texts: dict[int, list[str]] = {}
    for year, title, abstract in items:
        text = (title or "") + " " + (abstract or "")
        text = text.strip()
        if text:
            year_texts.setdefault(year, []).append(text)

    if not year_texts:
        return []

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        logger.warning("scikit-learn not available, skipping keyphrase extraction")
        return []

    results = []
    for year in sorted(year_texts.keys()):
        texts = year_texts[year]
        if len(texts) < 2:
            continue

        vectorizer = TfidfVectorizer(
            max_features=500,
            ngram_range=(1, 2),
            stop_words="english",
            max_df=0.9,
            min_df=1,
        )

        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
        except ValueError:
            continue

        feature_names = vectorizer.get_feature_names_out()
        # Sum TF-IDF scores across documents
        scores = tfidf_matrix.sum(axis=0).A1
        top_indices = scores.argsort()[::-1][:top_n]

        for idx in top_indices:
            results.append(
                {
                    "year": year,
                    "phrase": feature_names[idx],
                    "score": round(float(scores[idx]), 4),
                }
            )

    return results
