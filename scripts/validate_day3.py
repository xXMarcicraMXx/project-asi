"""
Day 3 validation script.

Makes a single Claude Haiku call via a minimal concrete agent and confirms:
1. Response text is returned
2. A row is written to agent_runs with correct token counts and cost_usd

Usage:
    python scripts/validate_day3.py

Requires:
    ANTHROPIC_API_KEY and DATABASE_URL set in .env (or environment)
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select

from agents.base_agent import BaseAgent
from db.models import AgentRun
from db.session import AsyncSessionLocal


# ---------------------------------------------------------------------------
# Minimal concrete agent for validation only
# ---------------------------------------------------------------------------

class _PingAgent(BaseAgent):
    AGENT_NAME = "ping_agent"
    MODEL = "claude-haiku-4-5-20251001"
    SYSTEM_PROMPT = (
        "You are a test agent. "
        "When asked to confirm readiness, reply with exactly: READY"
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

async def main() -> None:
    agent = _PingAgent()

    # Use a fake content_piece_id — this is a validation run, not a real job
    fake_piece_id = uuid.uuid4()

    async with AsyncSessionLocal() as session:
        print("Calling Claude Haiku via BaseAgent...")
        text = await agent.run(
            "Confirm readiness.",
            session=session,
            content_piece_id=fake_piece_id,
            iteration=1,
            job_cost_so_far=0.0,
        )
        print(f"Response: {text!r}")

        # Verify the agent_runs row was written
        result = await session.execute(
            select(AgentRun).where(AgentRun.content_piece_id == fake_piece_id)
        )
        row: AgentRun | None = result.scalar_one_or_none()

        if row is None:
            print("FAIL — no agent_runs row found")
            sys.exit(1)

        print(
            f"\nagent_runs row written:"
            f"\n  agent_name    = {row.agent_name}"
            f"\n  input_tokens  = {row.input_tokens}"
            f"\n  output_tokens = {row.output_tokens}"
            f"\n  cost_usd      = ${float(row.cost_usd):.6f}"
            f"\n  duration_ms   = {row.duration_ms} ms"
        )

        assert row.input_tokens  > 0, "input_tokens must be > 0"
        assert row.output_tokens > 0, "output_tokens must be > 0"
        assert float(row.cost_usd) > 0, "cost_usd must be > 0"

    print("\nDay 3 validation PASSED")


if __name__ == "__main__":
    asyncio.run(main())
