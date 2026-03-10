"""
Pipeline — orchestrates one full job across all requested regions.

Flow:
    CLI → load configs → fetch RSS (once) → AgentChain per region (parallel) → return drafts

Day 15: regions run concurrently via asyncio.gather().
Each region gets its own DB session so sessions are never shared across coroutines.
Error isolation: one failing region logs an error and returns None — other regions
still complete and their drafts are returned. Job status is set to "partial" when
at least one region succeeds but at least one fails.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.chain import AgentChain
from approval.slack_bot import post_for_approval
from config import ContentTypeConfig, load_content_type, load_region, load_settings
from data_sources.base_source import BaseSource
from data_sources.rss_source import ManualSource, RSSSource
from db.models import AgentRun, Brief, ContentPiece, Job
from db.session import AsyncSessionLocal
from orchestrator.job_model import Article, ArticleDraft, JobPayload

logger = logging.getLogger(__name__)


async def run_pipeline(
    payload: JobPayload,
    session: AsyncSession,
    source_text: str | None = None,
) -> list[ArticleDraft]:
    """
    Run the full agent chain for every region in the payload — in parallel.
    Returns one ArticleDraft per region that completed successfully.
    """
    settings = load_settings()
    ct_config = load_content_type(payload.content_type)

    config_snapshot = {
        "settings": settings.model_dump(),
        "content_type": ct_config.model_dump(),
    }
    job = Job(
        id=payload.id,
        project="asi",
        topic=payload.topic,
        content_type=payload.content_type,
        regions=payload.regions,
        status="running",
        config_snapshot=config_snapshot,
    )
    session.add(job)
    # Commit now so region sessions can reference job.id via FK
    await session.commit()

    logger.info(
        "job_started",
        extra={
            "job_id": str(payload.id),
            "topic": payload.topic,
            "regions": payload.regions,
            "content_type": payload.content_type,
        },
    )

    source: BaseSource = ManualSource(source_text) if source_text else RSSSource()
    logger.info("fetching_sources", extra={"topic": payload.topic})
    articles = await source.fetch(payload.topic)
    logger.info("sources_fetched", extra={"count": len(articles)})

    # ── Parallel region execution ─────────────────────────────────────────────
    tasks = [
        _run_region_task(
            region_id=region_id,
            job_id=payload.id,
            topic=payload.topic,
            articles=articles,
            ct_config=ct_config,
        )
        for region_id in payload.regions
    ]
    region_results: list[tuple[str, ArticleDraft | None, float, uuid.UUID | None, BaseException | None]]
    region_results = await asyncio.gather(*tasks)

    # ── Collect results ───────────────────────────────────────────────────────
    drafts: list[ArticleDraft] = []
    approval_pieces: list[dict] = []
    total_cost = 0.0
    failed_regions: list[str] = []

    for region_id, draft, chain_cost, piece_id, error in region_results:
        if error is not None:
            failed_regions.append(region_id)
            logger.error(
                "region_failed",
                extra={"region": region_id, "error": str(error), "job_id": str(payload.id)},
            )
        else:
            assert draft is not None
            drafts.append(draft)
            total_cost += chain_cost
            approval_pieces.append({
                "content_piece_id": piece_id,
                "region_id": region_id,
                "headline": draft.headline,
                "body": draft.body,
            })
            logger.info(
                "region_complete",
                extra={
                    "region": region_id,
                    "words": draft.word_count,
                    "headline": draft.headline,
                    "chain_cost_usd": round(chain_cost, 6),
                },
            )

    # ── Update job status ─────────────────────────────────────────────────────
    if not drafts:
        job.status = "failed"
    elif failed_regions:
        job.status = "partial"
    else:
        job.status = "complete"
    job.completed_at = datetime.utcnow()
    await session.commit()

    # ── Slack approval gate ───────────────────────────────────────────────────
    if approval_pieces:
        await post_for_approval(approval_pieces, payload.id)

    logger.info(
        "job_complete",
        extra={
            "job_id": str(payload.id),
            "status": job.status,
            "regions_ok": len(drafts),
            "regions_failed": len(failed_regions),
            "total_cost_usd": round(total_cost, 6),
        },
    )

    return drafts


# ---------------------------------------------------------------------------
# Per-region task — own session, own error boundary
# ---------------------------------------------------------------------------

async def _run_region_task(
    region_id: str,
    job_id: uuid.UUID,
    topic: str,
    articles: list[Article],
    ct_config: ContentTypeConfig,
) -> tuple[str, ArticleDraft | None, float, uuid.UUID | None, BaseException | None]:
    """
    Run one region end-to-end in its own DB session.

    Returns (region_id, draft, chain_cost, piece_id, error).
    On success error is None; on failure draft and piece_id are None.
    The caller (run_pipeline) decides what to do with failed regions.
    """
    async with AsyncSessionLocal() as session:
        try:
            region_config = load_region(region_id)
            logger.info(
                "region_started",
                extra={"region": region_id, "display_name": region_config.display_name},
            )

            brief_row = Brief(job_id=job_id)
            session.add(brief_row)
            await session.flush()

            piece = ContentPiece(
                brief_id=brief_row.id,
                region=region_id,
                content_type="regional_article",
                status="draft",
            )
            session.add(piece)
            await session.flush()

            chain = AgentChain()
            draft, chain_cost = await chain.run(
                topic,
                articles,
                region_config,
                ct_config,
                session=session,
                content_piece_id=piece.id,
                job_cost_so_far=0.0,  # parallel start — no sequential cost to carry
            )

            await session.commit()
            return region_id, draft, chain_cost, piece.id, None

        except Exception as exc:
            logger.exception(
                "region_task_error",
                extra={"region": region_id, "job_id": str(job_id)},
            )
            return region_id, None, 0.0, None, exc


# ---------------------------------------------------------------------------
# Cost report
# ---------------------------------------------------------------------------

async def query_cost_report(
    session: AsyncSession,
    job_id: object,
) -> list[dict]:
    """
    Return per-agent token and cost totals for a completed job.
    Queries agent_runs joined through content_pieces → briefs → jobs.
    """
    stmt = (
        select(
            AgentRun.agent_name,
            AgentRun.iteration,
            AgentRun.input_tokens,
            AgentRun.output_tokens,
            AgentRun.cost_usd,
            AgentRun.duration_ms,
            ContentPiece.region,
        )
        .join(ContentPiece, AgentRun.content_piece_id == ContentPiece.id)
        .join(Brief, ContentPiece.brief_id == Brief.id)
        .join(Job, Brief.job_id == Job.id)
        .where(Brief.job_id == job_id, Job.project == "asi")
        .order_by(ContentPiece.region, AgentRun.agent_name, AgentRun.iteration)
    )
    result = await session.execute(stmt)
    return [row._asdict() for row in result.all()]
