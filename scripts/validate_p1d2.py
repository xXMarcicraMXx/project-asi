"""
P1-D2 validation script — MetisRSSCollector, category hinting, duplicate logging.

Checks:
  1. news_sources.yaml exists and has all 5 regions
  2. Each region has >= 1 regional feed + global feeds
  3. MetisRSSCollector._load_feeds() returns correct counts
  4. _hint_category() classifies known keywords correctly
  5. log_duplicate_urls() runs without error
  6. CLI 'collect' subcommand is importable and has --regions flag
  7. RawStory Pydantic model round-trips correctly

Run from repo root:
    python scripts/validate_p1d2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return condition


def main() -> int:
    failures = 0

    print("\nP1-D2 Validation - MetisRSSCollector\n" + "-" * 60)

    # ── 1. news_sources.yaml exists and has all 5 regions ──────────────────
    yaml_path = REPO_ROOT / "config" / "news_sources.yaml"
    ok = check("news_sources.yaml exists", yaml_path.exists(), str(yaml_path))
    if not ok:
        failures += 1

    try:
        import yaml
        with open(yaml_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        expected_regions = {"eu", "na", "latam", "apac", "africa"}
        present = set(cfg.get("regions", {}).keys())
        ok = check(
            "All 5 regions present",
            expected_regions == present,
            f"found: {sorted(present)}",
        )
        if not ok:
            failures += 1

        global_count = len(cfg.get("global", []))
        ok = check(
            "Global feeds >= 4",
            global_count >= 4,
            f"count={global_count}",
        )
        if not ok:
            failures += 1

    except Exception as exc:
        print(f"  [FAIL] Could not parse news_sources.yaml — {exc}")
        failures += 1

    # ── 2. _load_feeds() returns global + regional ──────────────────────────
    from data_sources.rss_source import MetisRSSCollector, _hint_category, log_duplicate_urls

    for region in ("eu", "na", "latam", "apac", "africa"):
        collector = MetisRSSCollector(region_id=region)
        feeds = collector._load_feeds()
        ok = check(
            f"_load_feeds({region}) returns > global count",
            len(feeds) > global_count,
            f"feeds={len(feeds)}, global={global_count}",
        )
        if not ok:
            failures += 1

    # ── 3. Category hinting ─────────────────────────────────────────────────
    cases = [
        ("President signs treaty with parliament", "Congress votes on legislation", "Politics"),
        ("Federal Reserve raises interest rate", "Inflation and stock market react", "Finance"),
        ("Artificial intelligence regulation debate", "Big tech faces scrutiny", "Tech"),
        ("Earthquake kills dozens", "Humanitarian crisis and flood", "Events"),
    ]
    for title, summary, expected in cases:
        result = _hint_category(title, summary)
        ok = check(
            f"_hint_category -> {expected}",
            result == expected,
            f"got '{result}'",
        )
        if not ok:
            failures += 1

    # ── 4. log_duplicate_urls() runs without error ──────────────────────────
    from orchestrator.brief_job_model import RawStory
    try:
        stories_by_region = {
            "eu": [RawStory(title="Shared", url="https://reuters.com/s1",
                            source_name="Reuters", body="Body A")],
            "na": [RawStory(title="Shared NA", url="https://reuters.com/s1",
                            source_name="Reuters", body="Body A")],
        }
        log_duplicate_urls(stories_by_region)
        check("log_duplicate_urls() runs without error", True)
    except Exception as exc:
        check("log_duplicate_urls() runs without error", False, str(exc))
        failures += 1

    # ── 5. RawStory Pydantic model round-trips ──────────────────────────────
    try:
        story = RawStory(
            title="Test Story",
            url="https://example.com/test",
            source_name="Test Source",
            body="This is a test body for the raw story entry.",
            category_hint="Politics",
        )
        assert story.title == "Test Story"
        assert story.category_hint == "Politics"
        check("RawStory round-trip (with category_hint)", True)
    except Exception as exc:
        check("RawStory round-trip (with category_hint)", False, str(exc))
        failures += 1

    try:
        story_no_hint = RawStory(
            title="Uncategorised",
            url=None,
            source_name="Feed",
            body="Body text here.",
        )
        assert story_no_hint.category_hint is None
        check("RawStory round-trip (category_hint=None)", True)
    except Exception as exc:
        check("RawStory round-trip (category_hint=None)", False, str(exc))
        failures += 1

    # ── 6. CLI collect subcommand present ──────────────────────────────────
    try:
        cli_src = (REPO_ROOT / "cli.py").read_text(encoding="utf-8")
        has_collect = 'sub.add_parser("collect"' in cli_src
        has_regions = '"--regions"' in cli_src and "cmd_collect" in cli_src
        ok = check(
            "CLI has 'collect' subcommand with --regions",
            has_collect and has_regions,
            f"collect={has_collect}, regions/cmd={has_regions}",
        )
        if not ok:
            failures += 1
    except Exception as exc:
        check("CLI has 'collect' subcommand with --regions", False, str(exc))
        failures += 1

    # ── Summary ─────────────────────────────────────────────────────────────
    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED — P1-D2 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
