"""
WriterAgent — produces a regional article draft from a ResearchBrief.

Input:  ResearchBrief + RegionConfig + ContentTypeConfig
        + optional editor_feedback (on revision passes)
        + optional rag_context (Sprint 3+ — Pinecone persona docs)
Output: ArticleDraft (headline, body, word_count, region_id, iteration)

Model: Claude Sonnet — full creative capacity for article drafting.

Sprint 1–2 note: persona context comes entirely from region YAML.
RAG enrichment is wired in Sprint 3 without changing this interface.
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base_agent import BaseAgent
from config import ContentTypeConfig, RegionConfig
from orchestrator.job_model import ArticleDraft, ResearchBrief


class WriterAgent(BaseAgent):
    AGENT_NAME = "writer_agent"
    MODEL = "claude-sonnet-4-20250514"
    SYSTEM_PROMPT = """You are a professional journalist and editor.

You write formal, well-structured articles based on provided research briefs.
You adapt your editorial voice and perspective to the regional context given
in each request. You do not fabricate statistics, quotes, or sources.

Rules:
- Follow the format instructions provided in each request exactly.
- Adopt the editorial voice described in each request — do not default to a
  neutral or American news voice unless explicitly instructed.
- Cite sources inline using [Source Name] notation.
- Every claim must be traceable to the research brief. Do not extrapolate.
- Output raw markdown only. No preamble, no explanation, no sign-off."""

    async def run_write(
        self,
        brief: ResearchBrief,
        region_config: RegionConfig,
        ct_config: ContentTypeConfig,
        *,
        session: AsyncSession,
        content_piece_id: uuid.UUID,
        iteration: int = 1,
        job_cost_so_far: float = 0.0,
        editor_feedback: Optional[str] = None,
        rag_context: Optional[str] = None,
    ) -> ArticleDraft:
        """
        Produce one article draft.

        On revision passes (iteration > 1), pass editor_feedback so the
        writer knows exactly what to fix.
        """
        user_message = _build_user_message(
            brief, region_config, ct_config,
            editor_feedback=editor_feedback,
            rag_context=rag_context,
        )

        raw = await self.run(
            user_message,
            session=session,
            content_piece_id=content_piece_id,
            iteration=iteration,
            job_cost_so_far=job_cost_so_far,
        )

        return _parse_draft(raw, region_config.region_id, iteration)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_user_message(
    brief: ResearchBrief,
    region_config: RegionConfig,
    ct_config: ContentTypeConfig,
    editor_feedback: Optional[str],
    rag_context: Optional[str],
) -> str:
    parts: list[str] = []

    parts.append(f"EDITORIAL VOICE:\n{region_config.editorial_voice.strip()}")

    parts.append(
        f"FORMAT INSTRUCTIONS:\n{ct_config.writer_instructions.strip()}\n"
        f"Minimum words: {ct_config.output.min_words}\n"
        f"Maximum words: {ct_config.output.max_words}"
    )

    if rag_context:
        parts.append(
            f"ADDITIONAL PERSONA CONTEXT (from editorial archive):\n{rag_context.strip()}"
        )

    if editor_feedback:
        parts.append(
            f"EDITOR FEEDBACK FROM PREVIOUS DRAFT (address every point):\n{editor_feedback.strip()}"
        )

    # Research brief
    facts_block = "\n".join(f"- {f}" for f in brief.key_facts)
    data_block = "\n".join(f"- {d}" for d in brief.data_points) if brief.data_points else "None"
    quotes_block = "\n".join(f'- "{q}"' for q in brief.direct_quotes) if brief.direct_quotes else "None"
    perspectives_block = (
        "\n".join(f"- {p}" for p in brief.conflicting_perspectives)
        if brief.conflicting_perspectives else "None"
    )

    parts.append(
        f"RESEARCH BRIEF:\n"
        f"Topic: {brief.topic}\n\n"
        f"Key facts:\n{facts_block}\n\n"
        f"Data points:\n{data_block}\n\n"
        f"Direct quotes:\n{quotes_block}\n\n"
        f"Conflicting perspectives:\n{perspectives_block}"
    )

    parts.append("Write the article now.")

    return "\n\n".join(parts)


def _parse_draft(raw: str, region_id: str, iteration: int) -> ArticleDraft:
    """
    Extract headline and body from raw markdown output.
    The first non-empty line (stripped of # markers) is the headline.
    """
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    headline = lines[0].lstrip("#").strip() if lines else "Untitled"
    word_count = len(raw.split())

    return ArticleDraft(
        headline=headline,
        body=raw,
        word_count=word_count,
        region_id=region_id,
        iteration=iteration,
    )
