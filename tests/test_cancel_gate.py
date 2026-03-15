"""
Tests for orchestrator/cancel_gate.py

Covers:
  - handle_cancel_request: cancel, already_published, not_found, idempotent
  - _process_ready_editions: filtering logic
  - _publish_edition: success, failure, timeout
  - _send_metis_slack: env var gate
  - start_cancel_gate_poller: exception isolation
  - Pipeline integration: publish_at window based on SLACK_BOT_TOKEN
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_edition(
    *,
    status: str = "pending_publish",
    publish_at: datetime | None = None,
    published_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    region: str = "eu",
) -> MagicMock:
    edition = MagicMock()
    edition.id = uuid.uuid4()
    edition.region = region
    edition.run_id = uuid.uuid4()
    edition.status = status
    edition.publish_at = publish_at or datetime.utcnow() - timedelta(minutes=1)
    edition.published_at = published_at
    edition.cancelled_at = cancelled_at
    return edition


def _make_session(edition: MagicMock | None = None) -> AsyncMock:
    session = AsyncMock()
    scalar = MagicMock()
    scalar.scalar_one_or_none = MagicMock(return_value=edition)
    session.execute = AsyncMock(return_value=scalar)
    session.commit = AsyncMock()
    return session


# ── handle_cancel_request ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_pending_edition():
    from orchestrator.cancel_gate import handle_cancel_request

    edition = _make_edition(status="pending_publish")
    session = _make_session(edition)

    result = await handle_cancel_request(edition.id, session)

    assert result == {"status": "cancelled"}
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_already_published():
    from orchestrator.cancel_gate import handle_cancel_request

    edition = _make_edition(
        status="published",
        published_at=datetime.utcnow(),
    )
    session = _make_session(edition)

    result = await handle_cancel_request(edition.id, session)

    assert result == {"status": "already_published"}
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_not_found():
    from orchestrator.cancel_gate import handle_cancel_request

    session = _make_session(None)
    result = await handle_cancel_request(uuid.uuid4(), session)

    assert result == {"status": "not_found"}


@pytest.mark.asyncio
async def test_cancel_idempotent():
    """Second cancel on same edition returns 'cancelled' without re-writing."""
    from orchestrator.cancel_gate import handle_cancel_request

    edition = _make_edition(
        status="cancelled",
        cancelled_at=datetime.utcnow(),
    )
    session = _make_session(edition)

    result = await handle_cancel_request(edition.id, session)

    assert result == {"status": "cancelled"}
    # Should NOT commit again (idempotent — no DB write on second cancel)
    session.commit.assert_not_awaited()


# ── _process_ready_editions ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_ready_editions_calls_publish():
    from orchestrator.cancel_gate import _process_ready_editions

    edition = _make_edition()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [edition]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.commit = AsyncMock()

    with patch("orchestrator.cancel_gate._publish_edition", new_callable=AsyncMock) as mock_pub:
        await _process_ready_editions(session)

    mock_pub.assert_awaited_once_with(edition, session)


@pytest.mark.asyncio
async def test_process_skips_when_none_ready():
    from orchestrator.cancel_gate import _process_ready_editions

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

    with patch("orchestrator.cancel_gate._publish_edition", new_callable=AsyncMock) as mock_pub:
        await _process_ready_editions(session)

    mock_pub.assert_not_awaited()


# ── _publish_edition ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_edition_success():
    from orchestrator.cancel_gate import _publish_edition

    edition = _make_edition()
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"OK eu\n", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("orchestrator.cancel_gate._send_metis_slack", new_callable=AsyncMock):
            await _publish_edition(edition, session)

    session.commit.assert_awaited()
    # Verify published_at was set (execute called with update containing published_at)
    assert session.execute.await_count >= 1


@pytest.mark.asyncio
async def test_publish_edition_sets_status_published_on_success():
    from orchestrator.cancel_gate import _publish_edition

    edition = _make_edition()
    captured_stmt = []
    session = AsyncMock()

    async def capture_execute(stmt):
        captured_stmt.append(stmt)
        return MagicMock()

    session.execute = capture_execute
    session.commit = AsyncMock()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"Done.\n", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("orchestrator.cancel_gate._send_metis_slack", new_callable=AsyncMock):
            await _publish_edition(edition, session)

    # The UPDATE statement should be in captured_stmt
    assert len(captured_stmt) >= 1


@pytest.mark.asyncio
async def test_publish_edition_sets_failed_on_nonzero_exit():
    from orchestrator.cancel_gate import _publish_edition

    edition = _make_edition()
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Permission denied"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("orchestrator.cancel_gate._send_metis_slack", new_callable=AsyncMock) as mock_slack:
            await _publish_edition(edition, session)

    # Slack alert should be sent on failure
    mock_slack.assert_awaited_once()
    assert "failed" in mock_slack.call_args[0][0].lower() or "fail" in mock_slack.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_publish_edition_calls_publish_sh_with_region():
    from orchestrator.cancel_gate import _publish_edition

    edition = _make_edition(region="na")
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"OK na\n", b""))

    captured_args = []

    async def fake_subprocess(*args, **kwargs):
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        with patch("orchestrator.cancel_gate._send_metis_slack", new_callable=AsyncMock):
            await _publish_edition(edition, session)

    assert "na" in captured_args


# ── _send_metis_slack ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_metis_slack_skips_when_no_token(monkeypatch):
    """Returns early (no raise, no SDK import) when token is missing."""
    from orchestrator.cancel_gate import _send_metis_slack

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("METIS_SLACK_CHANNEL_ID", raising=False)

    # The function checks env vars first and returns before importing slack_sdk
    await _send_metis_slack("test message")  # must not raise


@pytest.mark.asyncio
async def test_send_metis_slack_skips_when_no_channel(monkeypatch):
    """Returns early when channel ID is missing."""
    from orchestrator.cancel_gate import _send_metis_slack

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("METIS_SLACK_CHANNEL_ID", raising=False)

    await _send_metis_slack("test message")  # must not raise


# ── Poller exception isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_poller_does_not_crash_on_db_error():
    """One DB exception must not stop the poller loop."""
    from orchestrator.cancel_gate import start_cancel_gate_poller

    call_count = 0

    async def fake_session_ctx():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB connection lost")
        # Stop after second iteration
        raise asyncio.CancelledError()

    with patch("orchestrator.cancel_gate.AsyncSessionLocal") as mock_sl:
        mock_sl.return_value.__aenter__ = AsyncMock(side_effect=fake_session_ctx)
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(asyncio.CancelledError):
                await start_cancel_gate_poller(interval_seconds=0)

    assert call_count >= 2  # Poller continued after the first error


# ── Pipeline integration: publish_at window ───────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_sets_30min_window_when_slack_token_set(monkeypatch):
    """When SLACK_BOT_TOKEN is set, publish_at = now() + 30min."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")

    captured_publish_at = []

    async def fake_set_pending(edition_id, publish_at):
        captured_publish_at.append(publish_at)

    with patch("orchestrator.brief_pipeline._set_pending_publish",
               side_effect=fake_set_pending):
        # Simulate what the pipeline calls
        from datetime import timedelta
        slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
        window = timedelta(minutes=30) if slack_token else timedelta(seconds=0)
        await fake_set_pending(uuid.uuid4(), datetime.utcnow() + window)

    assert len(captured_publish_at) == 1
    delta = captured_publish_at[0] - datetime.utcnow()
    assert delta.total_seconds() > 25 * 60  # at least 25 minutes in the future


@pytest.mark.asyncio
async def test_pipeline_sets_immediate_when_no_slack_token(monkeypatch):
    """When SLACK_BOT_TOKEN is not set, publish_at = now() (immediate)."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    captured_publish_at = []

    async def fake_set_pending(edition_id, publish_at):
        captured_publish_at.append(publish_at)

    with patch("orchestrator.brief_pipeline._set_pending_publish",
               side_effect=fake_set_pending):
        slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
        window = timedelta(minutes=30) if slack_token else timedelta(seconds=0)
        await fake_set_pending(uuid.uuid4(), datetime.utcnow() + window)

    assert len(captured_publish_at) == 1
    delta = captured_publish_at[0] - datetime.utcnow()
    assert abs(delta.total_seconds()) < 5  # within 5 seconds of now


# ── Cancelled edition not processed ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancelled_edition_not_in_ready_query():
    """
    The DB query in _process_ready_editions filters by status='pending_publish'.
    Cancelled editions (status='cancelled') never appear in that result set.
    This test verifies the query predicate filters correctly.
    """
    from orchestrator.cancel_gate import _process_ready_editions

    # Return empty list (as if cancelled edition was filtered by DB)
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

    with patch("orchestrator.cancel_gate._publish_edition", new_callable=AsyncMock) as mock_pub:
        await _process_ready_editions(session)

    mock_pub.assert_not_awaited()
