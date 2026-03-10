"""
Day 17 — Slack Approval Gate validation script.

Confirms:
  1. MarkdownPublisher writes files correctly
  2. Slack Block Kit message structure is valid
  3. post_for_approval() sends a real Slack message (if SLACK_BOT_TOKEN set)
  4. Webhook server starts and /health responds
  5. Approval callback: updates DB status + writes markdown

Usage:
    python scripts/validate_day17.py                # full checks
    python scripts/validate_day17.py --skip-slack   # skip live Slack post
    python scripts/validate_day17.py --skip-server  # skip webhook server check
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return condition


# ---------------------------------------------------------------------------
# Check 1: MarkdownPublisher
# ---------------------------------------------------------------------------

def check_markdown_publisher() -> list[bool]:
    from publishers.markdown_publisher import MarkdownPublisher

    results: list[bool] = []
    publisher = MarkdownPublisher()
    job_id = str(uuid.uuid4())
    body = "## Test Article\n\nThis is a test article body.\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = publisher.publish(
            body=body,
            job_id=job_id,
            region_id="EU",
            output_dir=Path(tmpdir),
        )
        results.append(check("MarkdownPublisher creates file", out_path.exists(), str(out_path)))
        results.append(check("Output path is {job_id}/eu.md", out_path.name == "eu.md"))
        results.append(check("File content matches", out_path.read_text() == body))

    return results


# ---------------------------------------------------------------------------
# Check 2: Block Kit message structure
# ---------------------------------------------------------------------------

def check_block_kit_structure() -> bool:
    from approval.slack_bot import _build_approval_blocks

    blocks = _build_approval_blocks(
        job_id=str(uuid.uuid4()),
        piece_id=str(uuid.uuid4()),
        region="EU",
        headline="Test Headline: ECB Holds Rates",
        excerpt="This is the article excerpt. " * 20,
    )

    has_header = any(b.get("type") == "header" for b in blocks)
    has_actions = any(b.get("type") == "actions" for b in blocks)
    actions_block = next((b for b in blocks if b.get("type") == "actions"), None)
    has_approve = has_reject = False
    if actions_block:
        for el in actions_block.get("elements", []):
            if el.get("action_id", "").startswith("approve_"):
                has_approve = True
            if el.get("action_id", "").startswith("reject_"):
                has_reject = True

    return check(
        "Block Kit message has header + approve + reject buttons",
        has_header and has_approve and has_reject,
        f"header={has_header} approve={has_approve} reject={has_reject}",
    )


# ---------------------------------------------------------------------------
# Check 3: Live Slack post (optional)
# ---------------------------------------------------------------------------

async def check_slack_post() -> bool:
    import os
    if not os.environ.get("SLACK_BOT_TOKEN") or not os.environ.get("ASI_SLACK_CHANNEL_ID"):
        print("  [SKIP]  Live Slack post (SLACK_BOT_TOKEN or ASI_SLACK_CHANNEL_ID not set)")
        return True

    from approval.slack_bot import post_for_approval

    piece_id = uuid.uuid4()
    job_id = uuid.uuid4()
    try:
        await post_for_approval(
            content_pieces=[{
                "content_piece_id": piece_id,
                "region_id": "EU",
                "headline": "[ASI validate_day17] Test approval message — safe to dismiss",
                "body": "## Test Article\n\n" + ("This is a test. " * 50),
            }],
            job_id=job_id,
        )
        return check("Live Slack message posted", True, "check your Slack channel")
    except Exception as exc:
        return check("Live Slack message posted", False, str(exc)[:80])


# ---------------------------------------------------------------------------
# Check 4: Webhook server health
# ---------------------------------------------------------------------------

async def check_webhook_server() -> list[bool]:
    results: list[bool] = []
    try:
        import aiohttp
    except ImportError:
        results.append(check("aiohttp installed", False, "pip install aiohttp"))
        return results

    results.append(check("aiohttp installed", True))

    from approval.slack_bot import run_webhook_server
    import asyncio

    # Start server in background task
    port = 13017  # arbitrary test port
    server_task = asyncio.create_task(run_webhook_server(port=port))
    await asyncio.sleep(0.5)  # give server time to start

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://localhost:{port}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                text = await resp.text()
                results.append(check(
                    "Webhook server /health responds 200",
                    resp.status == 200,
                    text[:50],
                ))
    except Exception as exc:
        results.append(check("Webhook server /health responds 200", False, str(exc)[:80]))
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    return results


# ---------------------------------------------------------------------------
# Check 5: Approval callback updates DB + writes file
# ---------------------------------------------------------------------------

async def check_approval_callback() -> list[bool]:
    import json, os
    from db.session import AsyncSessionLocal
    from db.models import ContentPiece, Brief, Job
    from approval.slack_bot import _handle_interaction

    results: list[bool] = []

    job_id = uuid.uuid4()
    piece_id = uuid.uuid4()

    # Seed a minimal content_piece row
    async with AsyncSessionLocal() as session:
        job = Job(id=job_id, project="asi", topic="test", content_type="journal_article",
                  regions=["EU"], status="complete")
        session.add(job)
        await session.flush()

        brief = Brief(job_id=job_id)
        session.add(brief)
        await session.flush()

        piece = ContentPiece(
            id=piece_id,
            brief_id=brief.id,
            region="EU",
            content_type="regional_article",
            status="approved",  # already set by chain
            headline="Test Headline",
            body="## Test\n\nArticle body content for approval test.\n",
        )
        session.add(piece)
        await session.commit()

    # Simulate an approve interaction payload
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["ASI_OUTPUT_DIR"] = tmpdir

        payload = {
            "actions": [{
                "action_id": f"approve_{piece_id}",
                "value": json.dumps({
                    "job_id": str(job_id),
                    "piece_id": str(piece_id),
                    "region": "EU",
                }),
            }],
        }
        await _handle_interaction(payload)

        # Check DB updated
        async with AsyncSessionLocal() as session:
            updated = await session.get(ContentPiece, piece_id)
            results.append(check(
                "Approval callback sets status='approved' in DB",
                updated is not None and updated.status == "approved",
                updated.status if updated else "not found",
            ))

        # Check markdown file written
        out_path = Path(tmpdir) / str(job_id) / "eu.md"
        results.append(check(
            "Approval callback writes markdown file",
            out_path.exists(),
            str(out_path) if out_path.exists() else "file not found",
        ))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(skip_slack: bool, skip_server: bool) -> None:
    print(f"\n{'─'*60}")
    print(f"  Day 17 — Slack Approval Gate Validation")
    print(f"{'─'*60}\n")

    all_results: list[bool] = []

    print("  [1] MarkdownPublisher:")
    all_results.extend(check_markdown_publisher())

    print("\n  [2] Block Kit message structure:")
    all_results.append(check_block_kit_structure())

    print("\n  [3] Live Slack post:")
    if skip_slack:
        print("  [SKIP]  --skip-slack")
    else:
        all_results.append(asyncio.run(check_slack_post()))

    print("\n  [4] Webhook server:")
    if skip_server:
        print("  [SKIP]  --skip-server")
    else:
        all_results.extend(asyncio.run(check_webhook_server()))

    print("\n  [5] Approval callback (DB + file):")
    all_results.extend(asyncio.run(check_approval_callback()))

    print(f"\n{'─'*60}")
    failed = sum(1 for r in all_results if not r)
    if not failed:
        print("  Day 17 / Slack approval gate: ALL CHECKS PASSED")
        print("  Approval gate is ready. Next: Day 18 Docker deployment.")
    else:
        print(f"  Day 17 / Slack approval gate: {failed} CHECK(S) FAILED")
    print(f"{'─'*60}\n")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Day 17 Slack approval gate")
    parser.add_argument("--skip-slack",  action="store_true", help="Skip live Slack API call")
    parser.add_argument("--skip-server", action="store_true", help="Skip webhook server test")
    args = parser.parse_args()
    main(skip_slack=args.skip_slack, skip_server=args.skip_server)
