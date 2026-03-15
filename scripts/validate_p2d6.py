"""
P2-D6 validation script — LayoutAgent.

Checks:
  1.  LayoutAgent imports and instantiates
  2.  Model is Haiku
  3.  SYSTEM_PROMPT enumerates all 5 grid_type values
  4.  SYSTEM_PROMPT enumerates all background_style values
  5.  SYSTEM_PROMPT enumerates all typography_family values
  6.  SYSTEM_PROMPT contains CSS color format (#RRGGBB)
  7.  SYSTEM_PROMPT bans named colors and rgb()
  8.  SYSTEM_PROMPT contains a worked JSON example
  9.  SYSTEM_PROMPT contains adversarial text warning
 10.  _parse_response() accepts valid JSON → LayoutConfig
 11.  _parse_response() rejects invalid grid_type
 12.  _parse_response() rejects named CSS color
 13.  _parse_response() strips markdown fences
 14.  _pick_least_recently_used() with empty history → first grid_type
 15.  _pick_least_recently_used() with partial history → picks unused
 16.  _pick_least_recently_used() with full history → picks oldest
 17.  _build_user_message() includes layout_id, region, history
 18.  run_layout() mock round-trip → LayoutConfig returned (no DB)
 19.  No-repeat override: repeat grid_type in history → LRU selected
 20.  CLI has 'layout' subcommand (or brief pipeline integration)

Run from repo root:
    python scripts/validate_p2d6.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-validate")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return condition


def main() -> int:
    failures = 0
    print("\nP2-D6 Validation - LayoutAgent\n" + "-" * 60)

    # ── 1-2. Import and model ────────────────────────────────────────────────
    try:
        from agents.layout_agent import (
            ALL_GRID_TYPES,
            LayoutAgent,
            _build_user_message,
            _parse_response,
            _pick_least_recently_used,
        )
        agent = LayoutAgent()
        ok = check("LayoutAgent instantiates", True, f"model={agent.MODEL}")
        ok2 = check("Model is Haiku", "haiku" in agent.MODEL.lower(), agent.MODEL)
        if not ok or not ok2:
            failures += 1
    except Exception as exc:
        check("LayoutAgent instantiates", False, str(exc))
        return 1

    # ── 3-9. SYSTEM_PROMPT checks ────────────────────────────────────────────
    sp = LayoutAgent.SYSTEM_PROMPT

    for gt in ALL_GRID_TYPES:
        ok = check(f"SYSTEM_PROMPT contains grid_type '{gt}'", gt in sp)
        if not ok:
            failures += 1

    for style in ["light", "dark", "warm-neutral", "cool-neutral"]:
        ok = check(f"SYSTEM_PROMPT contains background_style '{style}'", style in sp)
        if not ok:
            failures += 1

    for fam in ["serif", "sans", "mixed"]:
        ok = check(f"SYSTEM_PROMPT contains typography_family '{fam}'", fam in sp)
        if not ok:
            failures += 1

    ok = check(
        "SYSTEM_PROMPT specifies #RRGGBB color format",
        "#RRGGBB" in sp or "6 digit" in sp.lower() or "6-digit" in sp.lower(),
    )
    if not ok:
        failures += 1

    ok = check(
        "SYSTEM_PROMPT bans named colors",
        "named color" in sp.lower() or "no named" in sp.lower(),
    )
    if not ok:
        failures += 1

    ok = check(
        "SYSTEM_PROMPT bans rgb()",
        "rgb()" in sp or "no rgb" in sp.lower(),
    )
    if not ok:
        failures += 1

    ok = check(
        "SYSTEM_PROMPT has worked JSON example",
        '"grid_type"' in sp and '"primary_color"' in sp,
    )
    if not ok:
        failures += 1

    ok = check(
        "SYSTEM_PROMPT contains adversarial text warning",
        "adversarial text" in sp.lower(),
    )
    if not ok:
        failures += 1

    # ── 10-13. _parse_response ────────────────────────────────────────────────
    valid_json = json.dumps({
        "layout_id": "eu-2026-03-15",
        "grid_type": "hero-top",
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

    try:
        from orchestrator.brief_job_model import LayoutConfig
        cfg = _parse_response(valid_json)
        ok = check(
            "_parse_response accepts valid JSON",
            isinstance(cfg, LayoutConfig) and cfg.grid_type == "hero-top",
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("_parse_response accepts valid JSON", False, str(exc))
        failures += 1

    bad_grid = json.loads(valid_json)
    bad_grid["grid_type"] = "banner"
    ok = check(
        "_parse_response rejects invalid grid_type",
        _raises(_parse_response, json.dumps(bad_grid)),
    )
    if not ok:
        failures += 1

    bad_color = json.loads(valid_json)
    bad_color["primary_color"] = "red"
    ok = check(
        "_parse_response rejects named color",
        _raises(_parse_response, json.dumps(bad_color)),
    )
    if not ok:
        failures += 1

    wrapped = f"```json\n{valid_json}\n```"
    try:
        cfg2 = _parse_response(wrapped)
        ok = check("_parse_response strips markdown fences", isinstance(cfg2, LayoutConfig))
        if not ok:
            failures += 1
    except Exception as exc:
        check("_parse_response strips markdown fences", False, str(exc))
        failures += 1

    # ── 14-16. _pick_least_recently_used ────────────────────────────────────
    result = _pick_least_recently_used([])
    ok = check(
        "_pick_least_recently_used([]) returns first grid_type",
        result == ALL_GRID_TYPES[0],
        f"got: {result!r}",
    )
    if not ok:
        failures += 1

    result = _pick_least_recently_used(["hero-top", "mosaic"])
    ok = check(
        "_pick_least_recently_used(partial) picks unused",
        result not in ["hero-top", "mosaic"] and result in ALL_GRID_TYPES,
        f"got: {result!r}",
    )
    if not ok:
        failures += 1

    full_history = ["mosaic", "timeline", "hero-top", "hero-left", "editorial"]
    result = _pick_least_recently_used(full_history)
    ok = check(
        "_pick_least_recently_used(full) picks oldest = editorial",
        result == "editorial",
        f"got: {result!r}",
    )
    if not ok:
        failures += 1

    # ── 17. _build_user_message ───────────────────────────────────────────────
    from orchestrator.brief_job_model import DailyStatus, StoryEntry
    status = DailyStatus(daily_color="Amber", sentiment="Cautious",
                         mood_headline="Markets steady.")
    summary = " ".join(f"w{i}" for i in range(110)) + "."
    stories = [
        StoryEntry(
            rank=1, category="Politics", title="EU votes on AI Act",
            url="https://politico.eu/ai", source_name="Politico",
            summary=summary, word_count=110,
            significance_score=0.9, raw_story_id=uuid.uuid4(),
        )
    ]
    msg = _build_user_message(
        stories=stories, daily_status=status,
        region_id="eu", run_date=date(2026, 3, 15),
        layout_id="eu-2026-03-15", recent_grid_types=["hero-top", "mosaic"],
    )
    checks_17 = {
        "layout_id in message": "eu-2026-03-15" in msg,
        "REGION: EU in message": "EU" in msg,
        "hero-top in history list": "hero-top" in msg,
        "mosaic in history list": "mosaic" in msg,
        "Amber in message": "Amber" in msg,
    }
    for label, cond in checks_17.items():
        ok = check(f"_build_user_message: {label}", cond)
        if not ok:
            failures += 1

    # ── 18. run_layout() mock round-trip ─────────────────────────────────────
    async def _run_mock():
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", AsyncMock(return_value=valid_json)
        ):
            a = LayoutAgent()
            return await a.run_layout(
                stories, daily_status=status,
                region_id="eu", run_date=date(2026, 3, 15),
                session=session, edition_id=uuid.uuid4(),
            )

    try:
        from orchestrator.brief_job_model import LayoutConfig
        result = asyncio.run(_run_mock())
        ok = check(
            "run_layout() mock round-trip returns LayoutConfig",
            isinstance(result, LayoutConfig),
            f"grid_type={result.grid_type}",
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("run_layout() mock round-trip", False, str(exc))
        failures += 1

    # ── 19. No-repeat override ────────────────────────────────────────────────
    async def _run_override():
        # History has hero-top → agent returns hero-top → override fires
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()

        class _R:
            def all(self):
                return [("hero-top",), ("mosaic",)]

        session.execute = AsyncMock(return_value=_R())

        with patch.object(
            __import__("agents.base_agent", fromlist=["BaseAgent"]).BaseAgent,
            "run", AsyncMock(return_value=valid_json)  # valid_json has hero-top
        ):
            a = LayoutAgent()
            return await a.run_layout(
                stories, daily_status=status,
                region_id="eu", run_date=date(2026, 3, 15),
                session=session,
            )

    try:
        result = asyncio.run(_run_override())
        ok = check(
            "No-repeat: repeat grid_type overridden to LRU",
            result.grid_type not in ["hero-top", "mosaic"],
            f"got: {result.grid_type!r}",
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("No-repeat override", False, str(exc))
        failures += 1

    # ── 20. CLI check ─────────────────────────────────────────────────────────
    cli_src = (REPO_ROOT / "cli.py").read_text(encoding="utf-8")
    has_layout_cmd = '"layout"' in cli_src and "cmd_layout" in cli_src
    has_brief_cmd = '"brief"' in cli_src and "LayoutAgent" in (REPO_ROOT / "orchestrator" / "brief_pipeline.py").read_text()
    ok = check(
        "LayoutAgent integrated in pipeline or CLI layout command",
        has_layout_cmd or has_brief_cmd,
    )
    if not ok:
        failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P2-D6 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


def _raises(fn, *args) -> bool:
    try:
        fn(*args)
        return False
    except Exception:
        return True


if __name__ == "__main__":
    sys.exit(main())
