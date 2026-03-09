"""
EditorAgent — evaluates an article draft and returns a structured verdict.

Input:  ArticleDraft + editor criteria from ContentTypeConfig
Output: EditorVerdict {"status": "approve" | "revise", "feedback": "..."}

Model: Claude Sonnet — same quality bar as the writer.

The system prompt is fully static. Criteria are injected into the user
message only, preserving prompt cache hits across all editing calls.
"""

from __future__ import annotations

import json
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base_agent import BaseAgent
from config import ContentTypeConfig
from orchestrator.job_model import ArticleDraft, EditorDecision, EditorVerdict


class EditorAgent(BaseAgent):
    AGENT_NAME = "editor_agent"
    MODEL = "claude-sonnet-4-20250514"
    SYSTEM_PROMPT = """You are a senior editor at a news organisation.

You evaluate article drafts against a checklist of editorial criteria.
You return a structured verdict — never free-form feedback.

Rules:
- Evaluate the draft against every criterion in the provided list.
- If all criteria pass: return {"status": "approve", "feedback": "Approved."}
- If any criterion fails: return {"status": "revise", "feedback": "<specific, actionable instruction for the writer — one paragraph>"}
- Feedback must name the specific criterion that failed and explain what to fix.
- Output must be valid JSON. No preamble, no prose outside the JSON object."""

    async def run_edit(
        self,
        draft: ArticleDraft,
        ct_config: ContentTypeConfig,
        *,
        session: AsyncSession,
        content_piece_id: uuid.UUID,
        iteration: int = 1,
        job_cost_so_far: float = 0.0,
    ) -> EditorVerdict:
        """
        Evaluate a draft and return an EditorVerdict.
        Raises ValueError if the agent returns invalid JSON.
        """
        user_message = _build_user_message(draft, ct_config)

        raw = await self.run(
            user_message,
            session=session,
            content_piece_id=content_piece_id,
            iteration=iteration,
            job_cost_so_far=job_cost_so_far,
        )

        return _parse_verdict(raw)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_user_message(draft: ArticleDraft, ct_config: ContentTypeConfig) -> str:
    criteria_block = "\n".join(f"- {c}" for c in ct_config.editor_criteria)
    return (
        f"EDITORIAL CRITERIA:\n{criteria_block}\n\n"
        f"ARTICLE DRAFT (iteration {draft.iteration}):\n"
        f"{draft.headline}\n\n"
        f"{draft.body}\n\n"
        "Evaluate the draft against every criterion above and return your JSON verdict."
    )


def _parse_verdict(raw: str) -> EditorVerdict:
    """Parse and validate the agent's JSON response into an EditorVerdict."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"EditorAgent returned invalid JSON: {exc}\n\nRaw response:\n{raw}"
        )

    return EditorVerdict.model_validate(data)
