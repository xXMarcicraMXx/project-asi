"""
brief_pipeline.py — Metis v2 daily brief orchestrator.

DO NOT modify pipeline.py — Oracle dependency.

Pipeline stages (per run):
    1. News collection — MetisRSSCollector per region in parallel → merged global pool
    2. StatusAgent — once on the full story pool
    3. Per region in parallel (asyncio.gather, each with own AsyncSessionLocal):
           a. CurationAgent — selects 5-8 stories for the region
           b. NewsletterWriterAgent — writes 100-150 word summary per story
    4. (Future P2-D6+) LayoutAgent → HtmlPublisher → Cancel Gate → rsync

Cost ceiling:
    per_region_ceiling = settings.cost.max_usd_per_job / len(regions)
    Each region starts its agent calls against this sub-ceiling.
    Fix for the v1 parallel-mode bug where every region started at the
    full job ceiling and could collectively 5× overrun the budget.

Error isolation:
    Per-region failure marks that region 'failed'. Other regions continue.
    Run status: 'complete' (all OK) | 'partial' (≥1 OK, ≥1 failed)
              | 'failed' (all failed)

Dry-run mode:
    All pipeline logic runs. No Anthropic API calls. No DB writes. No rsync.
    Used for validation, cost estimation, and CI smoke tests.

Slack alerts:
    Sent on partial/failed run status and on specific error conditions.
    Silently skipped if SLACK_BOT_TOKEN or ASI_SLACK_CHANNEL_ID not set.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agents.curation_agent import CurationAgent
from agents.newsletter_writer_agent import NewsletterWriterAgent
from agents.status_agent import StatusAgent
from config import load_region, load_settings
from data_sources.rss_source import MetisRSSCollector, log_duplicate_urls
from db.models_v2 import Asi2DailyRun, Asi2Edition, Asi2RawStory, Asi2StoryEntry
from db.session import AsyncSessionLocal
from orchestrator.brief_job_model import DailyStatus, RawStory, StoryEntry

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

METIS_REGIONS: list[str] = ["eu", "na", "latam", "apac", "africa"]

# ── Result types ──────────────────────────────────────────────────────────────


@dataclass
class RegionResult:
    """Outcome of one regional pipeline run."""

    region_id: str
    edition_id: Optional[uuid.UUID]
    stories: list[StoryEntry]
    status: str  # 'complete' | 'failed' | 'no_content'
    cost_usd: float
    error: Optional[BaseException]


@dataclass
class BriefRunResult:
    """Outcome of a full daily pipeline run across all regions."""

    run_id: uuid.UUID
    run_date: date
    daily_status: DailyStatus
    regions: dict[str, RegionResult] = field(default_factory=dict)
    run_status: str = "running"  # 'complete' | 'partial' | 'failed'
    total_cost_usd: float = 0.0


# ── Public entry point ────────────────────────────────────────────────────────


async def run_brief_pipeline(
    regions: Optional[list[str]] = None,
    *,
    dry_run: bool = False,
    run_date: Optional[date] = None,
) -> BriefRunResult:
    """
    Run the full Metis daily brief pipeline.

    Args:
        regions:    Region IDs to process. Defaults to all 5 METIS_REGIONS.
        dry_run:    If True: no Anthropic calls, no DB writes, no rsync.
        run_date:   Override the run date (defaults to today UTC).

    Returns:
        BriefRunResult with per-region outcomes and overall run status.
    """
    if regions is None:
        regions = METIS_REGIONS

    today = run_date or date.today()
    run_id = uuid.uuid4()
    settings = load_settings()
    per_region_ceiling = settings.cost.max_usd_per_job / len(regions)

    logger.info(
        "brief_pipeline_start",
        extra={
            "run_id": str(run_id),
            "run_date": str(today),
            "regions": regions,
            "dry_run": dry_run,
            "per_region_ceiling_usd": round(per_region_ceiling, 4),
        },
    )

    # ── Step 0: Create daily run row ──────────────────────────────────────────
    if not dry_run:
        async with AsyncSessionLocal() as main_session:
            run_row = Asi2DailyRun(id=run_id, run_date=today, status="running")
            main_session.add(run_row)
            await main_session.commit()

    # ── Step 1: News collection (once, global pool) ───────────────────────────
    try:
        raw_stories, stories_by_region = await _collect_global_pool(regions, dry_run=dry_run)
    except RuntimeError as exc:
        # All feeds failed — halt immediately
        msg = f"Metis: no stories collected for {today}. Pipeline halted."
        logger.error("brief_pipeline_no_stories", extra={"run_id": str(run_id), "error": str(exc)})
        await _send_slack_alert(msg)
        if not dry_run:
            await _mark_run(run_id, "failed", total_cost=0.0)
        return BriefRunResult(
            run_id=run_id,
            run_date=today,
            daily_status=_default_status(),
            run_status="failed",
            total_cost_usd=0.0,
        )

    if not raw_stories:
        msg = f"Metis: story pool empty for {today}. Run failed."
        logger.error("brief_pipeline_empty_pool", extra={"run_id": str(run_id)})
        await _send_slack_alert(msg)
        if not dry_run:
            await _mark_run(run_id, "failed", total_cost=0.0)
        return BriefRunResult(
            run_id=run_id,
            run_date=today,
            daily_status=_default_status(),
            run_status="failed",
            total_cost_usd=0.0,
        )

    logger.info(
        "collection_complete",
        extra={"run_id": str(run_id), "story_count": len(raw_stories), "regions": regions},
    )

    # ── Step 2: Save raw stories to DB ───────────────────────────────────────
    if not dry_run:
        await _save_raw_stories(run_id, raw_stories)

    # ── Step 3: StatusAgent — once on full pool ───────────────────────────────
    status_cost = 0.0
    if dry_run:
        daily_status = _default_status()
    else:
        daily_status, status_cost = await _run_status_agent(run_id, raw_stories)

    logger.info(
        "status_agent_complete",
        extra={
            "run_id": str(run_id),
            "color": daily_status.daily_color,
            "sentiment": daily_status.sentiment,
            "mood_headline": daily_status.mood_headline[:80],
        },
    )

    # Update daily run with status agent result
    if not dry_run:
        await _update_run_status_fields(run_id, daily_status)

    # ── Step 4: Regional pipelines in parallel ────────────────────────────────
    region_tasks = [
        _run_region_pipeline(
            region_id=region_id,
            run_id=run_id,
            run_date=today,
            raw_stories=raw_stories,
            daily_status=daily_status,
            per_region_ceiling=per_region_ceiling,
            dry_run=dry_run,
        )
        for region_id in regions
    ]
    region_results_list: list[RegionResult] = await asyncio.gather(*region_tasks)

    # ── Step 5: Aggregate results ─────────────────────────────────────────────
    region_map: dict[str, RegionResult] = {r.region_id: r for r in region_results_list}
    total_cost = status_cost + sum(r.cost_usd for r in region_results_list)

    ok_regions = [r for r in region_results_list if r.status in ("complete", "no_content")]
    failed_regions = [r for r in region_results_list if r.status == "failed"]

    if not ok_regions:
        run_status = "failed"
    elif failed_regions:
        run_status = "partial"
    else:
        run_status = "complete"

    logger.info(
        "brief_pipeline_complete",
        extra={
            "run_id": str(run_id),
            "run_status": run_status,
            "regions_ok": len(ok_regions),
            "regions_failed": len(failed_regions),
            "total_cost_usd": round(total_cost, 6),
        },
    )

    # ── Step 6: Slack alerts on failure ──────────────────────────────────────
    n_published = len(ok_regions)
    n_total = len(regions)
    if run_status == "failed":
        await _send_slack_alert(f"Metis: complete pipeline failure on {today}.")
    elif run_status == "partial":
        await _send_slack_alert(
            f"Metis: partial run — {n_published}/{n_total} regions published on {today}."
        )

    # Daily cost summary alert
    if run_status in ("complete", "partial"):
        await _send_slack_alert(
            f"Metis: {today} {'complete' if run_status == 'complete' else 'partial'} "
            f"— {n_published}/{n_total} regions, ${total_cost:.2f}"
        )

    # ── Step 7: Final DB update ───────────────────────────────────────────────
    if not dry_run:
        await _mark_run(run_id, run_status, total_cost=total_cost)

    return BriefRunResult(
        run_id=run_id,
        run_date=today,
        daily_status=daily_status,
        regions=region_map,
        run_status=run_status,
        total_cost_usd=total_cost,
    )


# ── News collection ───────────────────────────────────────────────────────────


async def _collect_global_pool(
    regions: list[str],
    *,
    dry_run: bool,
) -> tuple[list[RawStory], dict[str, list[RawStory]]]:
    """
    Collect stories for all regions in parallel, merge and deduplicate by URL.

    Returns:
        (global_pool, stories_by_region) where global_pool is deduplicated
        across all regions' feeds (global + regional).

    Raises:
        RuntimeError: if all region collectors fail (zero stories total).
    """
    if dry_run:
        # In dry-run mode, return synthetic stories so the pipeline logic
        # exercises all branches without network I/O.
        stub = [
            RawStory(
                title=f"[DRY RUN] Story {i + 1}",
                url=f"https://example.com/story-{i + 1}",
                source_name="Dry Run",
                body=f"Dry-run placeholder story {i + 1}.",
            )
            for i in range(10)
        ]
        return stub, {r: stub for r in regions}

    # Collect each region concurrently
    tasks = [MetisRSSCollector(region_id).collect() for region_id in regions]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    stories_by_region: dict[str, list[RawStory]] = {}
    any_ok = False

    for region_id, result in zip(regions, results_raw):
        if isinstance(result, Exception):
            logger.warning(
                "region_collection_failed",
                extra={"region": region_id, "error": str(result)},
            )
            stories_by_region[region_id] = []
        else:
            stories_by_region[region_id] = result
            any_ok = True

    if not any_ok:
        raise RuntimeError(
            f"All {len(regions)} region collectors failed. "
            "Zero stories available. Pipeline halted."
        )

    # Log URL duplication across regions (Phase 4 dedup metrics data)
    log_duplicate_urls(stories_by_region)

    # Merge and deduplicate by URL (preserve insertion order — EU first)
    seen_urls: set[str] = set()
    global_pool: list[RawStory] = []

    for region_id in regions:
        for story in stories_by_region.get(region_id, []):
            if story.url and story.url in seen_urls:
                continue
            if story.url:
                seen_urls.add(story.url)
            global_pool.append(story)

    return global_pool, stories_by_region


# ── StatusAgent ───────────────────────────────────────────────────────────────


async def _run_status_agent(
    run_id: uuid.UUID,
    stories: list[RawStory],
) -> tuple[DailyStatus, float]:
    """Run StatusAgent once on the full pool. Returns (DailyStatus, cost_usd)."""
    async with AsyncSessionLocal() as session:
        agent = StatusAgent()
        status = await agent.run_brief(
            stories,
            session=session,
            run_id=run_id,
            job_cost_so_far=0.0,
        )
        cost = getattr(agent, "last_call_cost", 0.0)
        return status, cost


# ── Per-region pipeline ───────────────────────────────────────────────────────


async def _run_region_pipeline(
    region_id: str,
    run_id: uuid.UUID,
    run_date: date,
    raw_stories: list[RawStory],
    daily_status: DailyStatus,
    per_region_ceiling: float,
    *,
    dry_run: bool,
) -> RegionResult:
    """
    Run the full pipeline for one region in its own AsyncSessionLocal.

    Error isolation: any exception is caught, returned as a failed RegionResult.
    The caller (run_brief_pipeline) checks which regions failed but does NOT
    propagate the exception — other regions continue regardless.
    """
    edition_id: Optional[uuid.UUID] = None

    try:
        region_config = load_region(region_id)
        edition_id = uuid.uuid4()

        logger.info(
            "region_pipeline_start",
            extra={"region": region_id, "run_id": str(run_id), "edition_id": str(edition_id)},
        )

        # Create edition row in DB
        if not dry_run:
            async with AsyncSessionLocal() as session:
                edition = Asi2Edition(
                    id=edition_id,
                    run_id=run_id,
                    region=region_id,
                    status="curating",
                )
                session.add(edition)
                await session.commit()

        # ── CurationAgent ─────────────────────────────────────────────────────
        if dry_run:
            # Dry-run: produce synthetic curated stories without calling the API
            from orchestrator.brief_job_model import CuratedStory

            curated = [
                CuratedStory(
                    raw_story_id=story.id,
                    title=story.title,
                    url=story.url,
                    source_name=story.source_name,
                    category="Politics",
                    significance_score=round(0.9 - i * 0.05, 2),
                    body=story.body,
                )
                for i, story in enumerate(raw_stories[:6])
            ]
            curation_cost = 0.0
        else:
            async with AsyncSessionLocal() as session:
                curation_agent = CurationAgent()
                try:
                    curated = await curation_agent.run_region(
                        raw_stories,
                        region_id=region_id,
                        curation_bias=region_config.curation_bias,
                        session=session,
                        edition_id=edition_id,
                        job_cost_so_far=0.0,
                    )
                    curation_cost = getattr(curation_agent, "last_call_cost", 0.0)
                except RuntimeError as exc:
                    # 0 stories selected — mark no_content and return early
                    logger.warning(
                        "region_no_content",
                        extra={"region": region_id, "error": str(exc)},
                    )
                    await _update_edition_status(edition_id, "no_content")
                    await _send_slack_alert(
                        f"Metis: region {region_id.upper()} — no stories selected for {run_date}."
                    )
                    return RegionResult(
                        region_id=region_id,
                        edition_id=edition_id,
                        stories=[],
                        status="no_content",
                        cost_usd=0.0,
                        error=None,
                    )

            if not dry_run:
                await _update_edition_status(edition_id, "writing")

        # ── NewsletterWriterAgent ─────────────────────────────────────────────
        if dry_run:
            # Dry-run: produce synthetic StoryEntry objects without API calls
            entries = [
                StoryEntry(
                    rank=rank,
                    category=story.category,
                    title=story.title,
                    url=story.url,
                    source_name=story.source_name,
                    summary=(
                        f"[DRY RUN] Summary for {story.title[:40]}. "
                        "This is a placeholder summary generated in dry-run mode "
                        "to validate the pipeline structure without API calls. "
                        "No content agents were invoked."
                    ),
                    word_count=30,
                    significance_score=story.significance_score,
                    raw_story_id=story.raw_story_id,
                )
                for rank, story in enumerate(curated, 1)
            ]
            region_cost = 0.0
        else:
            writer_agent = NewsletterWriterAgent()
            entries: list[StoryEntry] = []
            region_cost = curation_cost

            async with AsyncSessionLocal() as session:
                for rank, story in enumerate(curated, 1):
                    entry = await writer_agent.run_story(
                        story,
                        rank=rank,
                        region_id=region_id,
                        daily_status=daily_status,
                        session=session,
                        edition_id=edition_id,
                        job_cost_so_far=region_cost,
                    )
                    entries.append(entry)
                    region_cost += getattr(writer_agent, "last_call_cost", 0.0)

                    # Cost ceiling guard — stop writing if over budget
                    if region_cost >= per_region_ceiling:
                        logger.warning(
                            "region_cost_ceiling_hit",
                            extra={
                                "region": region_id,
                                "cost_so_far": round(region_cost, 6),
                                "ceiling": round(per_region_ceiling, 6),
                                "stories_written": rank,
                                "stories_total": len(curated),
                            },
                        )
                        break

        # ── Save story entries to DB ──────────────────────────────────────────
        if not dry_run:
            await _save_story_entries(edition_id, entries)
            await _update_edition_status(edition_id, "layout_done")

        # ── HtmlPublisher — write static HTML locally ─────────────────────────
        if not dry_run:
            from orchestrator.brief_job_model import RegionalEdition
            from publishers.html_publisher import DiskFullError, HtmlPublisher, PublishError

            regional_edition = RegionalEdition(
                region=region_id,
                daily_status=daily_status,
                stories=entries,
                layout=layout_config,
            )
            try:
                publisher = HtmlPublisher()
                current_path, _ = publisher.publish(regional_edition, run_date)
                logger.info(
                    "html_published_locally",
                    extra={"region": region_id, "path": str(current_path)},
                )
                # Record html_path on the edition row
                from sqlalchemy import update as sa_update
                async with AsyncSessionLocal() as _s:
                    await _s.execute(
                        sa_update(Asi2Edition)
                        .where(Asi2Edition.id == edition_id)
                        .values(html_path=str(current_path))
                    )
                    await _s.commit()
            except DiskFullError as exc:
                await _send_slack_alert(str(exc))
                raise
            except PublishError as exc:
                await _send_slack_alert(str(exc))
                raise

            # ── Open cancel window ────────────────────────────────────────────
            from datetime import timedelta
            slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
            if slack_token:
                window = timedelta(minutes=30)
            else:
                logger.warning(
                    "cancel_window_bypassed",
                    extra={"region": region_id,
                           "reason": "SLACK_BOT_TOKEN not set — publishing immediately"},
                )
                window = timedelta(seconds=0)
            await _set_pending_publish(edition_id, datetime.utcnow() + window)

        logger.info(
            "region_pipeline_complete",
            extra={
                "region": region_id,
                "stories": len(entries),
                "cost_usd": round(region_cost if not dry_run else 0.0, 6),
            },
        )

        return RegionResult(
            region_id=region_id,
            edition_id=edition_id,
            stories=entries,
            status="complete",
            cost_usd=region_cost if not dry_run else 0.0,
            error=None,
        )

    except Exception as exc:
        logger.exception(
            "region_pipeline_error",
            extra={"region": region_id, "run_id": str(run_id), "error": str(exc)},
        )
        # Best-effort: mark edition failed in DB
        if edition_id and not dry_run:
            try:
                await _update_edition_status(edition_id, "failed")
            except Exception:
                pass  # DB update failure must not mask the original error

        return RegionResult(
            region_id=region_id,
            edition_id=edition_id,
            stories=[],
            status="failed",
            cost_usd=0.0,
            error=exc,
        )


# ── DB helpers ────────────────────────────────────────────────────────────────


async def _save_raw_stories(run_id: uuid.UUID, stories: list[RawStory]) -> None:
    """Bulk-insert RawStory objects into asi2_raw_stories."""
    async with AsyncSessionLocal() as session:
        for story in stories:
            row = Asi2RawStory(
                id=story.id,
                run_id=run_id,
                title=story.title,
                url=story.url,
                source_name=story.source_name,
                category_hint=story.category_hint,
                body_preview=(story.body or "")[:500],
                published_at=story.published_at,
            )
            session.add(row)
        await session.commit()


async def _save_story_entries(
    edition_id: uuid.UUID,
    entries: list[StoryEntry],
) -> None:
    """Insert StoryEntry rows for one edition."""
    async with AsyncSessionLocal() as session:
        for entry in entries:
            row = Asi2StoryEntry(
                edition_id=edition_id,
                rank=entry.rank,
                category=entry.category,
                title=entry.title,
                url=entry.url,
                source_name=entry.source_name,
                summary=entry.summary,
                word_count=entry.word_count,
                significance_score=entry.significance_score,
                raw_story_id=entry.raw_story_id,
            )
            session.add(row)
        await session.commit()


async def _update_edition_status(edition_id: uuid.UUID, status: str) -> None:
    """Update a single edition's status column."""
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as session:
        stmt = (
            sa_update(Asi2Edition)
            .where(Asi2Edition.id == edition_id)
            .values(status=status)
        )
        await session.execute(stmt)
        await session.commit()


async def _set_pending_publish(edition_id: uuid.UUID, publish_at: datetime) -> None:
    """Set edition status to pending_publish with the given publish_at timestamp."""
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as session:
        stmt = (
            sa_update(Asi2Edition)
            .where(Asi2Edition.id == edition_id)
            .values(status="pending_publish", publish_at=publish_at)
        )
        await session.execute(stmt)
        await session.commit()


async def _update_run_status_fields(run_id: uuid.UUID, daily_status: DailyStatus) -> None:
    """Write StatusAgent outputs back to the Asi2DailyRun row."""
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as session:
        stmt = (
            sa_update(Asi2DailyRun)
            .where(Asi2DailyRun.id == run_id)
            .values(
                daily_color=daily_status.daily_color,
                sentiment=daily_status.sentiment,
                mood_headline=daily_status.mood_headline,
            )
        )
        await session.execute(stmt)
        await session.commit()


async def _mark_run(
    run_id: uuid.UUID,
    status: str,
    *,
    total_cost: float,
) -> None:
    """Set final status + completed_at + total_cost on the daily run row."""
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as session:
        stmt = (
            sa_update(Asi2DailyRun)
            .where(Asi2DailyRun.id == run_id)
            .values(
                status=status,
                completed_at=datetime.utcnow(),
                total_cost_usd=round(total_cost, 6),
            )
        )
        await session.execute(stmt)
        await session.commit()


# ── Slack alerts ──────────────────────────────────────────────────────────────


async def _send_slack_alert(message: str) -> None:
    """
    Post a plain text alert to the Metis Slack channel.

    Silently skips if SLACK_BOT_TOKEN or ASI_SLACK_CHANNEL_ID are not set.
    Never raises — Slack failure must not mask pipeline errors.
    """
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("ASI_SLACK_CHANNEL_ID", "")
    if not token or not channel:
        logger.warning(
            "metis_slack_alert_skipped",
            extra={"reason": "SLACK_BOT_TOKEN or ASI_SLACK_CHANNEL_ID not set", "message": message[:100]},
        )
        return
    try:
        from slack_sdk.web.async_client import AsyncWebClient

        client = AsyncWebClient(token=token)
        await client.chat_postMessage(channel=channel, text=message)
        logger.info("metis_slack_alert_sent", extra={"message": message[:100]})
    except Exception as exc:
        logger.error("metis_slack_alert_failed", extra={"error": str(exc), "message": message[:100]})


# ── Helpers ───────────────────────────────────────────────────────────────────


def _default_status() -> DailyStatus:
    """Return Amber/Cautious as the safe default DailyStatus."""
    return DailyStatus(
        daily_color="Amber",
        sentiment="Cautious",
        mood_headline="A mixed global news day with no single dominant story.",
    )
