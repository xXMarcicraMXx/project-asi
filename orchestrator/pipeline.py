"""
Pipeline — orchestrates one full job across all requested regions.

Flow:
    CLI → load configs → fetch RSS (once) → AgentChain per region → return drafts

Each region runs sequentially (Sprint 2). Upgraded to asyncio.gather in Sprint 3.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agents.chain import AgentChain
from config import load_content_type, load_region, load_settings
from data_sources.base_source import BaseSource
from data_sources.rss_source import ManualSource, RSSSource
from db.models import Brief, ContentPiece, Job
from orchestrator.job_model import ArticleDraft, JobPayload


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

    source: BaseSource = ManualSource(source_text) if source_text else RSSSource()
    print(f"Fetching sources for: '{payload.topic}'...")
    articles = await source.fetch(payload.topic)
    print(f"  {len(articles)} article(s) fetched.")

    drafts: list[ArticleDraft] = []
    job_cost_so_far = 0.0

    for region_id in payload.regions:
        region_config = load_region(region_id)
        print(f"\n[{region_config.display_name}] Starting agent chain...")

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
        draft = await chain.run(
            payload.topic,
            articles,
            region_config,
            ct_config,
            session=session,
            content_piece_id=piece.id,
            job_cost_so_far=job_cost_so_far,
        )

        await session.refresh(piece)
        print(f"[{region_config.display_name}] {piece.status.upper()} — {draft.word_count} words — '{draft.headline}'")

        drafts.append(draft)

    job.status = "complete"
    job.completed_at = datetime.utcnow()
    await session.commit()

    return drafts
