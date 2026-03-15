"""
P1-D3 tests — StatusAgent and CurationAgent.

All Anthropic API calls are mocked — no real network I/O.
Tests cover: happy path, JSON parse failure + retry, fallback defaults,
curation_bias injection, story index mapping, edge cases.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import os

import pytest

from agents.curation_agent import CurationAgent
from agents.status_agent import StatusAgent, _DEFAULT_STATUS
from orchestrator.brief_job_model import CuratedStory, DailyStatus, RawStory


# ---------------------------------------------------------------------------
# Module-level setup — agents require ANTHROPIC_API_KEY at instantiation time
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """Ensure ANTHROPIC_API_KEY is present so BaseAgent.__init__ doesn't raise."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_stories(n: int = 10) -> list[RawStory]:
    return [
        RawStory(
            title=f"Story {i}",
            url=f"https://example.com/story-{i}",
            source_name="Test Source",
            body=f"Body text for story {i}. " * 5,
            category_hint="Politics" if i % 2 == 0 else "Finance",
        )
        for i in range(1, n + 1)
    ]


def _mock_agent_run(text: str):
    """Patch BaseAgent.run to return a fixed text string."""
    return patch.object(
        __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
        "run",
        new_callable=AsyncMock,
        return_value=text,
    )


def _make_session() -> MagicMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests: StatusAgent
# ---------------------------------------------------------------------------

class TestStatusAgent:
    @pytest.mark.asyncio
    async def test_parses_valid_json_response(self):
        payload = {
            "daily_color": "Amber",
            "sentiment": "Tense",
            "mood_headline": "Elevated tensions across multiple fronts today.",
        }
        with _mock_agent_run(json.dumps(payload)):
            agent = StatusAgent()
            result = await agent.run_brief(
                _make_stories(5), session=_make_session()
            )
        assert isinstance(result, DailyStatus)
        assert result.daily_color == "Amber"
        assert result.sentiment == "Tense"
        assert result.mood_headline == "Elevated tensions across multiple fronts today."

    @pytest.mark.asyncio
    async def test_parses_red_green_optimistic(self):
        payload = {"daily_color": "Green", "sentiment": "Optimistic",
                   "mood_headline": "Markets steady as central banks signal pause."}
        with _mock_agent_run(json.dumps(payload)):
            agent = StatusAgent()
            result = await agent.run_brief(_make_stories(3), session=_make_session())
        assert result.daily_color == "Green"
        assert result.sentiment == "Optimistic"

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fences(self):
        payload = {"daily_color": "Red", "sentiment": "Crisis",
                   "mood_headline": "Active military conflict escalates in disputed region."}
        wrapped = f"```json\n{json.dumps(payload)}\n```"
        with _mock_agent_run(wrapped):
            agent = StatusAgent()
            result = await agent.run_brief(_make_stories(5), session=_make_session())
        assert result.daily_color == "Red"
        assert result.sentiment == "Crisis"

    @pytest.mark.asyncio
    async def test_falls_back_to_default_on_two_parse_failures(self):
        with _mock_agent_run("This is not JSON at all, sorry!"):
            agent = StatusAgent()
            result = await agent.run_brief(_make_stories(5), session=_make_session())
        assert result == _DEFAULT_STATUS
        assert result.daily_color == "Amber"
        assert result.sentiment == "Cautious"

    @pytest.mark.asyncio
    async def test_falls_back_on_invalid_enum_value(self):
        bad = {"daily_color": "Purple", "sentiment": "Tense",
               "mood_headline": "Some headline here."}
        with _mock_agent_run(json.dumps(bad)):
            agent = StatusAgent()
            result = await agent.run_brief(_make_stories(5), session=_make_session())
        assert result == _DEFAULT_STATUS

    @pytest.mark.asyncio
    async def test_retries_exactly_twice_on_failure(self):
        call_count = 0

        async def fake_run(self_ref, msg, **kwargs):
            nonlocal call_count
            call_count += 1
            return "not json"

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = StatusAgent()
            result = await agent.run_brief(_make_stories(5), session=_make_session())

        assert call_count == 2
        assert result == _DEFAULT_STATUS

    @pytest.mark.asyncio
    async def test_second_attempt_includes_json_hint(self):
        messages_seen: list[str] = []

        async def fake_run(self_ref, msg, **kwargs):
            messages_seen.append(msg)
            return "not json"

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = StatusAgent()
            await agent.run_brief(_make_stories(5), session=_make_session())

        assert len(messages_seen) == 2
        assert "IMPORTANT" in messages_seen[1]
        assert "JSON" in messages_seen[1]

    @pytest.mark.asyncio
    async def test_truncates_story_pool_to_50(self):
        messages_seen: list[str] = []

        async def fake_run(self_ref, msg, **kwargs):
            messages_seen.append(msg)
            payload = {"daily_color": "Green", "sentiment": "Optimistic",
                       "mood_headline": "Stable."}
            return json.dumps(payload)

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = StatusAgent()
            await agent.run_brief(_make_stories(80), session=_make_session())

        # Message should only list up to 50 stories
        msg = messages_seen[0]
        assert "50." in msg          # line "50. Story 50"
        assert "51." not in msg      # story 51+ not included

    def test_build_user_message_format(self):
        stories = _make_stories(3)
        msg = StatusAgent._build_user_message(stories)
        assert "1." in msg
        assert "2." in msg
        assert "3." in msg
        assert "Story 1" in msg
        assert "Test Source" in msg

    def test_parse_response_raises_on_empty_string(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            StatusAgent._parse_response("")

    def test_parse_response_raises_on_missing_field(self):
        bad = json.dumps({"daily_color": "Amber"})  # missing sentiment + mood_headline
        with pytest.raises(Exception):
            StatusAgent._parse_response(bad)


# ---------------------------------------------------------------------------
# Tests: CurationAgent
# ---------------------------------------------------------------------------

def _valid_selection_payload(story_indices: list[int]) -> str:
    items = [
        {
            "story_index": i,
            "category": "Politics" if i % 2 != 0 else "Finance",
            "significance_score": round(0.9 - (i * 0.05), 2),
        }
        for i in story_indices
    ]
    return json.dumps(items)


class TestCurationAgent:
    @pytest.mark.asyncio
    async def test_parses_valid_json_response(self):
        stories = _make_stories(10)
        payload = _valid_selection_payload([1, 2, 3, 4, 5])
        with _mock_agent_run(payload):
            agent = CurationAgent()
            result = await agent.run_region(
                stories,
                region_id="eu",
                curation_bias="Focus on European regulatory issues.",
                session=_make_session(),
            )
        assert len(result) == 5
        assert all(isinstance(s, CuratedStory) for s in result)

    @pytest.mark.asyncio
    async def test_assigns_raw_story_id_from_index(self):
        stories = _make_stories(10)
        payload = json.dumps([
            {"story_index": 3, "category": "Tech", "significance_score": 0.8},
            {"story_index": 7, "category": "Events", "significance_score": 0.7},
            {"story_index": 1, "category": "Politics", "significance_score": 0.9},
            {"story_index": 5, "category": "Finance", "significance_score": 0.6},
            {"story_index": 9, "category": "Politics", "significance_score": 0.5},
        ])
        with _mock_agent_run(payload):
            agent = CurationAgent()
            result = await agent.run_region(
                stories, region_id="na", curation_bias=None, session=_make_session()
            )
        ids = {s.raw_story_id for s in result}
        assert stories[2].id in ids  # story_index=3 → stories[2]
        assert stories[6].id in ids  # story_index=7 → stories[6]

    @pytest.mark.asyncio
    async def test_sorts_by_significance_score_descending(self):
        stories = _make_stories(10)
        payload = _valid_selection_payload([1, 2, 3, 4, 5])
        with _mock_agent_run(payload):
            agent = CurationAgent()
            result = await agent.run_region(
                stories, region_id="latam", curation_bias=None, session=_make_session()
            )
        scores = [s.significance_score for s in result]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_caps_at_8_stories(self):
        stories = _make_stories(15)
        # LLM tries to return 12 selections
        payload = _valid_selection_payload(list(range(1, 13)))
        with _mock_agent_run(payload):
            agent = CurationAgent()
            result = await agent.run_region(
                stories, region_id="apac", curation_bias=None, session=_make_session()
            )
        assert len(result) <= 8

    @pytest.mark.asyncio
    async def test_raises_runtime_error_on_zero_stories(self):
        stories = _make_stories(10)
        with _mock_agent_run("[]"):
            agent = CurationAgent()
            with pytest.raises(RuntimeError, match="0 valid stories"):
                await agent.run_region(
                    stories, region_id="africa", curation_bias=None, session=_make_session()
                )

    @pytest.mark.asyncio
    async def test_raises_value_error_after_two_json_failures(self):
        stories = _make_stories(10)
        with _mock_agent_run("This is definitely not JSON"):
            agent = CurationAgent()
            with pytest.raises(ValueError):
                await agent.run_region(
                    stories, region_id="eu", curation_bias=None, session=_make_session()
                )

    @pytest.mark.asyncio
    async def test_skips_invalid_story_index(self):
        stories = _make_stories(5)
        # story_index=99 is out of bounds — should be skipped, not crash
        payload = json.dumps([
            {"story_index": 99, "category": "Tech", "significance_score": 0.9},
            {"story_index": 1, "category": "Politics", "significance_score": 0.8},
            {"story_index": 2, "category": "Finance", "significance_score": 0.7},
            {"story_index": 3, "category": "Events", "significance_score": 0.6},
            {"story_index": 4, "category": "Tech", "significance_score": 0.5},
            {"story_index": 5, "category": "Politics", "significance_score": 0.4},
        ])
        with _mock_agent_run(payload):
            agent = CurationAgent()
            result = await agent.run_region(
                stories, region_id="na", curation_bias=None, session=_make_session()
            )
        # index 99 is skipped; remaining 5 valid
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_curation_bias_appears_in_user_message(self):
        messages_seen: list[str] = []

        async def fake_run(self_ref, msg, **kwargs):
            messages_seen.append(msg)
            return _valid_selection_payload([1, 2, 3, 4, 5])

        stories = _make_stories(5)
        bias = "Focus heavily on central bank decisions and currency movements."
        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = CurationAgent()
            await agent.run_region(
                stories, region_id="eu", curation_bias=bias, session=_make_session()
            )
        assert bias in messages_seen[0]

    @pytest.mark.asyncio
    async def test_region_id_appears_in_user_message(self):
        messages_seen: list[str] = []

        async def fake_run(self_ref, msg, **kwargs):
            messages_seen.append(msg)
            return _valid_selection_payload([1, 2, 3, 4, 5])

        stories = _make_stories(5)
        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = CurationAgent()
            await agent.run_region(
                stories, region_id="apac", curation_bias=None, session=_make_session()
            )
        assert "APAC" in messages_seen[0]

    @pytest.mark.asyncio
    async def test_strips_markdown_fences_from_response(self):
        stories = _make_stories(5)
        inner = _valid_selection_payload([1, 2, 3, 4, 5])
        wrapped = f"```json\n{inner}\n```"
        with _mock_agent_run(wrapped):
            agent = CurationAgent()
            result = await agent.run_region(
                stories, region_id="na", curation_bias=None, session=_make_session()
            )
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_retries_with_json_hint_on_first_failure(self):
        messages_seen: list[str] = []
        call_count = [0]

        async def fake_run(self_ref, msg, **kwargs):
            messages_seen.append(msg)
            call_count[0] += 1
            if call_count[0] == 1:
                return "not json"
            return _valid_selection_payload([1, 2, 3, 4, 5])

        stories = _make_stories(5)
        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = CurationAgent()
            result = await agent.run_region(
                stories, region_id="eu", curation_bias=None, session=_make_session()
            )

        assert call_count[0] == 2
        assert "IMPORTANT" in messages_seen[1]
        assert len(result) == 5

    def test_build_user_message_includes_all_stories(self):
        stories = _make_stories(7)
        msg = CurationAgent._build_user_message(stories, "eu", "Focus on EU policy.")
        for i in range(1, 8):
            assert f"{i}." in msg
        assert "Focus on EU policy." in msg
        assert "REGION: EU" in msg

    def test_parse_response_raises_on_non_array(self):
        with pytest.raises(ValueError, match="Expected JSON array"):
            CurationAgent._parse_response('{"story_index": 1}')

    def test_parse_response_raises_on_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            CurationAgent._parse_response("not json at all")
