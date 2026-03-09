"""
APScheduler daily cron — implemented Day 19.

This placeholder keeps the container alive so manual CLI runs work during
Sprint 2 and Sprint 3. Replaced with the real scheduler on Day 19.
"""

import logging
import time

from dotenv import load_dotenv
load_dotenv()

from config import load_settings
from utils.log import setup_logging

settings = load_settings()
setup_logging(level=settings.logging.level, json_format=True)

logger = logging.getLogger(__name__)
logger.info("asi_scheduler_placeholder_running", extra={"note": "real scheduler implemented Day 19"})

# Keep container alive for manual `docker compose exec asi-app python cli.py run ...`
while True:
    time.sleep(3600)
