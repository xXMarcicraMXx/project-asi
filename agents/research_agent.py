"""
ResearchAgent — extracts structured facts from source articles.

Input:  list[Article] + topic string
Output: ResearchBrief (key_facts, direct_quotes, data_points,
        conflicting_perspectives, source_urls)

Model: Claude Haiku — fast and cheap for parsing/extraction tasks.
The agent never invents content — it only extracts what is explicitly
present in the provided articles.
"""

from __future__ import annotations

import json
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agents.base_agent import BaseAgent
from orchestrator.job_model import Article, ResearchBrief


class ResearchAgent(BaseAgent):
    AGENT_NAME = "research_agent"
    MODEL = "claude-haiku-4-5-20251001"
    SYSTEM_PROMPT = """You are a research analyst for a news organisation.

Your job is to read a set of provided news articles and extract structured
information. You do not write prose. You output only structured data.

Rules:
- Extract only facts, quotes, and data points that appear explicitly in the
  provided articles. Do not invent or infer.
- Preserve direct quotes exactly as written. Do not paraphrase.
- Identify conflicting perspectives if different sources disagree.
- Output must be valid JSON matching this exact schema:
  {
    "topic": "<string>",
    "key_facts": ["<string>", ...],
    "direct_quotes": ["<string>", ...],
    "data_points": ["<string>", ...],
    "conflicting_perspectives": ["<string>", ...],
    "source_urls": ["<string>", ...]
  }
- key_facts must contain at least 5 distinct factual statements.
- If a field has no relevant content, return an empty list — never null.
- Output the JSON object only. No preamble, no explanation."""

    async def run_research(
        self,
        topic: str,
        articles: list[Article],
        *,
        session: AsyncSession,
        content_piece_id: uuid.UUID,
        iteration: int = 1,
        job_cost_so_far: float = 0.0,
    ) -> ResearchBrief:
        """
        Extract a structured ResearchBrief from the provided articles.
        Raises ValueError if the agent returns invalid JSON or fails schema validation.
        """
        user_message = _build_user_message(topic, articles)

        raw = await self.run(
            user_message,
            session=session,
            content_piece_id=content_piece_id,
            iteration=iteration,
            job_cost_so_far=job_cost_so_far,
        )

        return _parse_brief(raw, topic, articles)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_user_message(topic: str, articles: list[Article]) -> str:
    sources_block = "\n\n".join(
        f"--- SOURCE {i+1}: {a.source_name} ---\n"
        f"Title: {a.title}\n"
        f"URL: {a.url}\n\n"
        f"{a.body[:3000]}"
        for i, a in enumerate(articles)
    )
    return f"""Topic: {topic}

Articles to analyse:

{sources_block}

Extract a ResearchBrief JSON object from the articles above."""


def _parse_brief(raw: str, topic: str, articles: list[Article]) -> ResearchBrief:
    """
    Parse the agent's JSON response into a validated ResearchBrief.
    Strips markdown code fences if present.
    """
    # Strip ```json ... ``` fences if the model added them
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"ResearchAgent returned invalid JSON: {exc}\n\nRaw response:\n{raw}"
        )

    # Ensure topic is set (agent sometimes omits it)
    data.setdefault("topic", topic)

    # Backfill source_urls from article objects if agent left them empty
    if not data.get("source_urls"):
        data["source_urls"] = [a.url for a in articles if a.url]

    brief = ResearchBrief.model_validate(data)

    if len(brief.key_facts) < 5:
        raise ValueError(
            f"ResearchBrief has only {len(brief.key_facts)} key_facts "
            f"(minimum 5 required). Facts: {brief.key_facts}"
        )

    return brief
