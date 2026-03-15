"""
P1-D3 validation script — StatusAgent, CurationAgent, region curation_bias.

Checks:
  1. All 5 region YAML files load with curation_bias field present
  2. curation_bias is non-empty for each region
  3. RegionConfig Pydantic model has curation_bias attribute
  4. StatusAgent and CurationAgent import and instantiate without error
  5. StatusAgent._build_user_message() formats stories correctly
  6. StatusAgent._parse_response() accepts valid JSON, rejects bad JSON
  7. CurationAgent._build_user_message() includes region + bias
  8. CurationAgent._parse_response() accepts valid array, rejects non-array
  9. CurationAgent._build_curated_stories() maps story_index correctly
 10. BaseAgent.run() accepts content_piece_id=None (v2 compatibility)
 11. CLI 'curate' subcommand is present with --region flag

Run from repo root:
    python scripts/validate_p1d3.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Set dummy API key so BaseAgent.__init__ doesn't raise
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
    print("\nP1-D3 Validation - StatusAgent + CurationAgent\n" + "-" * 60)

    # ── 1-2. All 5 region YAMLs have curation_bias ─────────────────────────
    from config import load_region
    regions = ["eu", "na", "latam", "apac", "africa"]
    for region in regions:
        try:
            cfg = load_region(region)
            has_bias = bool(cfg.curation_bias and cfg.curation_bias.strip())
            ok = check(
                f"Region {region.upper()} has non-empty curation_bias",
                has_bias,
                f"bias_len={len(cfg.curation_bias or '')}",
            )
            if not ok:
                failures += 1
        except Exception as exc:
            check(f"Region {region.upper()} loads", False, str(exc))
            failures += 1

    # ── 3. RegionConfig has curation_bias attribute ─────────────────────────
    from config import RegionConfig
    ok = check(
        "RegionConfig has curation_bias field",
        hasattr(RegionConfig.model_fields, "curation_bias") or
        "curation_bias" in RegionConfig.model_fields,
    )
    if not ok:
        failures += 1

    # ── 4. Agents import and instantiate ────────────────────────────────────
    try:
        from agents.status_agent import StatusAgent, _DEFAULT_STATUS
        agent = StatusAgent()
        ok = check("StatusAgent instantiates", True, f"model={agent.MODEL}")
        ok2 = check(
            "_DEFAULT_STATUS is Amber/Cautious",
            _DEFAULT_STATUS.daily_color == "Amber" and _DEFAULT_STATUS.sentiment == "Cautious",
        )
        if not ok or not ok2:
            failures += 1
    except Exception as exc:
        check("StatusAgent instantiates", False, str(exc))
        failures += 1

    try:
        from agents.curation_agent import CurationAgent
        agent_c = CurationAgent()
        ok = check("CurationAgent instantiates", True, f"model={agent_c.MODEL}")
        if not ok:
            failures += 1
    except Exception as exc:
        check("CurationAgent instantiates", False, str(exc))
        failures += 1

    # ── 5. StatusAgent._build_user_message ─────────────────────────────────
    from orchestrator.brief_job_model import RawStory
    stories = [
        RawStory(title=f"Story {i}", url=f"https://ex.com/{i}",
                 source_name="Reuters", body=f"Body {i}")
        for i in range(1, 6)
    ]
    msg = StatusAgent._build_user_message(stories)
    ok = check(
        "StatusAgent user message contains story titles",
        "Story 1" in msg and "Story 5" in msg and "Reuters" in msg,
    )
    if not ok:
        failures += 1

    # ── 6. StatusAgent._parse_response ─────────────────────────────────────
    from orchestrator.brief_job_model import DailyStatus
    valid_payload = json.dumps({
        "daily_color": "Red",
        "sentiment": "Crisis",
        "mood_headline": "Active conflict escalates across multiple regions.",
    })
    try:
        status = StatusAgent._parse_response(valid_payload)
        ok = check(
            "StatusAgent._parse_response accepts valid JSON",
            status.daily_color == "Red" and status.sentiment == "Crisis",
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("StatusAgent._parse_response accepts valid JSON", False, str(exc))
        failures += 1

    ok = check(
        "StatusAgent._parse_response raises on bad JSON",
        _raises(StatusAgent._parse_response, "not json"),
    )
    if not ok:
        failures += 1

    # ── 7. CurationAgent._build_user_message ───────────────────────────────
    bias = "Focus on central bank decisions."
    msg = CurationAgent._build_user_message(stories, "eu", bias)
    ok = check(
        "CurationAgent user message has REGION + bias + stories",
        "REGION: EU" in msg and bias in msg and "1." in msg and "Story 1" in msg,
    )
    if not ok:
        failures += 1

    # ── 8. CurationAgent._parse_response ───────────────────────────────────
    valid_array = json.dumps([
        {"story_index": 1, "category": "Politics", "significance_score": 0.9}
    ])
    try:
        parsed = CurationAgent._parse_response(valid_array)
        ok = check(
            "CurationAgent._parse_response accepts valid array",
            isinstance(parsed, list) and len(parsed) == 1,
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("CurationAgent._parse_response accepts valid array", False, str(exc))
        failures += 1

    ok = check(
        "CurationAgent._parse_response raises on non-array JSON",
        _raises(CurationAgent._parse_response, '{"story_index": 1}'),
    )
    if not ok:
        failures += 1

    # ── 9. CurationAgent._build_curated_stories ─────────────────────────────
    from orchestrator.brief_job_model import CuratedStory
    selections = [
        {"story_index": 2, "category": "Finance", "significance_score": 0.85},
        {"story_index": 4, "category": "Tech", "significance_score": 0.70},
        {"story_index": 1, "category": "Politics", "significance_score": 0.90},
        {"story_index": 5, "category": "Events", "significance_score": 0.60},
        {"story_index": 3, "category": "Politics", "significance_score": 0.75},
    ]
    try:
        curated = CurationAgent._build_curated_stories(selections, stories, "eu")
        ok = check(
            "_build_curated_stories returns 5 CuratedStory objects",
            len(curated) == 5 and all(isinstance(s, CuratedStory) for s in curated),
        )
        ok2 = check(
            "_build_curated_stories assigns correct raw_story_id",
            curated[0].raw_story_id == stories[0].id,  # story_index=1, highest score
        )
        if not ok or not ok2:
            failures += 1
    except Exception as exc:
        check("_build_curated_stories round-trip", False, str(exc))
        failures += 1

    # ── 10. BaseAgent.run() accepts content_piece_id=None ──────────────────
    import inspect
    from agents.base_agent import BaseAgent
    sig = inspect.signature(BaseAgent.run)
    param = sig.parameters.get("content_piece_id")
    ok = check(
        "BaseAgent.run() content_piece_id is Optional (default None)",
        param is not None and param.default is None,
        f"default={param.default if param else 'MISSING'}",
    )
    if not ok:
        failures += 1

    # ── 11. CLI 'curate' subcommand ──────────────────────────────────────────
    cli_src = (REPO_ROOT / "cli.py").read_text(encoding="utf-8")
    ok = check(
        "CLI has 'curate' subcommand with --region flag",
        '"curate"' in cli_src and '"--region"' in cli_src and "cmd_curate" in cli_src,
    )
    if not ok:
        failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P1-D3 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


def _raises(fn, *args) -> bool:
    """Return True if fn(*args) raises any exception."""
    try:
        fn(*args)
        return False
    except Exception:
        return True


if __name__ == "__main__":
    sys.exit(main())
