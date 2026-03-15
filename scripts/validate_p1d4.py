"""
P1-D4 validation script — NewsletterWriterAgent.

Checks:
  1. NewsletterWriterAgent imports and instantiates
  2. Model is Sonnet (not Haiku)
  3. SYSTEM_PROMPT includes adversarial text warning
  4. _clean() strips markdown code fences
  5. _truncate() at sentence boundary
  6. _truncate() hard-truncates when first sentence exceeds limit
  7. _fallback_summary() includes title + source
  8. _build_user_message() includes region, status, title, source, body
  9. StoryEntry.summary max_length is >= 1200 (updated from 900)
 10. run_story() returns StoryEntry with correct rank and word_count (mocked)
 11. CLI 'write' subcommand present with --region flag

Run from repo root:
    python scripts/validate_p1d4.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-validate")


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return condition


def main() -> int:
    failures = 0
    print("\nP1-D4 Validation - NewsletterWriterAgent\n" + "-" * 60)

    # ── 1-2. Import and model ────────────────────────────────────────────────
    try:
        from agents.newsletter_writer_agent import (
            NewsletterWriterAgent, _build_user_message,
            _clean, _fallback_summary, _truncate,
        )
        agent = NewsletterWriterAgent()
        ok = check("NewsletterWriterAgent instantiates", True, f"model={agent.MODEL}")
        ok2 = check(
            "Model is Sonnet",
            "sonnet" in agent.MODEL.lower(),
            agent.MODEL,
        )
        if not ok or not ok2:
            failures += 1
    except Exception as exc:
        check("NewsletterWriterAgent instantiates", False, str(exc))
        return 1

    # ── 3. SYSTEM_PROMPT has adversarial warning ─────────────────────────────
    ok = check(
        "SYSTEM_PROMPT includes adversarial text warning",
        "adversarial text" in NewsletterWriterAgent.SYSTEM_PROMPT.lower(),
    )
    if not ok:
        failures += 1

    # ── 4. _clean() strips fences ───────────────────────────────────────────
    ok = check(
        "_clean() strips markdown code fences",
        _clean("```json\nHello world.\n```") == "Hello world.",
    )
    if not ok:
        failures += 1

    # ── 5-6. _truncate() ────────────────────────────────────────────────────
    sentences = ["Sentence {:03d} ends here.".format(i) for i in range(20)]
    long_text = " ".join(sentences)
    result = _truncate(long_text, 10)
    ok = check(
        "_truncate() respects max_words limit",
        len(result.split()) <= 10,
        f"got {len(result.split())} words",
    )
    if not ok:
        failures += 1

    long_sentence = " ".join(f"word{i}" for i in range(200)) + "."
    result2 = _truncate(long_sentence, 50)
    ok = check(
        "_truncate() hard-truncates when first sentence exceeds limit",
        len(result2.split()) <= 51,
        f"got {len(result2.split())} words",
    )
    if not ok:
        failures += 1

    # ── 7. _fallback_summary() ──────────────────────────────────────────────
    from orchestrator.brief_job_model import CuratedStory
    story = CuratedStory(
        raw_story_id=uuid.uuid4(),
        title="EU Summit Reaches Deal on AI Regulation",
        url="https://politico.eu/ai",
        source_name="Politico Europe",
        category="Tech",
        significance_score=0.88,
        body="The European Union reached a landmark agreement.",
    )
    fallback = _fallback_summary(story)
    ok = check(
        "_fallback_summary() includes title and source",
        "EU Summit" in fallback and "Politico Europe" in fallback,
        f"result: {fallback[:80]}",
    )
    if not ok:
        failures += 1

    # ── 8. _build_user_message() ────────────────────────────────────────────
    from orchestrator.brief_job_model import DailyStatus
    status = DailyStatus(
        daily_color="Red", sentiment="Crisis",
        mood_headline="Active escalation across multiple fronts."
    )
    msg = _build_user_message(story, "eu", status)
    checks = {
        "REGION: EU": "REGION: EU" in msg,
        "Red in status": "Red" in msg,
        "Crisis in status": "Crisis" in msg,
        "title in message": "EU Summit" in msg,
        "source in message": "Politico Europe" in msg,
        "body in message": "European Union" in msg,
    }
    for label, condition in checks.items():
        ok = check(f"_build_user_message: {label}", condition)
        if not ok:
            failures += 1

    # ── 9. StoryEntry.summary max_length >= 1200 ────────────────────────────
    from orchestrator.brief_job_model import StoryEntry
    import inspect
    max_len = StoryEntry.model_fields["summary"].metadata
    # Pydantic stores constraints as metadata objects — check via field_info
    field_info = StoryEntry.model_fields["summary"]
    constraints = {type(m).__name__: m for m in (field_info.metadata or [])}
    max_length_val = getattr(constraints.get("MaxLen"), "max_length", None)
    ok = check(
        "StoryEntry.summary max_length >= 1200",
        max_length_val is not None and max_length_val >= 1200,
        f"max_length={max_length_val}",
    )
    if not ok:
        failures += 1

    # ── 10. run_story() mock round-trip ─────────────────────────────────────
    def _words(n: int) -> str:
        return " ".join(f"w{i}" for i in range(n)) + "."

    async def _run():
        summary_text = _words(115)
        fake_session = AsyncMock()
        fake_session.add = MagicMock()
        fake_session.commit = AsyncMock()

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", AsyncMock(return_value=summary_text)
        ):
            a = NewsletterWriterAgent()
            return await a.run_story(
                story, rank=3, region_id="eu",
                daily_status=status, session=fake_session,
            )

    try:
        entry = asyncio.run(_run())
        ok = check(
            "run_story() returns StoryEntry with rank=3",
            entry.rank == 3 and entry.word_count == 115,
            f"rank={entry.rank}, word_count={entry.word_count}",
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("run_story() mock round-trip", False, str(exc))
        failures += 1

    # ── 11. CLI 'write' subcommand ──────────────────────────────────────────
    cli_src = (REPO_ROOT / "cli.py").read_text(encoding="utf-8")
    ok = check(
        "CLI has 'write' subcommand with --region flag",
        '"write"' in cli_src and "cmd_write" in cli_src and '"--region"' in cli_src,
    )
    if not ok:
        failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P1-D4 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
