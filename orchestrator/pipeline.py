"""
Straight-line pipeline — Day 5 smoke test implementation.

Flow:
    CLI → load configs → fetch RSS → one Claude call → write to DB → return text

This is intentionally minimal. Sprint 2 (Days 6–8) replaces the single Claude
call with the full ResearchAgent → WriterAgent → EditorAgent chain.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config import load_content_type, load_region, load_settings
from data_sources.base_source import BaseSource
from data_sources.rss_source import ManualSource, RSSSource
from db.models import Brief, ContentPiece, Job
from orchestrator.job_model import ArticleDraft, JobPayload
from orchestrator.smoke_writer import SmokeWriterAgent


async def run_pipeline(
    payload: JobPayload,
    session: AsyncSession,
    source_text: str | None = None,
) -> list[ArticleDraft]:
    """
    Run the smoke-test pipeline for every region in the payload.
    Returns one ArticleDraft per region.
    """
    settings = load_settings()
    ct_config = load_content_type(payload.content_type)

    # Persist job row with full config snapshot
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

    # Data source — RSS or manual fallback
    source: BaseSource = (
        ManualSource(source_text) if source_text else RSSSource()
    )
    print(f"Fetching sources for: '{payload.topic}'...")
    articles = await source.fetch(payload.topic)
    print(f"  {len(articles)} article(s) fetched.")

    drafts: list[ArticleDraft] = []

    for region_id in payload.regions:
        region_config = load_region(region_id)
        print(f"\nRunning pipeline for region: {region_config.display_name} ({region_id})")

        # Brief row — one per region in MVP
        brief = Brief(job_id=job.id)
        session.add(brief)
        await session.flush()

        # Content piece row — placeholder until agent writes the body
        piece = ContentPiece(
            brief_id=brief.id,
            region=region_id,
            content_type="regional_article",
            status="draft",
        )
        session.add(piece)
        await session.flush()

        # Single Claude call — replaced by full agent chain in Sprint 2
        agent = SmokeWriterAgent()
        user_message = _build_user_message(payload.topic, articles, region_config, ct_config)

        print(f"  Calling Claude ({agent.MODEL})...")
        raw_text = await agent.run(
            user_message,
            session=session,
            content_piece_id=piece.id,
            iteration=1,
            job_cost_so_far=0.0,
        )

        # Parse headline from first non-empty line
        lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
        headline = lines[0].lstrip("#").strip() if lines else payload.topic
        word_count = len(raw_text.split())

        # Update content piece with written content
        piece.headline = headline
        piece.body = raw_text
        piece.word_count = word_count
        piece.status = "draft"
        piece.updated_at = datetime.utcnow()

        draft = ArticleDraft(
            headline=headline,
            body=raw_text,
            word_count=word_count,
            region_id=region_id,
            iteration=1,
        )
        drafts.append(draft)
        print(f"  Done. {word_count} words — '{headline}'")

    # Mark job complete
    job.status = "complete"
    job.completed_at = datetime.utcnow()
    await session.commit()

    return drafts


# ---------------------------------------------------------------------------
# User message builder
# ---------------------------------------------------------------------------

def _build_user_message(topic, articles, region_config, ct_config) -> str:
    sources_block = "\n\n".join(
        f"--- SOURCE: {a.source_name} ---\nTitle: {a.title}\n\n{a.body[:2000]}"
        for a in articles[:5]
    )
    return f"""TOPIC: {topic}

EDITORIAL VOICE:
{region_config.editorial_voice}

FORMAT INSTRUCTIONS:
{ct_config.writer_instructions}
Minimum words: {ct_config.output.min_words}
Maximum words: {ct_config.output.max_words}

SOURCE ARTICLES:
{sources_block}

Write the article now."""
