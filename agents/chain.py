"""
AgentChain — orchestrates the Research → Write → Edit loop for one region.

Loop logic:
    iteration 1–3:  WriterAgent → EditorAgent
                    approve  → return (draft, chain_cost)
                    revise   → feed feedback back to WriterAgent, increment iteration
    iteration 4:    hard cap reached → set content_piece.status = human_review, stop

Every WriterAgent and EditorAgent call is logged to agent_runs via BaseAgent.
The FeedbackLoop table records every editor verdict for the audit trail.

Returns:
    (ArticleDraft, total_chain_cost_usd)  — so the pipeline can accumulate the
    running job cost and pass a correct ceiling-check value to the next region.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agents.editor_agent import EditorAgent
from agents.research_agent import ResearchAgent
from agents.writer_agent import WriterAgent
from config import ContentTypeConfig, RegionConfig
from db.models import ContentPiece, FeedbackLoop
from orchestrator.job_model import Article, ArticleDraft, EditorDecision

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3


class AgentChain:
    """
    Runs the full Research → Write → Edit pipeline for a single region task.

    Usage:
        chain = AgentChain()
        draft, cost = await chain.run(
            topic, articles, region_config, ct_config,
            session=session,
            content_piece_id=piece.id,
        )
    """

    def __init__(self) -> None:
        self._researcher = ResearchAgent()
        self._writer = WriterAgent()
        self._editor = EditorAgent()

    async def run(
        self,
        topic: str,
        articles: list[Article],
        region_config: RegionConfig,
        ct_config: ContentTypeConfig,
        *,
        session: AsyncSession,
        content_piece_id: uuid.UUID,
        job_cost_so_far: float = 0.0,
    ) -> tuple[ArticleDraft, float]:
        """
        Run the full agent chain and return (final_draft, total_chain_cost_usd).

        total_chain_cost_usd is the sum of all agent calls made in this chain
        invocation (research + all write + all edit iterations).  The pipeline
        adds this to job_cost_so_far before starting the next region.

        Updates the content_piece row with the final headline, body, and status.
        """
        running_cost = job_cost_so_far

        # --- Research (once per region, not repeated on revision) ---
        brief = await self._researcher.run_research(
            topic,
            articles,
            session=session,
            content_piece_id=content_piece_id,
            iteration=1,
            job_cost_so_far=running_cost,
        )
        running_cost += self._researcher.last_call_cost
        logger.debug(
            "research_complete",
            extra={
                "region": region_config.region_id,
                "facts": len(brief.key_facts),
                "cost_usd": self._researcher.last_call_cost,
            },
        )

        editor_feedback: str | None = None
        draft: ArticleDraft | None = None
        chain_cost = running_cost - job_cost_so_far   # cost accumulated this chain

        for iteration in range(1, MAX_ITERATIONS + 1):
            # --- Write ---
            draft = await self._writer.run_write(
                brief,
                region_config,
                ct_config,
                session=session,
                content_piece_id=content_piece_id,
                iteration=iteration,
                job_cost_so_far=running_cost,
                editor_feedback=editor_feedback,
            )
            running_cost += self._writer.last_call_cost
            chain_cost += self._writer.last_call_cost

            # --- Edit ---
            verdict = await self._editor.run_edit(
                draft,
                ct_config,
                session=session,
                content_piece_id=content_piece_id,
                iteration=iteration,
                job_cost_so_far=running_cost,
            )
            running_cost += self._editor.last_call_cost
            chain_cost += self._editor.last_call_cost

            # Log feedback loop row
            await _log_feedback(
                session,
                content_piece_id=content_piece_id,
                iteration=iteration,
                status=verdict.status.value,
                feedback=verdict.feedback,
            )

            logger.debug(
                "editor_verdict",
                extra={
                    "region": region_config.region_id,
                    "iteration": iteration,
                    "verdict": verdict.status.value,
                    "words": draft.word_count,
                },
            )

            if verdict.status == EditorDecision.approve:
                await _update_piece(
                    session, content_piece_id,
                    headline=draft.headline,
                    body=draft.body,
                    word_count=draft.word_count,
                    iteration_count=iteration,
                    status="approved",
                )
                return draft, chain_cost

            # Revise — carry feedback into next iteration
            editor_feedback = verdict.feedback

        # Hard cap reached — escalate to human review
        assert draft is not None   # loop always runs at least once
        await _update_piece(
            session, content_piece_id,
            headline=draft.headline,
            body=draft.body,
            word_count=draft.word_count,
            iteration_count=MAX_ITERATIONS,
            status="human_review",
        )
        logger.warning(
            "iteration_cap_reached",
            extra={
                "region": region_config.region_id,
                "content_piece_id": str(content_piece_id),
            },
        )
        return draft, chain_cost


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _log_feedback(
    session: AsyncSession,
    *,
    content_piece_id: uuid.UUID,
    iteration: int,
    status: str,
    feedback: str,
) -> None:
    row = FeedbackLoop(
        content_piece_id=content_piece_id,
        iteration=iteration,
        status=status,
        feedback=feedback,
    )
    session.add(row)
    await session.flush()


async def _update_piece(
    session: AsyncSession,
    content_piece_id: uuid.UUID,
    *,
    headline: str,
    body: str,
    word_count: int,
    iteration_count: int,
    status: str,
) -> None:
    result = await session.get(ContentPiece, content_piece_id)
    if result:
        result.headline = headline
        result.body = body
        result.word_count = word_count
        result.iteration_count = iteration_count
        result.status = status
        result.updated_at = datetime.utcnow()
        await session.flush()
