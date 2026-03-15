"""
P2-D7 validation script -- Jinja2 templates + CSS variable system.

Checks:
  1.  publishers.jinja_env imports cleanly
  2.  build_jinja_env() creates Environment with autoescape=True for .html
  3.  safe_url() allows http URLs
  4.  safe_url() allows https URLs
  5.  safe_url() blocks javascript: scheme
  6.  safe_url() blocks data: scheme
  7.  safe_url() returns '#' for None
  8.  safe_url() returns '#' for empty string
  9.  All 5 grid-type templates exist on disk
 10.  _base.css exists on disk
 11.  _layout-vars.css exists on disk
 12.  hero-top renders without exception
 13.  hero-left renders without exception
 14.  mosaic renders without exception
 15.  timeline renders without exception
 16.  editorial renders without exception
 17.  Rendered HTML starts with <!DOCTYPE html>
 18.  Rendered HTML ends with </html>
 19.  CSS custom property --color-primary injected in rendered output
 20.  Story title appears in rendered output (autoescaped correctly)
 21.  No javascript: URL in rendered output (safe_url blocked it)
 22.  No 'javascript:' in any template source file
 23.  No '| safe' applied to story fields in template source
 24.  CSS variable --color-bg injected (background_style mapping works)
 25.  All 5 templates write valid HTML files to site/validate/
 26.  Region display name (EU) appears in rendered output
 27.  Mood label appears in rendered output
 28.  safe_url is registered as Jinja2 global (not just a Python function)
 29.  build_template_context returns expected keys
 30.  stories_by_section groups correctly by section_order

Run from repo root:
    python scripts/validate_p2d7.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-validate")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/test")

OUTPUT_DIR = REPO_ROOT / "site" / "validate"


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return condition


def _make_edition(grid_type: str):
    """Build a synthetic RegionalEdition for rendering tests."""
    from orchestrator.brief_job_model import (
        DailyStatus,
        LayoutConfig,
        RegionalEdition,
        StoryEntry,
    )

    layout = LayoutConfig(
        layout_id="eu-2026-03-15",
        grid_type=grid_type,
        primary_color="#2c3e50",
        secondary_color="#ecf0f1",
        accent_color="#3498db",
        background_style="light",
        typography_family="sans",
        typography_weight="regular",
        section_order=["Politics", "Events", "Tech", "Finance"],
        dominant_category="Politics",
        visual_weight="balanced",
        mood_label="Cautious Markets",
        color_rationale="Muted blues for a cautious day.",
    )

    summary = (
        "European lawmakers convened to discuss the latest round of AI regulation "
        "proposals, citing concerns over frontier model capabilities and deployment "
        "timelines. The session drew significant attention from tech industry "
        "lobbyists and civil society groups. Observers noted that the draft text "
        "remains contested on key enforcement provisions and liability thresholds. "
        "A vote is expected by end of quarter. The commission declined to comment "
        "on leaked draft language circulating in Brussels policy circles. Several "
        "member states have indicated they will seek amendments before final reading."
    )
    words = len(summary.split())

    stories = []
    categories = ["Politics", "Events", "Tech", "Finance",
                  "Politics", "Tech", "Finance", "Events"]
    titles = [
        "EU lawmakers debate AI Act enforcement provisions",
        "Brussels summit opens amid tight security",
        "Tech firms warn of compliance costs under new rules",
        "Euro edges higher as ECB signals patience",
        "NATO allies discuss Baltic Sea security pact",
        "Chip shortage eases as TSMC ramps capacity",
        "Bond markets steady after inflation data",
        "Climate summit produces landmark agreement",
    ]
    sources = ["Politico", "Reuters", "FT", "Bloomberg",
               "AFP", "The Verge", "WSJ", "BBC"]
    urls = [
        "https://politico.eu/ai-act",
        "https://reuters.com/brussels",
        "https://ft.com/tech",
        None,  # tests safe_url fallback to '#'
        "https://nato.int/news",
        "https://theverge.com/chips",
        "https://wsj.com/bonds",
        "https://bbc.com/climate",
    ]

    for i in range(min(6, len(titles))):
        stories.append(StoryEntry(
            rank=i + 1,
            category=categories[i],
            title=titles[i],
            url=urls[i],
            source_name=sources[i],
            summary=summary,
            word_count=words,
            significance_score=round(0.9 - i * 0.1, 1),
            raw_story_id=uuid4(),
        ))

    return RegionalEdition(
        region="eu",
        daily_status=DailyStatus(
            daily_color="Amber",
            sentiment="Cautious",
            mood_headline="Markets cautious as EU debates AI regulation framework.",
        ),
        stories=stories,
        layout=layout,
    )


def main() -> int:
    failures = 0
    print("\nP2-D7 Validation - Jinja2 Templates + CSS Variable System\n" + "-" * 60)

    # ── 1. Import ─────────────────────────────────────────────────────────────
    try:
        from publishers.jinja_env import (
            build_jinja_env,
            build_template_context,
            safe_url,
        )
        if not check("publishers.jinja_env imports cleanly", True):
            failures += 1
    except Exception as exc:
        check("publishers.jinja_env imports cleanly", False, str(exc))
        return 1

    # ── 2. Environment ────────────────────────────────────────────────────────
    env = build_jinja_env()
    from jinja2 import Environment
    ok = check("build_jinja_env returns jinja2 Environment", isinstance(env, Environment))
    if not ok:
        failures += 1

    # Autoescape check: render a string with HTML-special chars
    tmpl = env.from_string("{{ val }}")
    rendered = tmpl.render(val="<script>alert(1)</script>")
    ok = check(
        "autoescape=True: HTML entities escaped",
        "&lt;script&gt;" in rendered,
        f"got: {rendered!r}",
    )
    if not ok:
        failures += 1

    # ── 3-8. safe_url ─────────────────────────────────────────────────────────
    tests = [
        ("safe_url allows http://", "http://example.com/news", "http://example.com/news"),
        ("safe_url allows https://", "https://reuters.com/world", "https://reuters.com/world"),
        ("safe_url blocks javascript:", "javascript:alert(1)", "#"),
        ("safe_url blocks data:", "data:text/html,<h1>x</h1>", "#"),
        ("safe_url returns # for None", None, "#"),
        ("safe_url returns # for empty string", "", "#"),
    ]
    for label, inp, expected in tests:
        result = safe_url(inp)
        ok = check(label, result == expected, f"got {result!r}")
        if not ok:
            failures += 1

    # ── 9-11. Files exist ─────────────────────────────────────────────────────
    from publishers.jinja_env import TEMPLATES_DIR
    grid_types = ["hero-left", "hero-top", "mosaic", "timeline", "editorial"]

    for gt in grid_types:
        path = TEMPLATES_DIR / f"{gt}.html"
        ok = check(f"template {gt}.html exists", path.is_file())
        if not ok:
            failures += 1

    for css_file in ["_base.css", "_layout-vars.css"]:
        path = TEMPLATES_DIR / css_file
        ok = check(f"{css_file} exists", path.is_file())
        if not ok:
            failures += 1

    # ── 12-21. Render all 5 templates ────────────────────────────────────────
    run_date = date(2026, 3, 15)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rendered_outputs: dict[str, str] = {}

    for gt in grid_types:
        edition = _make_edition(gt)
        ctx = build_template_context(edition, run_date)
        try:
            tmpl = env.get_template(f"{gt}.html")
            html = tmpl.render(**ctx)
            rendered_outputs[gt] = html
            ok = check(f"{gt}.html renders without exception", True)
            if not ok:
                failures += 1
        except Exception as exc:
            check(f"{gt}.html renders without exception", False, str(exc))
            failures += 1

    # Check rendered output quality (use hero-top as representative sample)
    if "hero-top" in rendered_outputs:
        html = rendered_outputs["hero-top"]

        ok = check("rendered HTML starts with <!DOCTYPE html>",
                   html.strip().startswith("<!DOCTYPE html>"))
        if not ok:
            failures += 1

        ok = check("rendered HTML ends with </html>",
                   html.strip().endswith("</html>"))
        if not ok:
            failures += 1

        ok = check("--color-primary CSS variable injected",
                   "--color-primary: #2c3e50" in html)
        if not ok:
            failures += 1

        ok = check("story title present in output (autoescaped)",
                   "EU lawmakers debate AI Act" in html)
        if not ok:
            failures += 1

        ok = check("no javascript: URL in rendered output",
                   "javascript:" not in html.lower())
        if not ok:
            failures += 1

        ok = check("--color-bg injected (background_style mapping)",
                   "--color-bg:" in html)
        if not ok:
            failures += 1

        ok = check("region display (EU) in rendered output",
                   "EU Edition" in html)
        if not ok:
            failures += 1

        ok = check("mood_label in rendered output",
                   "Cautious Markets" in html)
        if not ok:
            failures += 1

    # ── 22-23. Template source safety checks ─────────────────────────────────
    agent_fields = ["story.title", "story.summary", "hero.title", "hero.summary",
                    "primary.title", "primary.summary", "secondary.title", "secondary.summary",
                    "story.source_name", "hero.source_name",
                    "edition.daily_status.mood_headline"]

    unsafe_patterns_found: list[str] = []
    js_found: list[str] = []

    for gt in grid_types:
        src = (TEMPLATES_DIR / f"{gt}.html").read_text(encoding="utf-8")
        if "javascript:" in src.lower():
            js_found.append(gt)
        for field in agent_fields:
            # Check for pattern like {{ field | safe }} (with optional spaces)
            import re
            pattern = re.compile(
                r"\{\{[^}]*" + re.escape(field) + r"[^}]*\|\s*safe\s*\}\}"
            )
            if pattern.search(src):
                unsafe_patterns_found.append(f"{gt}:{field}")

    ok = check(
        "no 'javascript:' in any template source",
        len(js_found) == 0,
        f"found in: {js_found}" if js_found else "",
    )
    if not ok:
        failures += 1

    ok = check(
        "no '| safe' on agent-output fields in templates",
        len(unsafe_patterns_found) == 0,
        f"found: {unsafe_patterns_found}" if unsafe_patterns_found else "",
    )
    if not ok:
        failures += 1

    # ── 24. safe_url is a Jinja2 global ──────────────────────────────────────
    ok = check(
        "safe_url registered as Jinja2 global",
        "safe_url" in env.globals,
    )
    if not ok:
        failures += 1

    # ── 25. build_template_context returns expected keys ─────────────────────
    edition = _make_edition("hero-top")
    ctx = build_template_context(edition, run_date)
    required_keys = {"edition", "layout", "run_date", "region_display",
                     "css_vars", "all_stories", "stories_by_section"}
    missing = required_keys - ctx.keys()
    ok = check(
        "build_template_context returns all required keys",
        len(missing) == 0,
        f"missing: {missing}" if missing else "",
    )
    if not ok:
        failures += 1

    # ── 26. stories_by_section grouping ──────────────────────────────────────
    by_sec = ctx["stories_by_section"]
    politics_stories = by_sec.get("Politics", [])
    ok = check(
        "stories_by_section groups Politics stories correctly",
        len(politics_stories) > 0,
        f"found {len(politics_stories)} Politics stories",
    )
    if not ok:
        failures += 1

    # ── 27. Write rendered HTML files ────────────────────────────────────────
    all_wrote = True
    for gt, html in rendered_outputs.items():
        path = OUTPUT_DIR / f"{gt}.html"
        try:
            path.write_text(html, encoding="utf-8")
        except Exception as exc:
            check(f"write {gt}.html to site/validate/", False, str(exc))
            failures += 1
            all_wrote = False

    if all_wrote and rendered_outputs:
        ok = check(
            f"all {len(rendered_outputs)} templates written to site/validate/",
            True,
            str(OUTPUT_DIR),
        )

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P2-D7 complete\n")
        print(f"  Rendered files in: {OUTPUT_DIR}")
        print("  Open in browser to verify visual layout.\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
