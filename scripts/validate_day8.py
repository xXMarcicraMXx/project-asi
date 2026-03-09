"""
Day 8 validation script.

Runs the full AgentChain (Research → Write → Edit loop) and confirms:
1. Chain completes and returns a draft
2. feedback_loops rows are written for every editor verdict
3. content_piece.status is either 'approved' or 'human_review'
4. agent_runs rows exist for all agent calls

Usage:
    python scripts/validate_day8.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from agents.chain import AgentChain
from config import load_content_type, load_region
from data_sources.rss_source import RSSSource
from db.models import AgentRun, Brief, ContentPiece, FeedbackLoop, Job
from db.session import AsyncSessionLocal


async def main() -> None:
    topic = "interest rates"
    region_id = "EU"

    print(f"Topic: '{topic}' | Region: {region_id}")
    print(f"Running full AgentChain (Research → Write → Edit loop)...\n")

    ct_config = load_content_type("journal_article")
    region_config = load_region(region_id)

    articles = await RSSSource().fetch(topic)
    print(f"  {len(articles)} article(s) fetched.\n")

    async with AsyncSessionLocal() as session:
        job = Job(topic=topic, content_type="journal_article", regions=[region_id])
        session.add(job)
        await session.flush()

        brief_row = Brief(job_id=job.id)
        session.add(brief_row)
        await session.flush()

        piece = ContentPiece(
            brief_id=brief_row.id, region=region_id, content_type="regional_article"
        )
        session.add(piece)
        await session.flush()

        chain = AgentChain()
        draft = await chain.run(
            topic, articles, region_config, ct_config,
            session=session,
            content_piece_id=piece.id,
        )

        await session.refresh(piece)

        # Query audit trail
        runs_result = await session.execute(
            select(AgentRun).where(AgentRun.content_piece_id == piece.id)
        )
        runs = runs_result.scalars().all()

        loops_result = await session.execute(
            select(FeedbackLoop).where(FeedbackLoop.content_piece_id == piece.id)
        )
        loops = loops_result.scalars().all()

    print(f"Final draft: '{draft.headline}'")
    print(f"  word_count       : {draft.word_count}")
    print(f"  content_piece    : status={piece.status}, iterations={piece.iteration_count}")

    print(f"\nEditor verdicts ({len(loops)}):")
    for fl in loops:
        print(f"  iteration {fl.iteration}: {fl.status:8s} — {fl.feedback[:80]}...")

    print(f"\nagent_runs ({len(runs)}):")
    total_cost = 0.0
    for r in runs:
        cost = float(r.cost_usd)
        total_cost += cost
        print(f"  {r.agent_name:20s} iter={r.iteration}  tokens={r.input_tokens}+{r.output_tokens}  ${cost:.6f}")
    print(f"  {'TOTAL':20s}                                    ${total_cost:.6f}")

    assert draft.word_count > 0, "Draft has no content"
    assert piece.status in ("approved", "human_review"), f"Unexpected status: {piece.status}"
    assert len(loops) >= 1, "No feedback_loops rows written"
    assert len(runs) >= 3, "Expected at least 3 agent_runs (research + write + edit)"

    print("\nDay 8 validation PASSED")


if __name__ == "__main__":
    asyncio.run(main())
