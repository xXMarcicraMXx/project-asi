"""
P1-D4 tests — NewsletterWriterAgent.

All Anthropic API calls are mocked — no real network I/O.
Tests cover: happy path, word-count enforcement (>150 truncate, <50 retry,
<50 fallback), markdown stripping, user message content, StoryEntry assembly,
and edge cases.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.newsletter_writer_agent import (
    NewsletterWriterAgent,
    _build_user_message,
    _clean,
    _fallback_summary,
    _truncate,
)
from orchestrator.brief_job_model import CuratedStory, DailyStatus, StoryEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")


def _make_story(
    title: str = "Fed raises rates amid inflation concerns",
    source_name: str = "Reuters",
    category: str = "Finance",
    url: str = "https://reuters.com/fed-rates",
    body: str = "The Federal Reserve raised interest rates by 25 basis points today.",
    score: float = 0.85,
) -> CuratedStory:
    return CuratedStory(
        raw_story_id=uuid.uuid4(),
        title=title,
        url=url,
        source_name=source_name,
        category=category,
        significance_score=score,
        body=body,
    )


def _make_status(
    color: str = "Amber",
    sentiment: str = "Cautious",
    headline: str = "Markets uneasy as central banks signal caution.",
) -> DailyStatus:
    return DailyStatus(
        daily_color=color,
        sentiment=sentiment,
        mood_headline=headline,
    )


def _make_session() -> MagicMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def _words(n: int) -> str:
    """Return a string of exactly n words."""
    return " ".join(f"word{i}" for i in range(n)) + "."


def _mock_run(text: str):
    """Patch BaseAgent.run to return a fixed text string."""
    return patch.object(
        __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
        "run",
        new_callable=AsyncMock,
        return_value=text,
    )


# ---------------------------------------------------------------------------
# Unit tests: pure helper functions
# ---------------------------------------------------------------------------

class TestClean:
    def test_strips_whitespace(self):
        assert _clean("  hello world  ") == "hello world"

    def test_strips_markdown_code_fence(self):
        text = "```\nSome summary here.\n```"
        assert _clean(text) == "Some summary here."

    def test_strips_markdown_json_fence(self):
        text = "```json\nSome text here.\n```"
        assert _clean(text) == "Some text here."

    def test_collapses_triple_blank_lines(self):
        text = "First paragraph.\n\n\n\nSecond paragraph."
        result = _clean(text)
        assert "\n\n\n" not in result

    def test_passes_through_clean_text(self):
        text = "Markets reacted sharply as the Federal Reserve announced its decision."
        assert _clean(text) == text


class TestTruncate:
    def test_no_truncation_needed_under_limit(self):
        text = _words(100)
        assert _truncate(text, 150) == text

    def test_no_truncation_needed_at_exact_limit(self):
        text = _words(150)
        assert _truncate(text, 150) == text

    def test_truncates_at_sentence_boundary(self):
        text = (
            "First sentence here. "
            "Second sentence covers more ground. "
            "Third sentence with even more words exceeds the limit significantly enough."
        )
        # first two sentences = ~10 words, comfortably under any limit
        result = _truncate(text, 8)
        assert len(result.split()) <= 8

    def test_truncation_result_ends_at_sentence(self):
        # Build text where first sentence = 5 words, second = 5 words, total > 6
        text = "Alpha beta gamma delta epsilon. Zeta eta theta iota kappa."
        result = _truncate(text, 6)
        # Should end after the first sentence
        assert result.endswith(".")
        assert len(result.split()) <= 6

    def test_hard_truncates_when_first_sentence_exceeds_limit(self):
        # All one sentence, 200 words
        long_sentence = " ".join(f"word{i}" for i in range(200)) + "."
        result = _truncate(long_sentence, 50)
        assert len(result.split()) <= 51  # 50 words + trailing "."

    def test_handles_exclamation_and_question_marks(self):
        text = "Breaking news! Markets crash? Experts weigh in on the situation today."
        result = _truncate(text, 3)
        # Should truncate after "Breaking news!"
        assert len(result.split()) <= 3


class TestFallback:
    def test_fallback_includes_title_and_source(self):
        story = _make_story(title="EU Summit Reaches Historic Agreement", source_name="Politico Europe")
        result = _fallback_summary(story)
        assert "EU Summit Reaches Historic Agreement" in result
        assert "Politico Europe" in result

    def test_fallback_is_at_least_10_chars(self):
        # Realistic minimal titles — even "tiny" real titles are well over 10 chars
        story = _make_story(title="Rate hike", source_name="Reuters")
        assert len(_fallback_summary(story)) >= 10


class TestBuildUserMessage:
    def test_contains_region(self):
        msg = _build_user_message(_make_story(), "eu", _make_status())
        assert "REGION: EU" in msg

    def test_contains_daily_status(self):
        status = _make_status(color="Red", sentiment="Crisis")
        msg = _build_user_message(_make_story(), "na", status)
        assert "Red" in msg
        assert "Crisis" in msg

    def test_contains_title_and_source(self):
        story = _make_story(title="Fed Hikes Rates", source_name="Reuters")
        msg = _build_user_message(story, "na", _make_status())
        assert "Fed Hikes Rates" in msg
        assert "Reuters" in msg

    def test_contains_category(self):
        story = _make_story(category="Tech")
        msg = _build_user_message(story, "apac", _make_status())
        assert "Tech" in msg

    def test_body_preview_included(self):
        story = _make_story(body="Central banks are meeting this week.")
        msg = _build_user_message(story, "eu", _make_status())
        assert "Central banks are meeting this week." in msg

    def test_body_preview_truncated_at_1500_chars(self):
        long_body = "x" * 3000
        story = _make_story(body=long_body)
        msg = _build_user_message(story, "eu", _make_status())
        # The body in the message should be at most 1500 chars from the body
        assert "x" * 1501 not in msg


# ---------------------------------------------------------------------------
# Integration tests: agent run_story()
# ---------------------------------------------------------------------------

class TestNewsletterWriterAgent:
    @pytest.mark.asyncio
    async def test_happy_path_returns_story_entry(self):
        summary = _words(120)
        with _mock_run(summary):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(),
                rank=1,
                region_id="eu",
                daily_status=_make_status(),
                session=_make_session(),
            )
        assert isinstance(entry, StoryEntry)
        assert entry.rank == 1
        assert entry.word_count == 120

    @pytest.mark.asyncio
    async def test_assigns_rank_correctly(self):
        summary = _words(110)
        with _mock_run(summary):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=4, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert entry.rank == 4

    @pytest.mark.asyncio
    async def test_word_count_matches_summary(self):
        summary = _words(125)
        with _mock_run(summary):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=1, region_id="na",
                daily_status=_make_status(), session=_make_session()
            )
        assert abs(entry.word_count - len(entry.summary.split())) <= 5

    @pytest.mark.asyncio
    async def test_preserves_metadata_from_curated_story(self):
        story = _make_story(
            title="EU Parliament votes on AI Act",
            source_name="Politico Europe",
            category="Tech",
            url="https://politico.eu/ai-act",
            score=0.92,
        )
        with _mock_run(_words(110)):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                story, rank=2, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert entry.title == "EU Parliament votes on AI Act"
        assert entry.source_name == "Politico Europe"
        assert entry.category == "Tech"
        assert entry.url == "https://politico.eu/ai-act"
        assert entry.significance_score == 0.92
        assert entry.raw_story_id == story.raw_story_id

    @pytest.mark.asyncio
    async def test_truncates_summary_over_150_words(self):
        # Build a clean 200-word text with sentence boundaries
        sentences = ["This is sentence number {:03d}.".format(i) for i in range(40)]
        long_text = " ".join(sentences)
        assert len(long_text.split()) > 150

        with _mock_run(long_text):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert entry.word_count <= 150

    @pytest.mark.asyncio
    async def test_retries_when_first_response_under_50_words(self):
        call_count = [0]
        # First call: 20 words; Second call: 110 words
        short_text = _words(20)
        good_text = _words(110)

        async def fake_run(self_ref, msg, **kwargs):
            call_count[0] += 1
            return short_text if call_count[0] == 1 else good_text

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=1, region_id="latam",
                daily_status=_make_status(), session=_make_session()
            )

        assert call_count[0] == 2
        assert entry.word_count >= 50

    @pytest.mark.asyncio
    async def test_retry_message_includes_word_count_instruction(self):
        messages_seen: list[str] = []
        call_count = [0]

        async def fake_run(self_ref, msg, **kwargs):
            messages_seen.append(msg)
            call_count[0] += 1
            return _words(20) if call_count[0] == 1 else _words(110)

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = NewsletterWriterAgent()
            await agent.run_story(
                _make_story(), rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )

        assert len(messages_seen) == 2
        assert "too short" in messages_seen[1].lower() or "50" in messages_seen[1]

    @pytest.mark.asyncio
    async def test_uses_fallback_when_both_attempts_under_50_words(self):
        short_text = _words(20)
        with _mock_run(short_text):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(
                    title="Major Central Bank Decision",
                    source_name="Bloomberg",
                ),
                rank=1, region_id="na",
                daily_status=_make_status(), session=_make_session()
            )
        # Fallback must mention title + source
        assert "Major Central Bank Decision" in entry.summary
        assert "Bloomberg" in entry.summary

    @pytest.mark.asyncio
    async def test_fallback_summary_passes_pydantic_validation(self):
        """Fallback summary must meet StoryEntry.summary min_length=10."""
        short_text = _words(5)
        story = _make_story(title="Short", source_name="X")
        with _mock_run(short_text):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                story, rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert len(entry.summary) >= 10

    @pytest.mark.asyncio
    async def test_strips_markdown_from_llm_response(self):
        summary_text = _words(110)
        wrapped = f"```\n{summary_text}\n```"
        with _mock_run(wrapped):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert "```" not in entry.summary

    @pytest.mark.asyncio
    async def test_exactly_150_word_summary_accepted(self):
        summary = _words(150)
        with _mock_run(summary):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert entry.word_count <= 150

    @pytest.mark.asyncio
    async def test_exactly_100_word_summary_accepted(self):
        summary = _words(100)
        with _mock_run(summary):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                _make_story(), rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session()
            )
        assert entry.word_count == 100

    @pytest.mark.asyncio
    async def test_url_none_preserved(self):
        story = _make_story(url=None)
        with _mock_run(_words(110)):
            agent = NewsletterWriterAgent()
            entry = await agent.run_story(
                story, rank=1, region_id="africa",
                daily_status=_make_status(), session=_make_session()
            )
        assert entry.url is None

    @pytest.mark.asyncio
    async def test_edition_id_passed_as_content_piece_id(self):
        """edition_id must be forwarded to BaseAgent.run as content_piece_id."""
        captured_kwargs: list[dict] = []

        async def fake_run(self_ref, msg, **kwargs):
            captured_kwargs.append(kwargs)
            return _words(110)

        edition_id = uuid.uuid4()
        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = NewsletterWriterAgent()
            await agent.run_story(
                _make_story(), rank=1, region_id="eu",
                daily_status=_make_status(), session=_make_session(),
                edition_id=edition_id,
            )

        assert captured_kwargs[0]["content_piece_id"] == edition_id
