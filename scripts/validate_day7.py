"""
Day 7 validation script.

Runs ResearchAgent → WriterAgent on a live topic and confirms:
1. Draft is >= 600 words
2. Body contains markdown headers (##)
3. Both agent_runs rows are written to DB

Usage:
    python scripts/validate_day7.py
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from agents.research_agent import ResearchAgent
from agents.writer_agent import WriterAgent
from config import load_content_type, load_region
from data_sources.rss_source import RSSSource
from db.models import AgentRun, Brief, ContentPiece, Job
from db.session import AsyncSessionLocal


async def main() -> None:
    topic = "interest rates"
    region_id = "EU"

    print(f"Topic: '{topic}' | Region: {region_id}\n")

    ct_config = load_content_type("journal_article")
    region_config = load_region(region_id)

    print("Fetching RSS articles...")
    articles = await RSSSource().fetch(topic)
    print(f"  {len(articles)} article(s) fetched.\n")

    async with AsyncSessionLocal() as session:
        job = Job(topic=topic, content_type="journal_article", regions=[region_id])
        session.add(job)
        await session.flush()

        brief_row = Brief(job_id=job.id)
        session.add(brief_row)
        await session.flush()

        piece = ContentPiece(brief_id=brief_row.id, region=region_id, content_type="regional_article")
        session.add(piece)
        await session.flush()

        # Step 1 — Research
        print("Running ResearchAgent (Haiku)...")
        research_brief = await ResearchAgent().run_research(
            topic, articles,
            session=session,
            content_piece_id=piece.id,
            iteration=1,
        )
        print(f"  {len(research_brief.key_facts)} key facts extracted.\n")

        # Step 2 — Write
        print("Running WriterAgent (Sonnet)...")
        draft = await WriterAgent().run_write(
            research_brief, region_config, ct_config,
            session=session,
            content_piece_id=piece.id,
            iteration=1,
        )

        # Verify both agent_runs rows
        result = await session.execute(
            select(AgentRun).where(AgentRun.content_piece_id == piece.id)
        )
        runs = result.scalars().all()

    print(f"\nDraft: '{draft.headline}'")
    print(f"  word_count : {draft.word_count}")
    print(f"  region     : {draft.region_id}")
    print(f"  iteration  : {draft.iteration}")
    print(f"\nFirst 300 chars:\n{draft.body[:300]}...")

    print(f"\nagent_runs rows: {len(runs)}")
    for r in runs:
        print(f"  {r.agent_name:20s} tokens={r.input_tokens}+{r.output_tokens}  cost=${float(r.cost_usd):.6f}")

    assert draft.word_count >= 600, f"Draft too short: {draft.word_count} words"
    assert re.search(r"^##\s+", draft.body, re.MULTILINE), "Draft missing markdown ## headers"
    assert len(runs) == 2, f"Expected 2 agent_runs rows, got {len(runs)}"

    print("\nDay 7 validation PASSED")


if __name__ == "__main__":
    asyncio.run(main())
