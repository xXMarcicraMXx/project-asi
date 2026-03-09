"""
Day 6 validation script.

Runs ResearchAgent on live RSS articles and confirms:
1. Returns a valid ResearchBrief
2. Contains >= 5 distinct key_facts
3. Agent run is logged to agent_runs table

Usage:
    python scripts/validate_day6.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from agents.research_agent import ResearchAgent
from data_sources.rss_source import RSSSource
from db.models import AgentRun, Brief, ContentPiece, Job
from db.session import AsyncSessionLocal


async def main() -> None:
    topic = "interest rates"
    print(f"Fetching articles for: '{topic}'...")

    source = RSSSource()
    articles = await source.fetch(topic)
    print(f"  {len(articles)} article(s) fetched.\n")

    async with AsyncSessionLocal() as session:
        # Minimal FK chain for agent_runs write
        job = Job(topic=topic, content_type="journal_article", regions=["EU"])
        session.add(job)
        await session.flush()

        brief = Brief(job_id=job.id)
        session.add(brief)
        await session.flush()

        piece = ContentPiece(brief_id=brief.id, region="EU", content_type="regional_article")
        session.add(piece)
        await session.flush()

        print("Running ResearchAgent...")
        agent = ResearchAgent()
        research_brief = await agent.run_research(
            topic,
            articles,
            session=session,
            content_piece_id=piece.id,
        )

        # Verify agent_runs row
        result = await session.execute(
            select(AgentRun).where(AgentRun.content_piece_id == piece.id)
        )
        run_row = result.scalar_one_or_none()
        assert run_row is not None, "No agent_runs row found"

    print("\nResearchBrief:")
    print(f"  topic                    : {research_brief.topic}")
    print(f"  key_facts                : {len(research_brief.key_facts)}")
    print(f"  direct_quotes            : {len(research_brief.direct_quotes)}")
    print(f"  data_points              : {len(research_brief.data_points)}")
    print(f"  conflicting_perspectives : {len(research_brief.conflicting_perspectives)}")

    print("\nKey facts:")
    for i, fact in enumerate(research_brief.key_facts, 1):
        print(f"  {i}. {fact}")

    print(f"\nagent_runs:")
    print(f"  input_tokens  = {run_row.input_tokens}")
    print(f"  output_tokens = {run_row.output_tokens}")
    print(f"  cost_usd      = ${float(run_row.cost_usd):.6f}")

    assert len(research_brief.key_facts) >= 5, "Must have >= 5 key_facts"

    print("\nDay 6 validation PASSED")


if __name__ == "__main__":
    asyncio.run(main())
