"""
Metis cancel-window gate.

Architecture:
  - Pipeline sets edition.publish_at = now() + 30min, status = 'pending_publish'
    after HtmlPublisher writes the HTML locally.
  - start_cancel_gate_poller() runs as a background asyncio task in app.py.
    Every 30 seconds it queries editions whose publish_at has passed and
    triggers rsync via deploy/publish.sh.
  - handle_cancel_request() is the webhook handler for cancel button clicks.
    It transitions the edition to 'cancelled' before rsync fires.
  - Container restart safe: the poller re-queries DB on every tick — no
    in-flight state is stored in memory.

Fallback when SLACK_BOT_TOKEN is not set:
  The pipeline sets publish_at = now() (no delay). The poller fires immediately
  on its next 30-second tick. The 30-minute cancel window is bypassed and a
  WARNING is logged. This allows local testing without Slack credentials.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_v2 import Asi2Edition
from db.session import AsyncSessionLocal

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
PUBLISH_SH = REPO_ROOT / "deploy" / "publish.sh"

# ── Public API ────────────────────────────────────────────────────────────────


async def start_cancel_gate_poller(interval_seconds: int = 30) -> None:
    """
    Long-running coroutine. Start with asyncio.create_task() in app.py.

    Polls asi2_editions every `interval_seconds` for editions that:
      - status = 'pending_publish'
      - publish_at <= now()

    For each: calls deploy/publish.sh {region} and transitions to 'published'
    or 'failed' depending on the rsync exit code.

    Never raises — all per-iteration exceptions are caught and logged.
    """
    logger.info("cancel_gate_poller_started", extra={"interval_seconds": interval_seconds})

    while True:
        try:
            async with AsyncSessionLocal() as session:
                await _process_ready_editions(session)
        except Exception as exc:
            logger.error(
                "cancel_gate_poll_error",
                extra={"error": str(exc)},
                exc_info=True,
            )
        await asyncio.sleep(interval_seconds)


async def handle_cancel_request(
    edition_id: uuid.UUID,
    session: AsyncSession,
) -> dict:
    """
    Cancel an edition before it is published.

    Returns:
        {"status": "cancelled"}         — successfully cancelled
        {"status": "already_published"} — rsync already ran, too late
        {"status": "not_found"}         — unknown edition_id

    Idempotent: calling cancel twice on the same edition returns
    {"status": "cancelled"} both times (no-op on second call).
    """
    result = await session.execute(
        select(Asi2Edition).where(Asi2Edition.id == edition_id)
    )
    edition = result.scalar_one_or_none()

    if edition is None:
        return {"status": "not_found"}

    if edition.published_at is not None:
        return {"status": "already_published"}

    # Idempotent: already cancelled → return success without re-writing
    if edition.cancelled_at is not None:
        return {"status": "cancelled"}

    now = datetime.utcnow()
    await session.execute(
        sa_update(Asi2Edition)
        .where(Asi2Edition.id == edition_id)
        .values(status="cancelled", cancelled_at=now)
    )
    await session.commit()

    logger.info(
        "cancel_gate_edition_cancelled",
        extra={"edition_id": str(edition_id), "region": edition.region},
    )
    return {"status": "cancelled"}


def register_cancel_routes(aiohttp_app) -> None:
    """
    Register the /metis/cancel/{edition_id} POST route on an aiohttp app.
    Called from app.py after building the aiohttp Application.
    """
    from aiohttp import web

    async def handle_cancel(request: web.Request) -> web.Response:
        edition_id_str = request.match_info.get("edition_id", "")
        try:
            edition_id = uuid.UUID(edition_id_str)
        except ValueError:
            return web.json_response({"error": "invalid edition_id"}, status=400)

        async with AsyncSessionLocal() as session:
            result = await handle_cancel_request(edition_id, session)

        status_code = 200
        if result["status"] == "not_found":
            status_code = 404
        elif result["status"] == "already_published":
            status_code = 409

        return web.json_response(result, status=status_code)

    aiohttp_app.router.add_post("/metis/cancel/{edition_id}", handle_cancel)
    logger.info("cancel_gate_routes_registered")


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _process_ready_editions(session: AsyncSession) -> None:
    """
    Query all pending_publish editions whose publish_at <= now() and publish each.
    Skips editions that have been cancelled since the window opened.
    """
    now = datetime.utcnow()

    result = await session.execute(
        select(Asi2Edition).where(
            Asi2Edition.status == "pending_publish",
            Asi2Edition.publish_at <= now,
        )
    )
    editions = result.scalars().all()

    if not editions:
        return

    logger.info("cancel_gate_processing", extra={"count": len(editions)})

    for edition in editions:
        await _publish_edition(edition, session)


async def _publish_edition(edition: Asi2Edition, session: AsyncSession) -> None:
    """
    Run deploy/publish.sh {region} via asyncio subprocess.

    On success: set published_at + status = 'published', send Slack confirmation.
    On failure: set status = 'failed', send Slack alert.
    Timeout: 120 seconds.
    """
    region = edition.region
    logger.info("cancel_gate_publishing", extra={"edition_id": str(edition.id), "region": region})

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", str(PUBLISH_SH), region,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(f"publish.sh timed out after 120s for region {region}")

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"publish.sh exited {proc.returncode} for {region}: {err_msg}"
            )

    except Exception as exc:
        logger.error(
            "cancel_gate_publish_failed",
            extra={"region": region, "edition_id": str(edition.id), "error": str(exc)},
        )
        await session.execute(
            sa_update(Asi2Edition)
            .where(Asi2Edition.id == edition.id)
            .values(status="failed")
        )
        await session.commit()
        await _send_metis_slack(f"Metis: deploy failed for {region}. Check logs.")
        return

    # Success
    now = datetime.utcnow()
    await session.execute(
        sa_update(Asi2Edition)
        .where(Asi2Edition.id == edition.id)
        .values(status="published", published_at=now)
    )
    await session.commit()

    logger.info(
        "cancel_gate_published",
        extra={"region": region, "edition_id": str(edition.id)},
    )
    await _send_metis_slack(
        f"Metis: {region.upper()} edition published — {edition.run_id}"
    )


async def _send_metis_slack(message: str) -> None:
    """
    Post an alert to the Metis Slack channel (METIS_SLACK_CHANNEL_ID).

    Silently skips if SLACK_BOT_TOKEN or METIS_SLACK_CHANNEL_ID are not set.
    Never raises — Slack failure must not block publish flow.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("METIS_SLACK_CHANNEL_ID", "")
    if not token or not channel:
        logger.warning(
            "metis_slack_skipped",
            extra={"reason": "SLACK_BOT_TOKEN or METIS_SLACK_CHANNEL_ID not set",
                   "alert_text": message[:100]},
        )
        return
    try:
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=token)
        await client.chat_postMessage(channel=channel, text=message)
        logger.info("metis_slack_sent", extra={"alert_text": message[:100]})
    except Exception as exc:
        logger.error("metis_slack_failed", extra={"error": str(exc), "alert_text": message[:100]})
