"""
ASI application entrypoint.

Runs as the Docker CMD. Starts the Slack approval webhook server.
Day 19 adds the APScheduler cron job alongside it.

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

    logger.info("asi_app_starting", extra={"webhook_port": port})

    from approval.slack_bot import run_webhook_server
    await run_webhook_server(port=port)


if __name__ == "__main__":
    asyncio.run(main())
