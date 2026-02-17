"""Digest report generation — summarize discoveries, recommendations, and trends."""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import InboxItem, Watch

logger = logging.getLogger(__name__)


def _parse_since(since: str) -> int:
    """Parse since string like '7d' to days."""
    m = re.match(r"^(\d+)d$", since)
    return int(m.group(1)) if m else 7


def generate_digest(
    session: Session,
    since: str = "7d",
    watch_name: str | None = None,
    output_path: str | None = None,
) -> dict:
    """Generate a digest report.

    Returns: {"markdown": str, "data": dict, "output_path": str|None}
    """
    days = _parse_since(since)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Build watch filter
    watch_filter = None
    if watch_name:
        watch = session.execute(select(Watch).where(Watch.name == watch_name)).scalar_one_or_none()
        if watch:
            watch_filter = watch.id

    # Gather stats
    inbox_query = select(InboxItem).where(InboxItem.discovered_at >= cutoff)
    if watch_filter:
        inbox_query = inbox_query.where(InboxItem.watch_id == watch_filter)

    inbox_items = session.execute(inbox_query).scalars().all()

    total_discovered = len(inbox_items)
    total_recommended = sum(1 for i in inbox_items if i.recommended)
    total_accepted = sum(1 for i in inbox_items if i.status == "accepted")
    total_rejected = sum(1 for i in inbox_items if i.status == "rejected")
    total_new = sum(1 for i in inbox_items if i.status == "new")

    # By watch
    watches = session.execute(select(Watch)).scalars().all()
    watch_map = {w.id: w for w in watches}

    watch_stats = {}
    for item in inbox_items:
        w = watch_map.get(item.watch_id)
        name = w.name if w else f"watch:{item.watch_id}"
        if name not in watch_stats:
            watch_stats[name] = {"discovered": 0, "recommended": 0, "accepted": 0, "top_recommended": []}
        ws = watch_stats[name]
        ws["discovered"] += 1
        if item.recommended:
            ws["recommended"] += 1
        if item.status == "accepted":
            ws["accepted"] += 1
        if item.recommend_score is not None:
            ws["top_recommended"].append(
                {
                    "title": item.title,
                    "score": item.recommend_score,
                    "year": item.year,
                    "venue": item.venue,
                }
            )

    # Sort top recommended per watch
    for ws in watch_stats.values():
        ws["top_recommended"] = sorted(ws["top_recommended"], key=lambda x: x["score"] or 0, reverse=True)[:5]

    # Extract keywords from discovered items
    keywords = _extract_keywords(inbox_items)

    # Build data dict
    data = {
        "period_days": days,
        "cutoff": cutoff.isoformat(),
        "summary": {
            "total_discovered": total_discovered,
            "total_recommended": total_recommended,
            "total_accepted": total_accepted,
            "total_rejected": total_rejected,
            "total_new": total_new,
        },
        "by_watch": watch_stats,
        "keywords": keywords,
    }

    # Generate markdown
    md = _render_markdown(data, watch_name=watch_name)

    result = {"markdown": md, "data": data, "output_path": None}

    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        result["output_path"] = str(p)
        # Also write JSON
        json_path = p.with_suffix(".json")
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return result


def _extract_keywords(inbox_items: list, top_n: int = 15) -> list[dict]:
    """Extract top keywords from inbox items using TF-IDF."""
    texts = []
    for item in inbox_items:
        text = (item.title or "") + " " + (item.abstract or "")
        text = text.strip()
        if text:
            texts.append(text)

    if len(texts) < 2:
        return []

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        return []

    vectorizer = TfidfVectorizer(
        max_features=200,
        ngram_range=(1, 2),
        stop_words="english",
        max_df=0.9,
        min_df=1,
    )

    try:
        tfidf_matrix = vectorizer.fit_transform(texts)
    except ValueError:
        return []

    feature_names = vectorizer.get_feature_names_out()
    scores = tfidf_matrix.sum(axis=0).A1
    top_indices = scores.argsort()[::-1][:top_n]

    return [{"phrase": feature_names[idx], "score": round(float(scores[idx]), 4)} for idx in top_indices]


def _render_markdown(data: dict, watch_name: str | None = None) -> str:
    """Render digest data as Markdown."""
    lines = []
    summary = data["summary"]
    period = data["period_days"]

    title = f"Research Intelligence Digest — Last {period} days"
    if watch_name:
        title += f" (watch: {watch_name})"
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| Discovered | {summary['total_discovered']} |")
    lines.append(f"| Recommended | {summary['total_recommended']} |")
    lines.append(f"| Accepted | {summary['total_accepted']} |")
    lines.append(f"| Rejected | {summary['total_rejected']} |")
    lines.append(f"| Pending | {summary['total_new']} |")
    lines.append("")

    # By watch
    if data["by_watch"]:
        lines.append("## By Watch")
        lines.append("")
        for wname, ws in data["by_watch"].items():
            lines.append(f"### {wname}")
            lines.append(
                f"Discovered: {ws['discovered']} | Recommended: {ws['recommended']} " f"| Accepted: {ws['accepted']}"
            )
            lines.append("")
            if ws["top_recommended"]:
                lines.append("**Top recommended:**")
                lines.append("")
                for i, rec in enumerate(ws["top_recommended"], 1):
                    venue = f" ({rec['venue']})" if rec.get("venue") else ""
                    year = f" [{rec['year']}]" if rec.get("year") else ""
                    score = f" score={rec['score']:.2f}" if rec.get("score") is not None else ""
                    lines.append(f"{i}. {rec['title']}{venue}{year}{score}")
                lines.append("")

    # Keywords
    if data["keywords"]:
        lines.append("## Top Keywords")
        lines.append("")
        for kw in data["keywords"]:
            lines.append(f"- **{kw['phrase']}** ({kw['score']})")
        lines.append("")

    return "\n".join(lines)
