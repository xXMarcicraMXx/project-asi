"""
APScheduler daily cron for Project Metis (ASI v2).

Fires once per day at the time set in config/settings.yaml (scheduler.cron).
Runs the full 5-region brief pipeline via run_brief_pipeline().

Integration with app.py:
    The scheduler is started by app.py alongside the webhook server.
    Both run on the same asyncio event loop via AsyncIOScheduler.

Manual trigger (useful for testing):
    from orchestrator.scheduler import run_scheduled_job
    import asyncio
    asyncio.run(run_scheduled_job())

Dry-run mode:
    Set DRY_RUN=1 in environment or pass dry_run=True to run_scheduled_job().
    All pipeline logic runs. No Anthropic calls. No DB writes. No rsync.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import load_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduled job
# ---------------------------------------------------------------------------

async def run_scheduled_job(*, dry_run: bool = False) -> None:
    """
    Execute one full Metis daily brief pipeline run.
    Called by APScheduler on the cron trigger, or directly for manual runs.

    Args:
        dry_run: If True, no Anthropic calls, no DB writes, no rsync.
                 Can also be set via DRY_RUN=1 environment variable.
    """
    from orchestrator.brief_pipeline import run_brief_pipeline

    dry_run = dry_run or os.environ.get("DRY_RUN", "").strip() == "1"

    logger.info(
        "scheduled_job_starting",
        extra={"dry_run": dry_run},
    )

    try:
        result = await run_brief_pipeline(dry_run=dry_run)

        logger.info(
            "scheduled_job_complete",
            extra={
                "run_id": str(result.run_id),
                "run_status": result.run_status,
                "total_cost_usd": result.total_cost_usd,
                "regions_ok": [
                    rid for rid, r in result.regions.items()
                    if r.status == "complete"
                ],
                "regions_failed": [
                    rid for rid, r in result.regions.items()
                    if r.status == "failed"
                ],
                "dry_run": dry_run,
            },
        )

    except Exception:
        logger.exception("scheduled_job_unhandled_error")


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
        id="metis_daily_pipeline",
        name="Metis daily brief pipeline",
        replace_existing=True,
        misfire_grace_time=600,  # allow up to 10 min late start
    )

    logger.info(
        "scheduler_configured",
        extra={"cron": settings.scheduler.cron},
    )
    return scheduler
