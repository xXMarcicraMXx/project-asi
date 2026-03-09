"""
Pipeline — orchestrates one full job across all requested regions.

Flow:
    CLI → load configs → fetch RSS (once) → AgentChain per region → return drafts

Each region runs sequentially (Sprint 2). Upgraded to asyncio.gather in Sprint 3.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.chain import AgentChain
from config import load_content_type, load_region, load_settings
from data_sources.base_source import BaseSource
from data_sources.rss_source import ManualSource, RSSSource
from db.models import AgentRun, Brief, ContentPiece, Job
from orchestrator.job_model import ArticleDraft, JobPayload

logger = logging.getLogger(__name__)


async def run_pipeline(
    payload: JobPayload,
    session: AsyncSession,
    source_text: str | None = None,
) -> list[ArticleDraft]:
    """
    Run the full agent chain for every region in the payload.
    Returns one ArticleDraft per region.
    """
    settings = load_settings()
    ct_config = load_content_type(payload.content_type)

    config_snapshot = {
        "settings": settings.model_dump(),
        "content_type": ct_config.model_dump(),
    }
    job = Job(
        id=payload.id,
        topic=payload.topic,
        content_type=payload.content_type,
        regions=payload.regions,
        status="running",
        config_snapshot=config_snapshot,
    )
    session.add(job)
    await session.flush()

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

    drafts: list[ArticleDraft] = []
    job_cost_so_far = 0.0

    for region_id in payload.regions:
        region_config = load_region(region_id)
        logger.info(
            "region_started",
            extra={"region": region_id, "display_name": region_config.display_name},
        )

        brief_row = Brief(job_id=job.id)
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
            payload.topic,
            articles,
            region_config,
            ct_config,
            session=session,
            content_piece_id=piece.id,
            job_cost_so_far=job_cost_so_far,
        )
        job_cost_so_far += chain_cost

        await session.refresh(piece)
        logger.info(
            "region_complete",
            extra={
                "region": region_id,
                "status": piece.status,
                "words": draft.word_count,
                "headline": draft.headline,
                "chain_cost_usd": round(chain_cost, 6),
                "job_cost_so_far_usd": round(job_cost_so_far, 6),
            },
        )

        drafts.append(draft)

    job.status = "complete"
    job.completed_at = datetime.utcnow()
    await session.commit()

    logger.info(
        "job_complete",
        extra={
            "job_id": str(payload.id),
            "regions": len(drafts),
            "total_cost_usd": round(job_cost_so_far, 6),
        },
    )

    return drafts


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
        .where(Brief.job_id == job_id)
        .order_by(ContentPiece.region, AgentRun.agent_name, AgentRun.iteration)
    )
    result = await session.execute(stmt)
    return [row._asdict() for row in result.all()]
