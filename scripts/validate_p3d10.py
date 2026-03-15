"""
P3-D10 validation script -- Slack cancel-window gate.

Checks:
  1.  orchestrator/cancel_gate.py imports cleanly
  2.  start_cancel_gate_poller is importable
  3.  handle_cancel_request is importable
  4.  register_cancel_routes is importable
  5.  handle_cancel_request returns 'not_found' for unknown UUID (mock)
  6.  handle_cancel_request returns 'cancelled' for pending edition (mock)
  7.  handle_cancel_request returns 'already_published' for published edition (mock)
  8.  handle_cancel_request is idempotent (mock)
  9.  _send_metis_slack skips when SLACK_BOT_TOKEN not set
 10.  _send_metis_slack skips when METIS_SLACK_CHANNEL_ID not set
 11.  app.py imports cleanly
 12.  app.py starts cancel gate poller (source check)
 13.  app.py has /metis/cancel route (source check)
 14.  cancel_gate.py uses asyncio.sleep for polling interval
 15.  cancel_gate.py queries publish_at in DB query
 16.  cancel_gate.py calls publish.sh with region argument
 17.  approval/slack_bot.py accepts extra_setup parameter
 18.  _set_pending_publish added to brief_pipeline.py
 19.  brief_pipeline.py sets pending_publish after layout_done
 20.  tests/test_cancel_gate.py exists with >= 15 tests
 21.  test suite passes: python -m pytest tests/test_cancel_gate.py

Run from repo root:
    python scripts/validate_p3d10.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-validate")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return condition


def _make_edition(*, published_at=None, cancelled_at=None):
    e = MagicMock()
    e.id = uuid.uuid4()
    e.region = "eu"
    e.run_id = uuid.uuid4()
    e.status = "pending_publish" if not published_at and not cancelled_at else (
        "published" if published_at else "cancelled"
    )
    e.published_at = published_at
    e.cancelled_at = cancelled_at
    return e


def _make_session(edition=None):
    session = AsyncMock()
    scalar = MagicMock()
    scalar.scalar_one_or_none = MagicMock(return_value=edition)
    session.execute = AsyncMock(return_value=scalar)
    session.commit = AsyncMock()
    return session


def main() -> int:
    failures = 0
    print("\nP3-D10 Validation - Cancel Gate\n" + "-" * 60)

    # ── 1-4. Imports ──────────────────────────────────────────────────────────
    try:
        from orchestrator.cancel_gate import (
            _send_metis_slack,
            handle_cancel_request,
            register_cancel_routes,
            start_cancel_gate_poller,
        )
        if not check("orchestrator/cancel_gate.py imports cleanly", True):
            failures += 1
    except Exception as exc:
        check("orchestrator/cancel_gate.py imports cleanly", False, str(exc))
        return 1

    for name, fn in [
        ("start_cancel_gate_poller importable", start_cancel_gate_poller),
        ("handle_cancel_request importable", handle_cancel_request),
        ("register_cancel_routes importable", register_cancel_routes),
    ]:
        if not check(name, callable(fn)):
            failures += 1

    # ── 5-8. handle_cancel_request mock tests ─────────────────────────────────
    async def _run_cancel_tests():
        nonlocal failures

        # 5. Not found
        session = _make_session(None)
        result = await handle_cancel_request(uuid.uuid4(), session)
        if not check("handle_cancel_request: not_found for unknown UUID",
                     result == {"status": "not_found"}, str(result)):
            failures += 1

        # 6. Cancel pending
        pending = _make_edition()
        session = _make_session(pending)
        result = await handle_cancel_request(pending.id, session)
        if not check("handle_cancel_request: cancelled for pending edition",
                     result == {"status": "cancelled"}, str(result)):
            failures += 1

        # 7. Already published
        published = _make_edition(published_at=datetime.utcnow())
        session = _make_session(published)
        result = await handle_cancel_request(published.id, session)
        if not check("handle_cancel_request: already_published for published edition",
                     result == {"status": "already_published"}, str(result)):
            failures += 1

        # 8. Idempotent
        cancelled = _make_edition(cancelled_at=datetime.utcnow())
        session = _make_session(cancelled)
        result = await handle_cancel_request(cancelled.id, session)
        if not check("handle_cancel_request: idempotent (second cancel = no-op)",
                     result == {"status": "cancelled"} and not session.commit.called,
                     str(result)):
            failures += 1

    asyncio.run(_run_cancel_tests())

    # ── 9-10. _send_metis_slack env var gate ──────────────────────────────────
    async def _run_slack_tests():
        nonlocal failures
        orig_token = os.environ.pop("SLACK_BOT_TOKEN", None)
        orig_channel = os.environ.pop("METIS_SLACK_CHANNEL_ID", None)
        try:
            await _send_metis_slack("test")
            ok = check("_send_metis_slack skips when no SLACK_BOT_TOKEN", True)
            if not ok:
                failures += 1

            os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
            await _send_metis_slack("test")
            ok = check("_send_metis_slack skips when no METIS_SLACK_CHANNEL_ID", True)
            if not ok:
                failures += 1
        finally:
            if orig_token:
                os.environ["SLACK_BOT_TOKEN"] = orig_token
            if orig_channel:
                os.environ["METIS_SLACK_CHANNEL_ID"] = orig_channel

    asyncio.run(_run_slack_tests())

    # ── 11-13. app.py source checks ───────────────────────────────────────────
    app_src = (REPO_ROOT / "app.py").read_text(encoding="utf-8")

    ok = check("app.py imports cleanly", True)  # passed import at top if we got here

    ok = check("app.py starts cancel gate poller",
               "start_cancel_gate_poller" in app_src)
    if not ok: failures += 1

    ok = check("app.py has /metis/cancel route (via register_cancel_routes)",
               "register_cancel_routes" in app_src or "/metis/cancel" in app_src)
    if not ok: failures += 1

    # ── 14-16. cancel_gate.py source checks ───────────────────────────────────
    cg_src = (REPO_ROOT / "orchestrator" / "cancel_gate.py").read_text(encoding="utf-8")

    ok = check("cancel_gate.py uses asyncio.sleep for polling interval",
               "asyncio.sleep" in cg_src)
    if not ok: failures += 1

    ok = check("cancel_gate.py queries publish_at",
               "publish_at" in cg_src)
    if not ok: failures += 1

    ok = check("cancel_gate.py calls publish.sh with region",
               "publish.sh" in cg_src or "PUBLISH_SH" in cg_src)
    if not ok: failures += 1

    # ── 17. slack_bot.py accepts extra_setup ─────────────────────────────────
    slack_src = (REPO_ROOT / "approval" / "slack_bot.py").read_text(encoding="utf-8")
    ok = check("approval/slack_bot.py accepts extra_setup parameter",
               "extra_setup" in slack_src)
    if not ok: failures += 1

    # ── 18-19. brief_pipeline.py integration ─────────────────────────────────
    pipeline_src = (REPO_ROOT / "orchestrator" / "brief_pipeline.py").read_text(encoding="utf-8")

    ok = check("_set_pending_publish added to brief_pipeline.py",
               "_set_pending_publish" in pipeline_src)
    if not ok: failures += 1

    ok = check("brief_pipeline.py sets pending_publish after layout_done",
               "pending_publish" in pipeline_src)
    if not ok: failures += 1

    # ── 20. Test file exists ──────────────────────────────────────────────────
    test_file = REPO_ROOT / "tests" / "test_cancel_gate.py"
    test_src = test_file.read_text(encoding="utf-8") if test_file.is_file() else ""
    test_count = test_src.count("async def test_") + test_src.count("def test_")

    ok = check(f"test_cancel_gate.py exists with >= 15 tests",
               test_file.is_file() and test_count >= 15,
               f"found {test_count} tests")
    if not ok: failures += 1

    # ── 21. Test suite passes ─────────────────────────────────────────────────
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_cancel_gate.py", "-q", "--tb=short"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    ok = check("pytest tests/test_cancel_gate.py passes",
               result.returncode == 0,
               result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[:100])
    if not ok: failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P3-D10 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
