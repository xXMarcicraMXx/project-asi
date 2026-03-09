"""
Structured logging setup for ASI.

Call setup_logging() once at process startup (cli.py / scheduler.py).
Level and format are driven by config/settings.yaml.

In JSON mode (Docker / VPS) every log record is a one-line JSON object:
  {"ts": "...", "level": "INFO", "logger": "...", "msg": "..."}

In plain mode (local dev) the standard format is used.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Minimal JSON formatter — no external dependencies."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via logger.info("msg", extra={...})
        _STDLIB_ATTRS = logging.LogRecord.__dict__.keys() | {
            "message", "asctime", "exc_text", "stack_info",
        }
        for key, value in record.__dict__.items():
            if key not in _STDLIB_ATTRS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO", json_format: bool = True) -> None:
    """
    Configure root logger.  Call once at startup.

    Args:
        level:       Log level string — "DEBUG" | "INFO" | "WARNING" | "ERROR"
        json_format: True → JSON lines (production); False → human-readable
    """
    handler = logging.StreamHandler(sys.stdout)

    if json_format:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
        )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any handlers added by libraries before our setup runs
    root.handlers.clear()
    root.addHandler(handler)

    # Suppress noisy library loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
