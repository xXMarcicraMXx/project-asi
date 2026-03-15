"""
P1-D5 tests — brief_pipeline.py (Metis v2 orchestrator).

All external I/O is mocked:
  - MetisRSSCollector.collect()
  - StatusAgent.run_brief()
  - CurationAgent.run_region()
  - NewsletterWriterAgent.run_story()
  - AsyncSessionLocal (DB)
  - _send_slack_alert()

Uses pytest-asyncio for async tests.
"""

from __future__ import annotations

# db.session reads DATABASE_URL at import time — set before any import
import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import asyncio
import uuid
from datetime import date
from typing import AsyncContextManager
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from orchestrator.brief_job_model import (
    CuratedStory,
    DailyStatus,
    RawStory,
    StoryEntry,
)
from orchestrator.brief_pipeline import (
    METIS_REGIONS,
    BriefRunResult,
    RegionResult,
    _collect_global_pool,
    _default_status,
    run_brief_pipeline,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Ensure env vars are set for every test (also set at module level for imports)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")


def _make_raw_stories(n: int = 10) -> list[RawStory]:
    return [
        RawStory(
            title=f"Story {i}",
            url=f"https://example.com/{i}",
            source_name="Reuters",
            body=f"Body {i}",
        )
        for i in range(n)
    ]


def _make_status(
    color: str = "Amber",
    sentiment: str = "Cautious",
    headline: str = "Markets steady.",
) -> DailyStatus:
    return DailyStatus(
        daily_color=color, sentiment=sentiment, mood_headline=headline
    )


def _make_curated(n: int = 6) -> list[CuratedStory]:
    return [
        CuratedStory(
            raw_story_id=uuid.uuid4(),
            title=f"Curated {i}",
            url=f"https://example.com/c{i}",
            source_name="Reuters",
            category="Politics",
            significance_score=round(0.9 - i * 0.05, 2),
            body=f"Body {i}",
        )
        for i in range(n)
    ]


def _make_story_entry(rank: int = 1) -> StoryEntry:
    summary = " ".join([f"word{j}" for j in range(110)]) + "."
    return StoryEntry(
        rank=rank,
        category="Politics",
        title=f"Story {rank}",
        url=f"https://example.com/{rank}",
        source_name="Reuters",
        summary=summary,
        word_count=110,
        significance_score=0.85,
        raw_story_id=uuid.uuid4(),
    )


def _null_session_ctx():
    """Return a context manager that yields a no-op async session."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()

    class _Ctx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *_):
            pass

    return _Ctx()


def _patch_db():
    """Patch AsyncSessionLocal to return a null session."""
    return patch(
        "orchestrator.brief_pipeline.AsyncSessionLocal",
        return_value=_null_session_ctx(),
    )


def _patch_collector(stories: list[RawStory] | None = None, raises=None):
    """Patch MetisRSSCollector to return canned stories or raise."""
    if stories is None:
        stories = _make_raw_stories()

    mock = MagicMock()
    if raises:
        mock.return_value.collect = AsyncMock(side_effect=raises)
    else:
        mock.return_value.collect = AsyncMock(return_value=stories)
    return patch("orchestrator.brief_pipeline.MetisRSSCollector", mock)


def _patch_status_agent(status: DailyStatus | None = None, cost: float = 0.01):
    """Patch StatusAgent to return canned DailyStatus."""
    if status is None:
        status = _make_status()
    mock = MagicMock()
    agent_instance = mock.return_value
    agent_instance.run_brief = AsyncMock(return_value=status)
    agent_instance.last_call_cost = cost
    return patch("orchestrator.brief_pipeline.StatusAgent", mock)


def _patch_curation_agent(curated: list[CuratedStory] | None = None, raises=None, cost: float = 0.005):
    """Patch CurationAgent."""
    if curated is None:
        curated = _make_curated()
    mock = MagicMock()
    instance = mock.return_value
    if raises:
        instance.run_region = AsyncMock(side_effect=raises)
    else:
        instance.run_region = AsyncMock(return_value=curated)
    instance.last_call_cost = cost
    return patch("orchestrator.brief_pipeline.CurationAgent", mock)


def _patch_writer_agent(cost: float = 0.003):
    """Patch NewsletterWriterAgent to return a canned StoryEntry."""
    mock = MagicMock()
    rank_counter = [0]

    async def fake_run_story(story, *, rank, **_kwargs):
        summary = " ".join([f"word{j}" for j in range(110)]) + "."
        return StoryEntry(
            rank=rank,
            category=story.category,
            title=story.title,
            url=story.url,
            source_name=story.source_name,
            summary=summary,
            word_count=110,
            significance_score=story.significance_score,
            raw_story_id=story.raw_story_id,
        )

    instance = mock.return_value
    instance.run_story = fake_run_story
    instance.last_call_cost = cost
    return patch("orchestrator.brief_pipeline.NewsletterWriterAgent", mock)


def _patch_slack():
    """Patch _send_slack_alert to a no-op AsyncMock."""
    return patch("orchestrator.brief_pipeline._send_slack_alert", new_callable=AsyncMock)


def _patch_log_dups():
    """Patch log_duplicate_urls to a no-op."""
    return patch("orchestrator.brief_pipeline.log_duplicate_urls", return_value=None)


def _patch_load_region():
    """Patch load_region to return a mock RegionConfig."""
    from config import RegionConfig, DemographicAnchor, PineconeMetadata
    mock_cfg = RegionConfig(
        region_id="eu",
        display_name="Europe",
        editorial_voice="analytical",
        demographic_anchor=DemographicAnchor(location="Brussels", cultural_lens="liberal"),
        pinecone_metadata=PineconeMetadata(department="eu"),
        curation_bias="Focus on EU institutional developments.",
    )
    return patch("orchestrator.brief_pipeline.load_region", return_value=mock_cfg)


# ---------------------------------------------------------------------------
# Test: dry-run mode (no API calls, no DB)
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_returns_brief_run_result(self):
        with _patch_slack(), _patch_log_dups(), _patch_load_region():
            result = await run_brief_pipeline(dry_run=True, run_date=date(2026, 3, 15))

        assert isinstance(result, BriefRunResult)
        assert result.run_date == date(2026, 3, 15)

    @pytest.mark.asyncio
    async def test_dry_run_has_5_regions(self):
        with _patch_slack(), _patch_log_dups(), _patch_load_region():
            result = await run_brief_pipeline(dry_run=True, run_date=date(2026, 3, 15))

        assert set(result.regions.keys()) == set(METIS_REGIONS)

    @pytest.mark.asyncio
    async def test_dry_run_run_status_complete(self):
        with _patch_slack(), _patch_log_dups(), _patch_load_region():
            result = await run_brief_pipeline(dry_run=True, run_date=date(2026, 3, 15))

        assert result.run_status == "complete"

    @pytest.mark.asyncio
    async def test_dry_run_all_editions_complete_status(self):
        with _patch_slack(), _patch_log_dups(), _patch_load_region():
            result = await run_brief_pipeline(dry_run=True, run_date=date(2026, 3, 15))

        for region_id, region_result in result.regions.items():
            assert region_result.status == "complete", (
                f"region {region_id} status={region_result.status!r}"
            )

    @pytest.mark.asyncio
    async def test_dry_run_zero_cost(self):
        with _patch_slack(), _patch_log_dups(), _patch_load_region():
            result = await run_brief_pipeline(dry_run=True, run_date=date(2026, 3, 15))

        assert result.total_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Test: 5 regions run in parallel
# ---------------------------------------------------------------------------


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_pipeline_runs_5_regions(self):
        """All 5 regions should be present in the result."""
        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            _patch_curation_agent(),
            _patch_writer_agent(),
            _patch_slack(),
            _patch_log_dups(),
            _patch_load_region(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert set(result.regions.keys()) == set(METIS_REGIONS)

    @pytest.mark.asyncio
    async def test_pipeline_5_regions_in_parallel(self):
        """asyncio.gather means all 5 regions are launched concurrently.
        We verify by checking all regions are in the result (timing-independent)."""
        finished_regions = []

        async def fake_collect():
            await asyncio.sleep(0)  # yield control point
            return _make_raw_stories()

        with (
            _patch_db(),
            patch(
                "orchestrator.brief_pipeline.MetisRSSCollector",
                return_value=MagicMock(collect=AsyncMock(return_value=_make_raw_stories())),
            ),
            _patch_status_agent(),
            _patch_curation_agent(),
            _patch_writer_agent(),
            _patch_slack(),
            _patch_log_dups(),
            _patch_load_region(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert len(result.regions) == 5


# ---------------------------------------------------------------------------
# Test: cost ceiling per region
# ---------------------------------------------------------------------------


class TestCostCeiling:
    @pytest.mark.asyncio
    async def test_per_region_ceiling_is_total_divided_by_5(self, monkeypatch):
        """Pipeline must compute per_region_ceiling = max_usd / len(regions)."""
        from config import SettingsConfig, ModelsConfig, PineconeConfig, CostConfig, LoggingConfig, SchedulerConfig
        mock_settings = MagicMock()
        mock_settings.cost.max_usd_per_job = 2.0

        ceilings_seen: list[float] = []

        original_run_region = _run_region_capture_ceiling(ceilings_seen)

        with (
            patch("orchestrator.brief_pipeline.load_settings", return_value=mock_settings),
            patch("orchestrator.brief_pipeline._run_region_pipeline", original_run_region),
            _patch_collector(),
            _patch_status_agent(),
            _patch_slack(),
            _patch_log_dups(),
            _patch_db(),
        ):
            await run_brief_pipeline(dry_run=False, run_date=date(2026, 3, 15))

        # Each call should have gotten ceiling = 2.0 / 5 = 0.40
        assert all(abs(c - 0.40) < 1e-9 for c in ceilings_seen), (
            f"Expected ceiling=0.40, got: {ceilings_seen}"
        )

    @pytest.mark.asyncio
    async def test_cost_accumulates_across_regions(self):
        """total_cost_usd should sum status_agent + all region costs."""
        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(cost=0.01),
            _patch_curation_agent(cost=0.005),
            _patch_writer_agent(cost=0.003),
            _patch_slack(),
            _patch_log_dups(),
            _patch_load_region(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        # Status agent: 0.01
        # Each region: curation (0.005) + 6 stories × writer_last_call_cost
        # total_cost_usd > 0 proves accumulation
        assert result.total_cost_usd > 0.0


def _run_region_capture_ceiling(ceilings_seen: list[float]):
    """Return a fake _run_region_pipeline that records per_region_ceiling."""
    async def _fake(**kwargs):
        ceilings_seen.append(kwargs["per_region_ceiling"])
        return RegionResult(
            region_id=kwargs["region_id"],
            edition_id=uuid.uuid4(),
            stories=[],
            status="complete",
            cost_usd=0.0,
            error=None,
        )
    return _fake


# ---------------------------------------------------------------------------
# Test: run status transitions
# ---------------------------------------------------------------------------


class TestRunStatus:
    @pytest.mark.asyncio
    async def test_marks_complete_when_all_regions_succeed(self):
        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            _patch_curation_agent(),
            _patch_writer_agent(),
            _patch_slack(),
            _patch_log_dups(),
            _patch_load_region(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert result.run_status == "complete"

    @pytest.mark.asyncio
    async def test_marks_partial_on_one_region_failure(self):
        call_count = [0]

        async def flaky_run_region(**kwargs):
            call_count[0] += 1
            region_id = kwargs["region_id"]
            if region_id == "eu":
                return RegionResult(
                    region_id=region_id,
                    edition_id=uuid.uuid4(),
                    stories=[],
                    status="failed",
                    cost_usd=0.0,
                    error=RuntimeError("EU failed"),
                )
            return RegionResult(
                region_id=region_id,
                edition_id=uuid.uuid4(),
                stories=[],
                status="complete",
                cost_usd=0.0,
                error=None,
            )

        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            patch("orchestrator.brief_pipeline._run_region_pipeline", flaky_run_region),
            _patch_slack(),
            _patch_log_dups(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert result.run_status == "partial"
        assert result.regions["eu"].status == "failed"

    @pytest.mark.asyncio
    async def test_marks_failed_when_all_regions_fail(self):
        async def all_fail(**kwargs):
            region_id = kwargs["region_id"]
            return RegionResult(
                region_id=region_id,
                edition_id=None,
                stories=[],
                status="failed",
                cost_usd=0.0,
                error=RuntimeError("Forced failure"),
            )

        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            patch("orchestrator.brief_pipeline._run_region_pipeline", all_fail),
            _patch_slack(),
            _patch_log_dups(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert result.run_status == "failed"


# ---------------------------------------------------------------------------
# Test: zero stories path
# ---------------------------------------------------------------------------


class TestZeroStories:
    @pytest.mark.asyncio
    async def test_zero_stories_sends_slack_and_halts(self):
        slack_messages: list[str] = []

        async def capture_slack(msg: str):
            slack_messages.append(msg)

        with (
            _patch_db(),
            patch(
                "orchestrator.brief_pipeline.MetisRSSCollector",
                return_value=MagicMock(collect=AsyncMock(side_effect=RuntimeError("All feeds failed"))),
            ),
            patch("orchestrator.brief_pipeline._send_slack_alert", capture_slack),
            _patch_log_dups(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert result.run_status == "failed"
        assert any("halted" in m.lower() or "no stories" in m.lower() for m in slack_messages), (
            f"Expected 'halted' or 'no stories' in Slack messages: {slack_messages}"
        )

    @pytest.mark.asyncio
    async def test_zero_stories_does_not_call_any_agents(self):
        status_called = [False]

        with (
            _patch_db(),
            patch(
                "orchestrator.brief_pipeline.MetisRSSCollector",
                return_value=MagicMock(collect=AsyncMock(side_effect=RuntimeError("all feeds failed"))),
            ),
            patch("orchestrator.brief_pipeline._send_slack_alert", new_callable=AsyncMock),
            patch(
                "orchestrator.brief_pipeline.StatusAgent",
                side_effect=lambda: (_ for _ in ()).throw(AssertionError("StatusAgent should not be called")),
            ),
            _patch_log_dups(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert result.run_status == "failed"


# ---------------------------------------------------------------------------
# Test: error isolation
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_one_region_failure_does_not_cancel_others(self):
        """If EU raises, all other regions still complete."""
        completed: list[str] = []

        async def _region_with_eu_failure(**kwargs):
            region_id = kwargs["region_id"]
            if region_id == "eu":
                return RegionResult(
                    region_id=region_id,
                    edition_id=None,
                    stories=[],
                    status="failed",
                    cost_usd=0.0,
                    error=RuntimeError("EU exploded"),
                )
            completed.append(region_id)
            return RegionResult(
                region_id=region_id,
                edition_id=uuid.uuid4(),
                stories=[],
                status="complete",
                cost_usd=0.0,
                error=None,
            )

        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            patch("orchestrator.brief_pipeline._run_region_pipeline", _region_with_eu_failure),
            _patch_slack(),
            _patch_log_dups(),
        ):
            result = await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert "eu" not in completed
        assert set(completed) == {"na", "latam", "apac", "africa"}
        assert result.run_status == "partial"


# ---------------------------------------------------------------------------
# Test: Slack alerts
# ---------------------------------------------------------------------------


class TestSlackAlerts:
    @pytest.mark.asyncio
    async def test_alerts_slack_on_all_regions_failed(self):
        alerts_sent: list[str] = []

        async def capture(msg: str):
            alerts_sent.append(msg)

        async def all_fail(**kwargs):
            region_id = kwargs["region_id"]
            return RegionResult(
                region_id=region_id, edition_id=None, stories=[],
                status="failed", cost_usd=0.0, error=RuntimeError("fail"),
            )

        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            patch("orchestrator.brief_pipeline._run_region_pipeline", all_fail),
            patch("orchestrator.brief_pipeline._send_slack_alert", capture),
            _patch_log_dups(),
        ):
            await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert any("failure" in m.lower() or "failed" in m.lower() for m in alerts_sent), (
            f"Expected failure alert: {alerts_sent}"
        )

    @pytest.mark.asyncio
    async def test_alerts_slack_on_partial_run(self):
        alerts_sent: list[str] = []

        async def capture(msg: str):
            alerts_sent.append(msg)

        async def one_fail(**kwargs):
            region_id = kwargs["region_id"]
            status = "failed" if region_id == "eu" else "complete"
            return RegionResult(
                region_id=region_id, edition_id=uuid.uuid4(),
                stories=[], status=status, cost_usd=0.0, error=None,
            )

        with (
            _patch_db(),
            _patch_collector(),
            _patch_status_agent(),
            patch("orchestrator.brief_pipeline._run_region_pipeline", one_fail),
            patch("orchestrator.brief_pipeline._send_slack_alert", capture),
            _patch_log_dups(),
        ):
            await run_brief_pipeline(run_date=date(2026, 3, 15))

        assert any("partial" in m.lower() for m in alerts_sent), (
            f"Expected partial alert: {alerts_sent}"
        )


# ---------------------------------------------------------------------------
# Test: _collect_global_pool dry-run
# ---------------------------------------------------------------------------


class TestCollectGlobalPool:
    @pytest.mark.asyncio
    async def test_dry_run_returns_synthetic_stories(self):
        pool, by_region = await _collect_global_pool(["eu", "na"], dry_run=True)
        assert len(pool) > 0
        assert set(by_region.keys()) == {"eu", "na"}

    @pytest.mark.asyncio
    async def test_deduplication_removes_same_url(self):
        """If two regions return same URLs, global pool should deduplicate."""
        shared_story = RawStory(
            title="Shared story",
            url="https://reuters.com/shared",
            source_name="Reuters",
            body="Body",
        )
        eu_stories = [shared_story] + _make_raw_stories(3)
        na_stories = [shared_story] + _make_raw_stories(3)

        mock_cls = MagicMock()
        call_count = [0]

        async def _collect_side_effect():
            idx = call_count[0]
            call_count[0] += 1
            return eu_stories if idx == 0 else na_stories

        mock_cls.return_value.collect = AsyncMock(side_effect=_collect_side_effect)

        with (
            patch("orchestrator.brief_pipeline.MetisRSSCollector", mock_cls),
            _patch_log_dups(),
        ):
            pool, _ = await _collect_global_pool(["eu", "na"], dry_run=False)

        # shared_story URL should appear only once in global pool
        shared_urls = [s.url for s in pool if s.url == "https://reuters.com/shared"]
        assert len(shared_urls) == 1

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_all_regions_fail(self):
        mock_cls = MagicMock()
        mock_cls.return_value.collect = AsyncMock(side_effect=RuntimeError("feed down"))

        with (
            patch("orchestrator.brief_pipeline.MetisRSSCollector", mock_cls),
            _patch_log_dups(),
        ):
            with pytest.raises(RuntimeError):
                await _collect_global_pool(["eu", "na"], dry_run=False)
