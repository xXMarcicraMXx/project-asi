"""
AgentChain — orchestrates the Research → Write → Edit loop for one region.

Loop logic:
    iteration 1–3:  WriterAgent → EditorAgent
                    approve  → return draft
                    revise   → feed feedback back to WriterAgent, increment iteration
    iteration 4:    hard cap reached → set content_piece.status = human_review, stop

Every WriterAgent and EditorAgent call is logged to agent_runs via BaseAgent.
The FeedbackLoop table records every editor verdict for the audit trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agents.editor_agent import EditorAgent
from agents.research_agent import ResearchAgent
from agents.writer_agent import WriterAgent
from config import ContentTypeConfig, RegionConfig
from db.models import ContentPiece, FeedbackLoop
from orchestrator.job_model import Article, ArticleDraft, EditorDecision

MAX_ITERATIONS = 3


class AgentChain:
    """
    Runs the full Research → Write → Edit pipeline for a single region task.

    Usage:
        chain = AgentChain()
        draft = await chain.run(
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
    ) -> ArticleDraft:
        """
        Run the full agent chain and return the final approved (or escalated) draft.
        Updates the content_piece row with the final headline, body, and status.
        """
        # --- Research (once per region, not repeated on revision) ---
        brief = await self._researcher.run_research(
            topic,
            articles,
            session=session,
            content_piece_id=content_piece_id,
            iteration=1,
            job_cost_so_far=job_cost_so_far,
        )

        editor_feedback: str | None = None
        draft: ArticleDraft | None = None

        for iteration in range(1, MAX_ITERATIONS + 1):
            # --- Write ---
            draft = await self._writer.run_write(
                brief,
                region_config,
                ct_config,
                session=session,
                content_piece_id=content_piece_id,
                iteration=iteration,
                job_cost_so_far=job_cost_so_far,
                editor_feedback=editor_feedback,
            )

            # --- Edit ---
            verdict = await self._editor.run_edit(
                draft,
                ct_config,
                session=session,
                content_piece_id=content_piece_id,
                iteration=iteration,
                job_cost_so_far=job_cost_so_far,
            )

            # Log feedback loop row
            await _log_feedback(
                session,
                content_piece_id=content_piece_id,
                iteration=iteration,
                status=verdict.status.value,
                feedback=verdict.feedback,
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
                return draft

            # Revise — carry feedback into next iteration
            editor_feedback = verdict.feedback

        # Hard cap reached — escalate to human review
        await _update_piece(
            session, content_piece_id,
            headline=draft.headline,
            body=draft.body,
            word_count=draft.word_count,
            iteration_count=MAX_ITERATIONS,
            status="human_review",
        )
        return draft


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
