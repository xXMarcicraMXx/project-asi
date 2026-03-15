"""
P2-D8 validation script -- HtmlPublisher.

Checks:
  1.  HtmlPublisher imports cleanly
  2.  HtmlPublisher instantiates
  3.  publish() writes current index.html
  4.  publish() writes archive date copy
  5.  publish() writes archive.html listing
  6.  No .tmp files remain after successful publish
  7.  current index.html is valid HTML (DOCTYPE + </html>)
  8.  archive copy is valid HTML
  9.  archive.html contains link to date directory
 10.  archive.html contains color dot for the edition
 11.  archive.html contains region display name
 12.  last-good/ backup created on second publish
 13.  All 5 grid types render and publish without error
 14.  PublishError raised on TemplateNotFound
 15.  DiskFullError is a subclass of PublishError
 16.  _atomic_write: .tmp not present after write (cleanup verified)
 17.  _extract_color_from_html reads Red correctly
 18.  _extract_color_from_html reads Green correctly
 19.  _extract_color_from_html returns Amber for missing file
 20.  archive.html regenerated with updated entries after 2nd publish
 21.  HtmlPublisher respects custom site_root
 22.  archive date entries are sorted most-recent-first
 23.  publish returns (current_path, archive_path) tuple
 24.  site/{region}/archive.html is valid HTML
 25.  CSS variables from LayoutConfig appear in rendered HTML

Run from repo root:
    python scripts/validate_p2d8.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from uuid import uuid4

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


def _make_edition(grid_type: str, region: str = "eu", daily_color: str = "Amber"):
    from orchestrator.brief_job_model import (
        DailyStatus,
        LayoutConfig,
        RegionalEdition,
        StoryEntry,
    )

    layout = LayoutConfig(
        layout_id=f"{region}-2026-03-15",
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
        "on leaked draft language circulating in Brussels policy circles."
    )
    words = len(summary.split())

    stories = [
        StoryEntry(
            rank=i + 1,
            category=["Politics", "Events", "Tech", "Finance"][i % 4],
            title=f"Test story {i + 1} for {region}",
            url=f"https://example.com/story/{i + 1}",
            source_name="Test Source",
            summary=summary,
            word_count=words,
            significance_score=round(0.9 - i * 0.1, 1),
            raw_story_id=uuid4(),
        )
        for i in range(5)
    ]

    return RegionalEdition(
        region=region,
        daily_status=DailyStatus(
            daily_color=daily_color,
            sentiment="Cautious",
            mood_headline="Markets cautious as EU debates AI regulation.",
        ),
        stories=stories,
        layout=layout,
    )


def main() -> int:
    failures = 0
    print("\nP2-D8 Validation - HtmlPublisher\n" + "-" * 60)

    # ── 1-2. Import and instantiate ───────────────────────────────────────────
    try:
        from publishers.html_publisher import (
            DiskFullError,
            HtmlPublisher,
            PublishError,
            _extract_color_from_html,
        )
        if not check("HtmlPublisher imports cleanly", True):
            failures += 1
    except Exception as exc:
        check("HtmlPublisher imports cleanly", False, str(exc))
        return 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        site_root = Path(tmp_dir) / "site"
        pub = HtmlPublisher(site_root=site_root)

        ok = check("HtmlPublisher instantiates", True)
        if not ok:
            failures += 1

        # ── 3-6. Basic publish ────────────────────────────────────────────
        edition = _make_edition("hero-top", region="eu", daily_color="Amber")
        run_date = date(2026, 3, 15)

        try:
            result = pub.publish(edition, run_date)
            current_path, archive_path = result

            ok = check("publish() writes current index.html",
                       current_path.is_file(),
                       str(current_path))
            if not ok:
                failures += 1

            ok = check("publish() writes archive date copy",
                       archive_path.is_file(),
                       str(archive_path))
            if not ok:
                failures += 1

            archive_listing = site_root / "eu" / "archive.html"
            ok = check("publish() writes archive.html listing",
                       archive_listing.is_file())
            if not ok:
                failures += 1

            tmp_files = list((site_root / "eu").rglob("*.tmp"))
            ok = check("no .tmp files remain after publish",
                       len(tmp_files) == 0,
                       f"found: {tmp_files}")
            if not ok:
                failures += 1

        except Exception as exc:
            check("publish() basic round-trip", False, str(exc))
            failures += 1
            return failures

        # ── 23. Returns tuple ─────────────────────────────────────────────
        ok = check("publish returns (current_path, archive_path) tuple",
                   isinstance(result, tuple) and len(result) == 2)
        if not ok:
            failures += 1

        # ── 7-8. Valid HTML ───────────────────────────────────────────────
        html = current_path.read_text(encoding="utf-8")
        ok = check("current index.html is valid HTML",
                   html.strip().startswith("<!DOCTYPE html>") and html.strip().endswith("</html>"))
        if not ok:
            failures += 1

        archive_html_content = archive_path.read_text(encoding="utf-8")
        ok = check("archive date copy is valid HTML",
                   archive_html_content.strip().startswith("<!DOCTYPE html>"))
        if not ok:
            failures += 1

        # ── 9-11. archive.html quality ────────────────────────────────────
        archive_listing_html = archive_listing.read_text(encoding="utf-8")

        ok = check("archive.html contains link to date directory",
                   "2026-03-15/index.html" in archive_listing_html)
        if not ok:
            failures += 1

        ok = check("archive.html contains color dot (Amber)",
                   "color-dot--Amber" in archive_listing_html)
        if not ok:
            failures += 1

        ok = check("archive.html contains region display name",
                   "EU" in archive_listing_html)
        if not ok:
            failures += 1

        ok = check("archive.html is valid HTML",
                   archive_listing_html.strip().startswith("<!DOCTYPE html>"))
        if not ok:
            failures += 1

        # ── 25. CSS variables in rendered HTML ────────────────────────────
        ok = check("CSS variables from LayoutConfig appear in rendered HTML",
                   "--color-primary: #2c3e50" in html)
        if not ok:
            failures += 1

        # ── 12. last-good backup on second publish ────────────────────────
        edition2 = _make_edition("hero-top", region="eu", daily_color="Red")
        run_date2 = date(2026, 3, 16)
        pub.publish(edition2, run_date2)

        backup_file = site_root / "eu" / "last-good" / "index.html"
        ok = check("last-good/ backup created on second publish",
                   backup_file.is_file())
        if not ok:
            failures += 1

        # ── 20. Archive listing updated after 2nd publish ─────────────────
        archive_listing_v2 = archive_listing.read_text(encoding="utf-8")
        ok = check("archive.html updated with 2nd edition entry",
                   "2026-03-16/index.html" in archive_listing_v2 and
                   "2026-03-15/index.html" in archive_listing_v2)
        if not ok:
            failures += 1

        # ── 22. Archive sorted most-recent-first ──────────────────────────
        pos_16 = archive_listing_v2.find("2026-03-16")
        pos_15 = archive_listing_v2.find("2026-03-15")
        ok = check("archive entries sorted most-recent-first",
                   pos_16 < pos_15,
                   f"pos_16={pos_16} pos_15={pos_15}")
        if not ok:
            failures += 1

        # ── 13. All 5 grid types publish ──────────────────────────────────
        grid_types = ["hero-left", "hero-top", "mosaic", "timeline", "editorial"]
        for gt in grid_types:
            try:
                ed = _make_edition(gt, region="na")
                pub.publish(ed, date(2026, 3, 15))
                ok = check(f"grid_type '{gt}' publishes without error", True)
            except Exception as exc:
                ok = check(f"grid_type '{gt}' publishes without error", False, str(exc))
            if not ok:
                failures += 1

        # ── 21. Custom site_root respected ────────────────────────────────
        with tempfile.TemporaryDirectory() as alt_dir:
            alt_root = Path(alt_dir) / "custom_site"
            alt_pub = HtmlPublisher(site_root=alt_root)
            ed = _make_edition("mosaic", region="eu")
            alt_current, _ = alt_pub.publish(ed, date(2026, 3, 15))
            ok = check("custom site_root respected",
                       str(alt_root) in str(alt_current))
            if not ok:
                failures += 1

        # ── 14. PublishError on TemplateNotFound ──────────────────────────
        raised = False
        try:
            from jinja2 import TemplateNotFound as JinjaNotFound
            from unittest.mock import patch

            bad_pub = HtmlPublisher(site_root=site_root)
            bad_edition = _make_edition("hero-top")
            with patch.object(
                bad_pub._env, "get_template",
                side_effect=JinjaNotFound("hero-top.html"),
            ):
                bad_pub.publish(bad_edition, date(2026, 3, 15))
        except PublishError:
            raised = True
        except Exception:
            raised = False

        ok = check("PublishError raised on TemplateNotFound", raised)
        if not ok:
            failures += 1

        # ── 15. DiskFullError is subclass of PublishError ─────────────────
        ok = check("DiskFullError is a subclass of PublishError",
                   issubclass(DiskFullError, PublishError))
        if not ok:
            failures += 1

    # ── 16. .tmp cleanup verified (independent test) ──────────────────────────
    with tempfile.TemporaryDirectory() as tmp_dir2:
        out = Path(tmp_dir2) / "test.html"
        pub2 = HtmlPublisher(site_root=Path(tmp_dir2))
        # Write directly using _atomic_write
        pub2._atomic_write(out, "<html><body>test</body></html>")
        tmp_file = out.with_suffix(".tmp")
        ok = check("_atomic_write: .tmp file cleaned up after write",
                   not tmp_file.exists())
        if not ok:
            failures += 1

        ok = check("_atomic_write: final file written",
                   out.is_file())
        if not ok:
            failures += 1

    # ── 17-19. _extract_color_from_html ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp_dir3:
        red_file = Path(tmp_dir3) / "red.html"
        green_file = Path(tmp_dir3) / "green.html"
        missing_file = Path(tmp_dir3) / "missing.html"

        red_file.write_text(
            '<!DOCTYPE html><html><body>'
            '<span class="color-dot color-dot--Red"></span>'
            '</body></html>',
            encoding="utf-8",
        )
        green_file.write_text(
            '<!DOCTYPE html><html><body>'
            '<span class="color-dot color-dot--Green"></span>'
            '</body></html>',
            encoding="utf-8",
        )

        ok = check("_extract_color_from_html reads Red",
                   _extract_color_from_html(red_file) == "Red")
        if not ok:
            failures += 1

        ok = check("_extract_color_from_html reads Green",
                   _extract_color_from_html(green_file) == "Green")
        if not ok:
            failures += 1

        ok = check("_extract_color_from_html returns Amber for missing file",
                   _extract_color_from_html(missing_file) == "Amber")
        if not ok:
            failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P2-D8 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
