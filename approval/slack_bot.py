"""
Slack approval gate for Project ASI.

Two responsibilities:
  1. post_for_approval()  — called by pipeline after job completion.
     Posts one Block Kit message per content_piece (headline + 200-word excerpt,
     ✅ Approve / ❌ Reject buttons).

  2. run_webhook_server() — long-running aiohttp server that receives Slack
     interactive callback POSTs, updates the DB, and writes markdown on approval.

Environment variables required:
    SLACK_BOT_TOKEN         — xoxb-... OAuth bot token
    ASI_SLACK_CHANNEL_ID    — channel ID (not name) to post to
    ASI_SLACK_SIGNING_SECRET — used to verify Slack request signatures
    ASI_OUTPUT_DIR          — base output directory for markdown files
    ASI_WEBHOOK_PORT        — port for the webhook server (default: 3000)

Start the webhook server (called from Docker CMD or scheduler):
    python -m approval.slack_bot
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client() -> AsyncWebClient:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN is not set")
    return AsyncWebClient(token=token)


def _channel() -> str:
    ch = os.environ.get("ASI_SLACK_CHANNEL_ID", "")
    if not ch:
        raise RuntimeError("ASI_SLACK_CHANNEL_ID is not set")
    return ch


def _excerpt(body: str, max_words: int = 200) -> str:
    words = body.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


def _output_dir() -> Path:
    return Path(os.environ.get("ASI_OUTPUT_DIR", "/data/output"))


# ---------------------------------------------------------------------------
# 1. Post for approval
# ---------------------------------------------------------------------------

async def post_for_approval(
    content_pieces: list[dict],
    job_id: uuid.UUID,
) -> None:
    """
    Post one Slack Block Kit message per content_piece.

    Each dict in content_pieces must have:
        content_piece_id : str  — UUID of the content_piece row
        region_id        : str  — e.g. "EU"
        headline         : str
        body             : str  — full article markdown

    Silently skips if SLACK_BOT_TOKEN or ASI_SLACK_CHANNEL_ID are not set,
    so the pipeline never fails due to missing Slack config.
    """
    if not os.environ.get("SLACK_BOT_TOKEN") or not os.environ.get("ASI_SLACK_CHANNEL_ID"):
        logger.warning("slack_skipped", extra={"reason": "SLACK_BOT_TOKEN or ASI_SLACK_CHANNEL_ID not set"})
        return

    client = _client()
    channel = _channel()

    for piece in content_pieces:
        piece_id = str(piece["content_piece_id"])
        region = piece["region_id"]
        headline = piece["headline"]
        excerpt = _excerpt(piece["body"])

        blocks = _build_approval_blocks(
            job_id=str(job_id),
            piece_id=piece_id,
            region=region,
            headline=headline,
            excerpt=excerpt,
        )

        try:
            resp = await client.chat_postMessage(
                channel=channel,
                text=f"[ASI] New article ready for approval: {headline}",
                blocks=blocks,
            )
            logger.info(
                "slack_message_posted",
                extra={
                    "region": region,
                    "piece_id": piece_id,
                    "ts": resp.get("ts"),
                },
            )
        except Exception as exc:
            logger.error(
                "slack_post_failed",
                extra={"region": region, "piece_id": piece_id, "error": str(exc)},
            )


def _build_approval_blocks(
    job_id: str,
    piece_id: str,
    region: str,
    headline: str,
    excerpt: str,
) -> list[dict[str, Any]]:
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"[{region}] {headline[:150]}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": excerpt[:2900]},  # Slack block text limit
        },
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"approval_{piece_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": f"approve_{piece_id}",
                    "value": json.dumps({"job_id": job_id, "piece_id": piece_id, "region": region}),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "action_id": f"reject_{piece_id}",
                    "value": json.dumps({"job_id": job_id, "piece_id": piece_id, "region": region}),
                },
            ],
        },
    ]


# ---------------------------------------------------------------------------
# 2. Webhook server
# ---------------------------------------------------------------------------

def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Slack request signature to reject forged callbacks."""
    secret = os.environ.get("ASI_SLACK_SIGNING_SECRET", "")
    if not secret:
        logger.warning("slack_signature_check_skipped", extra={"reason": "ASI_SLACK_SIGNING_SECRET not set"})
        return True  # permissive in dev — enforce in production

    # Reject replays older than 5 minutes
    if abs(time.time() - float(timestamp)) > 300:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    computed = "v0=" + hmac.new(
        secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


async def _handle_interaction(payload: dict) -> None:
    """
    Process a Slack interactive component payload.
    Updates the DB and publishes markdown on approval.
    """
    from db.session import AsyncSessionLocal
    from db.models import ContentPiece
    from publishers.markdown_publisher import MarkdownPublisher

    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id: str = action.get("action_id", "")
    value = json.loads(action.get("value", "{}"))

    piece_id_str = value.get("piece_id", "")
    job_id_str = value.get("job_id", "")
    region = value.get("region", "unknown")

    if not piece_id_str:
        return

    piece_id = uuid.UUID(piece_id_str)
    approved = action_id.startswith("approve_")

    async with AsyncSessionLocal() as session:
        piece = await session.get(ContentPiece, piece_id)
        if not piece:
            logger.error("slack_callback_piece_not_found", extra={"piece_id": piece_id_str})
            return

        if approved:
            piece.status = "approved"
            await session.commit()

            publisher = MarkdownPublisher()
            out_path = publisher.publish(
                body=piece.body or "",
                job_id=job_id_str,
                region_id=region,
                output_dir=_output_dir(),
            )
            logger.info(
                "article_approved",
                extra={"piece_id": piece_id_str, "region": region, "path": str(out_path)},
            )
        else:
            piece.status = "rejected"
            await session.commit()
            logger.info(
                "article_rejected",
                extra={"piece_id": piece_id_str, "region": region},
            )

    # Acknowledge the button click by updating the Slack message
    response_url = payload.get("response_url")
    if response_url:
        import httpx
        status_text = "✅ Approved — markdown saved." if approved else "❌ Rejected."
        async with httpx.AsyncClient() as http:
            await http.post(
                response_url,
                json={"text": status_text, "replace_original": True},
            )


async def run_webhook_server(port: int = 3000, extra_setup=None) -> None:
    """
    Start the aiohttp webhook server to receive Slack interactive callbacks.
    Called from __main__ or from Docker CMD.

    extra_setup: optional callable(aiohttp.web.Application) called after core
                 routes are registered — used by app.py to add Metis cancel routes.
    """
    try:
        from aiohttp import web
    except ImportError:
        raise RuntimeError("aiohttp is required: pip install aiohttp")

    async def handle_slack(request: "web.Request") -> "web.Response":
        body = await request.read()
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")

        if not _verify_slack_signature(body, timestamp, signature):
            return web.Response(status=403, text="Invalid signature")

        form = await request.post()
        raw_payload = form.get("payload", "")
        if not raw_payload:
            return web.Response(status=400, text="No payload")

        payload = json.loads(raw_payload)
        await _handle_interaction(payload)
        return web.Response(status=200, text="OK")

    async def healthcheck(request: "web.Request") -> "web.Response":
        return web.Response(text="ASI approval webhook — OK")

    app = web.Application()
    app.router.add_get("/health", healthcheck)
    app.router.add_post("/slack/interactive", handle_slack)

    if extra_setup is not None:
        extra_setup(app)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("webhook_server_started", extra={"port": port})
    print(f"  ASI approval webhook listening on port {port}")
    print(f"  Slack interactive endpoint: POST /slack/interactive")

    # Run until interrupted
    import asyncio
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv
    from utils.log import setup_logging

    load_dotenv()
    setup_logging(json_format=False)

    port = int(os.environ.get("ASI_WEBHOOK_PORT", "3000"))
    asyncio.run(run_webhook_server(port=port))
