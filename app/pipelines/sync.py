"""Sync pipeline — orchestrates watch run + inbox recommend + digest generation."""

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_config, resolve_path
from app.core.db import get_session, init_db
from app.core.models import Job, Watch

logger = logging.getLogger(__name__)


def _parse_since(since: str) -> int:
    """Parse since string like '7d' into days."""
    m = re.match(r"^(\d+)d$", since)
    return int(m.group(1)) if m else 7


def run_sync(
    since: str = "7d",
    watch_name: str | None = None,
    limit: int = 100,
    run_recommend: bool = True,
    output_path: str | None = None,
) -> dict:
    """Run the full sync pipeline.

    Returns: {"watches_run": int, "total_added": int, "recommended": int, "digest_path": str|None, "job_id": int}
    """
    init_db()
    session = get_session()
    cfg = get_config()
    sync_cfg = cfg.get("sync", {})

    since_days = _parse_since(since)
    if run_recommend is None:
        run_recommend = sync_cfg.get("run_recommend", True)

    started = datetime.now(timezone.utc)

    # Create job record
    job = Job(
        job_type="sync",
        status="running",
        payload_json=json.dumps({"since": since, "watch_name": watch_name, "limit": limit}),
        started_at=started,
    )
    session.add(job)
    session.commit()

    try:
        # Step 1: Run watches
        from app.pipelines.watch import run_watch

        query = select(Watch).where(Watch.enabled.is_(True))
        if watch_name:
            query = query.where(Watch.name == watch_name)
        watches = session.execute(query).scalars().all()

        watches_run = 0
        total_added = 0
        total_fetched = 0
        watch_results = []

        for w in watches:
            logger.info(f"Sync: running watch '{w.name}' ({w.source})")
            result = run_watch(session, w, since_days=since_days, limit=limit)
            session.commit()
            watches_run += 1
            total_added += result["added"]
            total_fetched += result["fetched"]
            watch_results.append(
                {
                    "watch": w.name,
                    "fetched": result["fetched"],
                    "added": result["added"],
                    "skipped": result["skipped"],
                }
            )

        # Step 2: Run recommend
        recommended = 0
        if run_recommend:
            from app.pipelines.inbox_recommend import recommend_inbox_items

            rec_result = recommend_inbox_items(session)
            recommended = rec_result.get("recommended", 0)

        # Step 3: Generate digest
        digest_path = None
        if output_path or sync_cfg.get("output_dir"):
            from app.analytics.digest import generate_digest

            if not output_path:
                out_dir = resolve_path(sync_cfg.get("output_dir", "data/cache/sync"))
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                output_path = str(out_dir / f"digest_{ts}.md")

            digest_result = generate_digest(session, since=since, output_path=output_path)
            digest_path = digest_result.get("output_path")

        finished = datetime.now(timezone.utc)
        duration = (finished - started).total_seconds()

        summary = {
            "watches_run": watches_run,
            "total_fetched": total_fetched,
            "total_added": total_added,
            "recommended": recommended,
            "digest_path": digest_path,
            "duration_sec": round(duration, 1),
            "watch_results": watch_results,
        }

        job.status = "done"
        job.finished_at = finished
        job.summary_json = json.dumps(summary, ensure_ascii=False)
        session.commit()

        return {**summary, "job_id": job.id}

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.finished_at = datetime.now(timezone.utc)
        session.commit()
        logger.error(f"Sync failed: {e}")
        raise
    finally:
        session.close()
