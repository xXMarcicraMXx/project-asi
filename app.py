"""
ASI application entrypoint.

Runs as the Docker CMD. Starts:
  - APScheduler cron job (daily pipeline at settings.scheduler.cron)
  - Slack approval webhook server (port ASI_WEBHOOK_PORT, default 3000)

Both run on the same asyncio event loop.

Usage:
    python app.py
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
load_dotenv()

from config import load_settings
from utils.log import setup_logging

settings = load_settings()
setup_logging(level=settings.logging.level, json_format=True)
logger = logging.getLogger(__name__)


async def main() -> None:
    port = int(os.environ.get("ASI_WEBHOOK_PORT", "3000"))

    # Start scheduler (uses the running event loop via AsyncIOScheduler)
    from orchestrator.scheduler import build_scheduler
    scheduler = build_scheduler()
    scheduler.start()

    logger.info(
        "asi_app_starting",
        extra={
            "webhook_port": port,
            "cron": settings.scheduler.cron,
            "regions": settings.scheduler.default_regions,
        },
    )

    try:
        from approval.slack_bot import run_webhook_server
        await run_webhook_server(port=port)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("asi_app_stopped")


if __name__ == "__main__":
    asyncio.run(main())
