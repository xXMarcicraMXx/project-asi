"""
P2-D6 tests — LayoutAgent.

All Anthropic API calls and DB interactions are mocked.
Tests cover: happy path, no-repeat enforcement, grid_type override (LRU),
safe-default fallback, history scoping, system prompt checks.
"""

from __future__ import annotations

import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.layout_agent import (
    ALL_GRID_TYPES,
    LayoutAgent,
    _build_user_message,
    _parse_response,
    _pick_least_recently_used,
)
from orchestrator.brief_job_model import (
    SAFE_DEFAULT_LAYOUT,
    DailyStatus,
    LayoutConfig,
    StoryEntry,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")


def _make_status(color="Amber", sentiment="Cautious") -> DailyStatus:
    return DailyStatus(
        daily_color=color,
        sentiment=sentiment,
        mood_headline="Markets steady amid uncertainty.",
    )


def _make_stories(n: int = 5) -> list[StoryEntry]:
    summary = " ".join(f"word{j}" for j in range(110)) + "."
    return [
        StoryEntry(
            rank=i + 1,
            category=["Politics", "Events", "Tech", "Finance", "Politics"][i % 5],
            title=f"Story {i + 1}",
            url=f"https://example.com/{i}",
            source_name="Reuters",
            summary=summary,
            word_count=110,
            significance_score=round(0.9 - i * 0.05, 2),
            raw_story_id=uuid.uuid4(),
        )
        for i in range(n)
    ]


def _valid_layout_json(grid_type: str = "hero-top") -> str:
    return json.dumps({
        "layout_id": "eu-2026-03-15",
        "grid_type": grid_type,
        "primary_color": "#2c3e50",
        "secondary_color": "#ecf0f1",
        "accent_color": "#3498db",
        "background_style": "light",
        "typography_family": "sans",
        "typography_weight": "regular",
        "section_order": ["Politics", "Events", "Tech", "Finance"],
        "dominant_category": "Politics",
        "visual_weight": "balanced",
        "mood_label": "Cautious",
        "color_rationale": "Muted blues for a cautious day.",
    })


def _make_session(grid_type_history: list[str] | None = None) -> MagicMock:
    """Return an async session mock whose execute() simulates history query."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    # Simulate the history SELECT: returns rows of (grid_type,)
    history = grid_type_history or []

    class _FakeResult:
        def all(self):
            return [(gt,) for gt in history]

    session.execute = AsyncMock(return_value=_FakeResult())
    return session


def _mock_base_run(text: str):
    """Patch BaseAgent.run to return a fixed response string."""
    return patch.object(
        __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
        "run",
        new_callable=AsyncMock,
        return_value=text,
    )


# ---------------------------------------------------------------------------
# Unit tests: _pick_least_recently_used
# ---------------------------------------------------------------------------

class TestPickLeastRecentlyUsed:
    def test_picks_unused_type_when_history_partial(self):
        history = ["hero-top", "mosaic"]
        result = _pick_least_recently_used(history)
        assert result not in history
        assert result in ALL_GRID_TYPES

    def test_picks_first_unused_in_all_grid_types_order(self):
        # Only "hero-top" used — should return "hero-left" (first in ALL_GRID_TYPES)
        history = ["hero-top"]
        result = _pick_least_recently_used(history)
        assert result == "hero-left"

    def test_picks_oldest_when_all_types_used(self):
        # All 5 used: history is most-recent-first
        history = ["mosaic", "timeline", "hero-top", "hero-left", "editorial"]
        # "editorial" is at index -1 = oldest = least recently used
        result = _pick_least_recently_used(history)
        assert result == "editorial"

    def test_empty_history_returns_first_grid_type(self):
        result = _pick_least_recently_used([])
        assert result == ALL_GRID_TYPES[0]

    def test_result_always_in_all_grid_types(self):
        for i in range(len(ALL_GRID_TYPES)):
            history = ALL_GRID_TYPES[:i]
            result = _pick_least_recently_used(history)
            assert result in ALL_GRID_TYPES


# ---------------------------------------------------------------------------
# Unit tests: _parse_response
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_parses_valid_json(self):
        config = _parse_response(_valid_layout_json("hero-top"))
        assert isinstance(config, LayoutConfig)
        assert config.grid_type == "hero-top"

    def test_strips_markdown_fences(self):
        wrapped = f"```json\n{_valid_layout_json('mosaic')}\n```"
        config = _parse_response(wrapped)
        assert config.grid_type == "mosaic"

    def test_raises_on_invalid_json(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _parse_response("not json at all")

    def test_raises_on_invalid_grid_type(self):
        data = json.loads(_valid_layout_json("hero-top"))
        data["grid_type"] = "banner"  # not in Literal
        with pytest.raises(Exception):
            _parse_response(json.dumps(data))

    def test_raises_on_invalid_color(self):
        data = json.loads(_valid_layout_json("hero-top"))
        data["primary_color"] = "red"  # named color — invalid
        with pytest.raises(Exception):
            _parse_response(json.dumps(data))

    def test_raises_on_rgb_color(self):
        data = json.loads(_valid_layout_json("hero-top"))
        data["primary_color"] = "rgb(44,62,80)"
        with pytest.raises(Exception):
            _parse_response(json.dumps(data))

    def test_raises_on_short_hex(self):
        data = json.loads(_valid_layout_json("hero-top"))
        data["primary_color"] = "#fff"
        with pytest.raises(Exception):
            _parse_response(json.dumps(data))

    def test_accepts_uppercase_hex(self):
        data = json.loads(_valid_layout_json("hero-top"))
        data["primary_color"] = "#2C3E50"
        config = _parse_response(json.dumps(data))
        assert config.primary_color == "#2C3E50"

    def test_raises_on_missing_section_category(self):
        data = json.loads(_valid_layout_json("hero-top"))
        data["section_order"] = ["Politics", "Events", "Tech"]  # missing Finance
        with pytest.raises(Exception):
            _parse_response(json.dumps(data))


# ---------------------------------------------------------------------------
# Unit tests: _build_user_message
# ---------------------------------------------------------------------------

class TestBuildUserMessage:
    def test_contains_layout_id(self):
        msg = _build_user_message(
            stories=_make_stories(),
            daily_status=_make_status(),
            region_id="eu",
            run_date=date(2026, 3, 15),
            layout_id="eu-2026-03-15",
            recent_grid_types=[],
        )
        assert "eu-2026-03-15" in msg

    def test_contains_daily_color_and_sentiment(self):
        status = _make_status(color="Red", sentiment="Crisis")
        msg = _build_user_message(
            stories=_make_stories(),
            daily_status=status,
            region_id="na",
            run_date=date(2026, 3, 15),
            layout_id="na-2026-03-15",
            recent_grid_types=[],
        )
        assert "Red" in msg
        assert "Crisis" in msg

    def test_recent_grid_types_in_user_message(self):
        history = ["mosaic", "timeline"]
        msg = _build_user_message(
            stories=_make_stories(),
            daily_status=_make_status(),
            region_id="eu",
            run_date=date(2026, 3, 15),
            layout_id="eu-2026-03-15",
            recent_grid_types=history,
        )
        assert "mosaic" in msg
        assert "timeline" in msg
        assert "DO NOT repeat" in msg

    def test_no_history_message_when_empty(self):
        msg = _build_user_message(
            stories=_make_stories(),
            daily_status=_make_status(),
            region_id="eu",
            run_date=date(2026, 3, 15),
            layout_id="eu-2026-03-15",
            recent_grid_types=[],
        )
        assert "none" in msg.lower() or "first edition" in msg.lower()

    def test_region_in_user_message(self):
        msg = _build_user_message(
            stories=_make_stories(),
            daily_status=_make_status(),
            region_id="apac",
            run_date=date(2026, 3, 15),
            layout_id="apac-2026-03-15",
            recent_grid_types=[],
        )
        assert "APAC" in msg


# ---------------------------------------------------------------------------
# Integration tests: LayoutAgent.run_layout()
# ---------------------------------------------------------------------------

class TestLayoutAgent:
    @pytest.mark.asyncio
    async def test_happy_path_returns_valid_layout_config(self):
        session = _make_session(grid_type_history=[])
        with _mock_base_run(_valid_layout_json("hero-left")):
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )
        assert isinstance(config, LayoutConfig)
        assert config.grid_type == "hero-left"

    @pytest.mark.asyncio
    async def test_output_validates_pydantic(self):
        session = _make_session(grid_type_history=[])
        with _mock_base_run(_valid_layout_json("mosaic")):
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="na",
                run_date=date(2026, 3, 15),
                session=session,
            )
        # All LayoutConfig fields should be valid
        assert config.grid_type in ALL_GRID_TYPES
        assert config.primary_color.startswith("#")
        assert len(config.primary_color) == 7

    @pytest.mark.asyncio
    async def test_grid_type_no_repeat_overrides_when_in_history(self):
        """If agent returns a grid_type already in 5-day window, pipeline overrides."""
        # History has "hero-top" — agent tries to return "hero-top" → override
        session = _make_session(grid_type_history=["hero-top", "mosaic", "timeline"])
        with _mock_base_run(_valid_layout_json("hero-top")):  # repeat!
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )
        assert config.grid_type not in ["hero-top", "mosaic", "timeline"]
        assert config.grid_type in ALL_GRID_TYPES

    @pytest.mark.asyncio
    async def test_pipeline_overrides_to_least_recently_used(self):
        """Override picks the LRU from ALL_GRID_TYPES not in history."""
        # History: hero-top, mosaic — unused: hero-left, timeline, editorial
        # _pick_least_recently_used(["hero-top", "mosaic"]) → "hero-left" (first unused)
        session = _make_session(grid_type_history=["hero-top", "mosaic"])
        with _mock_base_run(_valid_layout_json("hero-top")):  # repeat
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )
        assert config.grid_type == "hero-left"

    @pytest.mark.asyncio
    async def test_no_override_when_grid_type_not_in_history(self):
        """If agent returns a fresh grid_type, no override happens."""
        session = _make_session(grid_type_history=["hero-top", "mosaic"])
        with _mock_base_run(_valid_layout_json("timeline")):  # fresh!
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )
        assert config.grid_type == "timeline"

    @pytest.mark.asyncio
    async def test_uses_safe_default_after_two_invalid_outputs(self):
        """Two consecutive bad responses → SAFE_DEFAULT_LAYOUT."""
        session = _make_session(grid_type_history=[])
        with _mock_base_run("this is not json at all"):
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )
        # Should fall back to SAFE_DEFAULT_LAYOUT grid_type
        # (safe default is "hero-top" — but may be overridden by no-repeat logic)
        assert config.grid_type in ALL_GRID_TYPES

    @pytest.mark.asyncio
    async def test_safe_default_is_safe_default_layout(self):
        """Two bad outputs → result is safe default (modulo grid_type override)."""
        session = _make_session(grid_type_history=[])
        with _mock_base_run("bad json"):
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )
        # With empty history, SAFE_DEFAULT_LAYOUT.grid_type ("hero-top") is not in history
        # so no override happens — we get the safe default exactly
        assert config.primary_color == SAFE_DEFAULT_LAYOUT.primary_color
        assert config.background_style == SAFE_DEFAULT_LAYOUT.background_style

    @pytest.mark.asyncio
    async def test_retries_on_first_invalid_output(self):
        """First call returns bad JSON, second returns valid — should succeed."""
        call_count = [0]
        valid = _valid_layout_json("editorial")

        async def fake_run(self_ref, msg, **kwargs):
            call_count[0] += 1
            return "not json" if call_count[0] == 1 else valid

        session = _make_session(grid_type_history=[])
        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = LayoutAgent()
            config = await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )

        assert call_count[0] == 2
        assert config.grid_type == "editorial"

    @pytest.mark.asyncio
    async def test_history_query_scoped_to_correct_region(self):
        """The history SELECT must filter by region — EU history not contaminated by NA."""
        queries_seen: list = []

        async def fake_execute(stmt):
            queries_seen.append(stmt)

            class _R:
                def all(self):
                    return []
            return _R()

        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.execute = fake_execute

        with _mock_base_run(_valid_layout_json("mosaic")):
            agent = LayoutAgent()
            await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
            )

        # Verify at least one query was made (history SELECT)
        assert len(queries_seen) >= 1
        # The WHERE clause should reference "eu" — inspect compiled SQL
        first_query = queries_seen[0]
        compiled = str(first_query.compile(compile_kwargs={"literal_binds": True}))
        assert "eu" in compiled.lower()

    @pytest.mark.asyncio
    async def test_edition_id_passed_to_base_run(self):
        """edition_id must be forwarded as content_piece_id to BaseAgent.run."""
        captured: list[dict] = []
        edition_id = uuid.uuid4()

        async def fake_run(self_ref, msg, **kwargs):
            captured.append(kwargs)
            return _valid_layout_json("timeline")

        session = _make_session(grid_type_history=[])
        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", fake_run
        ):
            agent = LayoutAgent()
            await agent.run_layout(
                _make_stories(),
                daily_status=_make_status(),
                region_id="eu",
                run_date=date(2026, 3, 15),
                session=session,
                edition_id=edition_id,
            )

        assert captured[0]["content_piece_id"] == edition_id


# ---------------------------------------------------------------------------
# System prompt checks
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_system_prompt_enumerates_all_grid_types(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        for gt in ALL_GRID_TYPES:
            assert gt in sp, f"SYSTEM_PROMPT missing grid_type: {gt!r}"

    def test_system_prompt_enumerates_background_styles(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        for style in ["light", "dark", "warm-neutral", "cool-neutral"]:
            assert style in sp

    def test_system_prompt_enumerates_typography_families(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        for fam in ["serif", "sans", "mixed"]:
            assert fam in sp

    def test_system_prompt_enumerates_visual_weights(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        for w in ["dense", "balanced", "airy"]:
            assert w in sp

    def test_system_prompt_specifies_hex_color_format(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        assert "#RRGGBB" in sp or "6 digit" in sp.lower() or "6-digit" in sp.lower()

    def test_system_prompt_bans_named_colors(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        # Should explicitly forbid named colors
        assert "named color" in sp.lower() or "rgb()" in sp

    def test_system_prompt_contains_worked_example(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        # Worked example must show an actual JSON snippet
        assert '"grid_type"' in sp
        assert '"primary_color"' in sp

    def test_system_prompt_contains_adversarial_warning(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        assert "adversarial text" in sp.lower()

    def test_system_prompt_enumerates_all_categories(self):
        sp = LayoutAgent.SYSTEM_PROMPT
        for cat in ["Politics", "Events", "Tech", "Finance"]:
            assert cat in sp
