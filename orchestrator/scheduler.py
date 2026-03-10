"""
APScheduler daily cron for Project ASI.

Fires once per day at the time set in config/settings.yaml (scheduler.cron).
Picks a topic from the default_topics list (round-robin by day-of-year),
runs the full 4-region pipeline, and posts results to Slack for approval.

Integration with app.py:
    The scheduler is started by app.py alongside the webhook server.
    Both run on the same asyncio event loop via AsyncIOScheduler.

Manual trigger (useful for testing):
    from orchestrator.scheduler import run_scheduled_job
    import asyncio
    asyncio.run(run_scheduled_job())
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import load_settings
from db.session import AsyncSessionLocal
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import run_pipeline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topic selection
# ---------------------------------------------------------------------------

def _pick_topic(topics: list[str]) -> str:
    """Round-robin by day-of-year so each topic gets equal rotation."""
    day_of_year = datetime.now(tz=timezone.utc).timetuple().tm_yday
    return topics[day_of_year % len(topics)]


# ---------------------------------------------------------------------------
# Scheduled job
# ---------------------------------------------------------------------------

async def run_scheduled_job() -> None:
    """
    Execute one full pipeline run.
    Called by APScheduler on the cron trigger, or directly for manual runs.
    """
    settings = load_settings()
    topic = _pick_topic(settings.scheduler.default_topics)
    regions = settings.scheduler.default_regions

    payload = JobPayload(
        id=uuid.uuid4(),
        topic=topic,
        regions=regions,
        content_type="journal_article",
    )

    logger.info(
        "scheduled_job_starting",
        extra={
            "job_id": str(payload.id),
            "topic": topic,
            "regions": regions,
        },
    )

    try:
        async with AsyncSessionLocal() as session:
            drafts = await run_pipeline(payload, session=session)

        logger.info(
            "scheduled_job_complete",
            extra={
                "job_id": str(payload.id),
                "drafts": len(drafts),
                "topic": topic,
            },
        )

    except Exception:
        logger.exception(
            "scheduled_job_failed",
            extra={"job_id": str(payload.id), "topic": topic},
        )


# ---------------------------------------------------------------------------
# Scheduler factory
# ---------------------------------------------------------------------------

def build_scheduler() -> AsyncIOScheduler:
    """
    Create and configure the AsyncIOScheduler.
    Call scheduler.start() after the event loop is running.
    """
    settings = load_settings()
    scheduler = AsyncIOScheduler()

    trigger = CronTrigger.from_crontab(
        settings.scheduler.cron,
        timezone="UTC",
    )
    scheduler.add_job(
        run_scheduled_job,
        trigger=trigger,
        id="daily_pipeline",
        name="ASI daily pipeline",
        replace_existing=True,
        misfire_grace_time=600,   # allow up to 10 min late start
    )

    logger.info(
        "scheduler_configured",
        extra={
            "cron": settings.scheduler.cron,
            "regions": settings.scheduler.default_regions,
            "topics": len(settings.scheduler.default_topics),
        },
    )
    return scheduler
