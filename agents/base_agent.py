"""
BaseAgent — the foundation for every agent in the ASI pipeline.

Responsibilities:
- Enforce the static system prompt contract (prompt caching compliant)
- Call the Anthropic API with exponential backoff (3 retries on 429/529)
- Check the per-job cost ceiling before every call
- Log every invocation to agent_runs (tokens + cost_usd + duration_ms)

Subclasses must define:
    SYSTEM_PROMPT : str   — fully static, no variable injection
    MODEL         : str   — model ID from config/settings.yaml
    AGENT_NAME    : str   — matches agent_runs.agent_name column

Subclasses call:
    text = await self.run(user_message, session=session,
                          content_piece_id=..., iteration=1,
                          job_cost_so_far=0.0)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import AgentRun

# ---------------------------------------------------------------------------
# Pricing table (per 1 million tokens, USD)
# Update these constants when Anthropic changes list prices.
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    # claude-haiku-4-5 (parser / research tasks)
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    # claude-sonnet-4 (writer / editor tasks)
    "claude-sonnet-4-20250514":  {"input": 3.00, "output": 15.00},
}

_DEFAULT_PRICING = {"input": 3.00, "output": 15.00}   # safe fallback


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model, _DEFAULT_PRICING)
    return (
        input_tokens  / 1_000_000 * pricing["input"] +
        output_tokens / 1_000_000 * pricing["output"]
    )


# ---------------------------------------------------------------------------
# Retry constants
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS = {429, 529}
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.5   # seconds — delays: 1.5 s, 3 s, 6 s


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """
    Abstract base for all ASI agents.

    System prompt is declared as a class-level constant so it is always
    static — never assembled at runtime — enabling Anthropic prompt caching.
    """

    SYSTEM_PROMPT: str   # subclass must define
    MODEL: str           # subclass must define
    AGENT_NAME: str      # subclass must define

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"]
        )
        self._max_usd = float(os.environ.get("ASI_MAX_USD_PER_JOB", "2.00"))
        # Updated after every successful run() call — callers use these to
        # accumulate the running job cost and include it in cost reports.
        self.last_call_cost: float = 0.0
        self.last_call_input_tokens: int = 0
        self.last_call_output_tokens: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        *,
        session: AsyncSession,
        content_piece_id: Optional[uuid.UUID] = None,
        iteration: int = 1,
        job_cost_so_far: float = 0.0,
    ) -> str:
        """
        Call the Anthropic API and return the response text.

        Raises:
            RuntimeError  — cost ceiling exceeded before call
            anthropic.APIError — after all retries exhausted
        """
        self._check_cost_ceiling(job_cost_so_far)

        start_ms = time.monotonic()
        response = await self._call_with_retry(user_message)
        duration_ms = int((time.monotonic() - start_ms) * 1000)

        input_tokens  = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = _cost_usd(self.MODEL, input_tokens, output_tokens)

        # Expose last-call stats so AgentChain can accumulate the running total.
        self.last_call_cost = cost
        self.last_call_input_tokens = input_tokens
        self.last_call_output_tokens = output_tokens

        await self._log_run(
            session=session,
            content_piece_id=content_piece_id,
            iteration=iteration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            duration_ms=duration_ms,
        )

        return response.content[0].text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_cost_ceiling(self, job_cost_so_far: float) -> None:
        if job_cost_so_far >= self._max_usd:
            raise RuntimeError(
                f"Job cost ceiling reached: ${job_cost_so_far:.4f} >= "
                f"${self._max_usd:.2f}. Aborting before API call."
            )

    async def _call_with_retry(
        self, user_message: str
    ) -> anthropic.types.Message:
        """
        Call the API with exponential backoff on 429 / 529.
        Raises the last exception if all retries are exhausted.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._client.messages.create(
                    model=self.MODEL,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": self.SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_message}],
                    extra_headers={
                        "anthropic-beta": "prompt-caching-2024-07-31"
                    },
                )
            except anthropic.RateLimitError as exc:
                last_exc = exc
            except anthropic.APIStatusError as exc:
                if exc.status_code not in _RETRYABLE_STATUS:
                    raise
                last_exc = exc

            if attempt < _MAX_RETRIES:
                delay = _BACKOFF_BASE * (2 ** attempt)
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _log_run(
        self,
        *,
        session: AsyncSession,
        content_piece_id: uuid.UUID,
        iteration: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        duration_ms: int,
    ) -> None:
        run = AgentRun(
            content_piece_id=content_piece_id,
            agent_name=self.AGENT_NAME,
            iteration=iteration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
        )
        session.add(run)
        await session.commit()
