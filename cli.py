"""
ASI CLI — entry point for triggering pipeline runs.

Usage:
    python cli.py run --topic "EU elections" --regions EU
    python cli.py run --topic "AI regulation" --regions EU NA
    python cli.py run --topic "global trade" --regions EU LATAM SEA NA
    python cli.py run --topic "custom topic" --regions EU --source-text "paste article text here"

Options:
    --topic         Topic to research and write about (required)
    --regions       One or more region IDs: EU LATAM SEA NA
    --content-type  Content type config to use (default: journal_article)
    --source-text   Skip RSS fetch and use this raw text as the source
    --output-dir    Directory to write article markdown files (optional)
    --log-plain     Use plain text logging instead of JSON (useful for local dev)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from config import load_settings
from db.session import AsyncSessionLocal
from orchestrator.job_model import JobPayload
from orchestrator.pipeline import query_cost_report, run_pipeline
from utils.log import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="ASI — autonomous multi-regional article engine",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run the article pipeline")
    run_cmd.add_argument("--topic", required=True, help="Topic to write about")
    run_cmd.add_argument(
        "--regions",
        dest="regions",
        nargs="+",
        required=True,
        metavar="REGION",
        help="One or more region IDs: EU LATAM SEA NA",
    )
    run_cmd.add_argument(
        "--content-type",
        default="journal_article",
        help="Content type config name (default: journal_article)",
    )
    run_cmd.add_argument(
        "--source-text",
        default=None,
        help="Raw source text to use instead of RSS fetch",
    )
    run_cmd.add_argument(
        "--output-dir",
        default=os.environ.get("ASI_OUTPUT_DIR"),
        help="Directory to write output markdown files",
    )
    run_cmd.add_argument(
        "--log-plain",
        action="store_true",
        help="Human-readable log format (default: JSON)",
    )
    run_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and sources, print plan — no agents run, no DB writes",
    )
    # ── collect subcommand (Metis v2) ─────────────────────────────────────────
    collect_cmd = sub.add_parser("collect", help="Test Metis news collection (no agents)")
    collect_cmd.add_argument(
        "--regions",
        nargs="+",
        default=["eu", "na", "latam", "apac", "africa"],
        metavar="REGION",
        help="Regions to collect for (default: all 5)",
    )
    collect_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print story counts per region — no DB writes",
    )
    collect_cmd.add_argument(
        "--log-plain",
        action="store_true",
        help="Human-readable log format",
    )

    # ── curate subcommand (Metis v2) ──────────────────────────────────────────
    curate_cmd = sub.add_parser(
        "curate",
        help="Run StatusAgent + CurationAgent for one region (Metis v2)",
    )
    curate_cmd.add_argument(
        "--region",
        required=True,
        metavar="REGION",
        help="Region to curate for: eu | na | latam | apac | africa",
    )
    curate_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Show config and story counts — skip API calls",
    )
    curate_cmd.add_argument(
        "--log-plain",
        action="store_true",
        help="Human-readable log format",
    )

    return parser


async def cmd_collect(args: argparse.Namespace) -> None:
    """Collect news for one or more regions and print story counts."""
    from data_sources.rss_source import MetisRSSCollector, log_duplicate_urls

    regions = [r.lower() for r in args.regions]
    print(f"\nMetis news collection — regions: {', '.join(regions)}")
    print("─" * 60)

    stories_by_region: dict = {}
    for region in regions:
        try:
            collector = MetisRSSCollector(region_id=region)
            stories = await collector.collect()
            stories_by_region[region] = stories
            print(f"  [{region:6}]  {len(stories):3} stories collected")
        except RuntimeError as e:
            print(f"  [{region:6}]  ERROR — {e}")

    if len(stories_by_region) > 1:
        log_duplicate_urls(stories_by_region)

    total = sum(len(s) for s in stories_by_region.values())
    print("─" * 60)
    print(f"  Total: {total} stories across {len(stories_by_region)} region(s)\n")


async def cmd_curate(args: argparse.Namespace) -> None:
    """
    Run news collection + StatusAgent + CurationAgent for one region.
    --dry-run shows config and story counts without making API calls.
    """
    from config import load_region
    from data_sources.rss_source import MetisRSSCollector

    region_id = args.region.lower()
    print(f"\nMetis curate — region: {region_id.upper()}")
    print("-" * 60)

    # Validate region config
    try:
        region_cfg = load_region(region_id)
        bias_preview = (region_cfg.curation_bias or "")[:80].replace("\n", " ")
        print(f"  [OK] Region config loaded — {region_cfg.display_name}")
        print(f"       curation_bias: {bias_preview}...")
    except FileNotFoundError as exc:
        print(f"  [ERR] {exc}")
        return

    # Collect stories
    print(f"\n  Collecting stories for {region_id.upper()}...")
    try:
        collector = MetisRSSCollector(region_id=region_id)
        stories = await collector.collect()
        print(f"  [OK] {len(stories)} stories collected")
    except RuntimeError as exc:
        print(f"  [ERR] Collection failed: {exc}")
        return

    if args.dry_run:
        print("\n  DRY RUN — skipping API calls")
        print(f"  StatusAgent would read {min(len(stories), 50)} headlines")
        print(f"  CurationAgent would select 5-8 from {len(stories)} stories")
        print("  No tokens used.\n")
        return

    # StatusAgent
    from agents.status_agent import StatusAgent
    print("\n  Running StatusAgent...")
    status_agent = StatusAgent()
    from unittest.mock import MagicMock, AsyncMock
    fake_session = AsyncMock()
    fake_session.add = MagicMock()
    fake_session.commit = AsyncMock()

    status = await status_agent.run_brief(stories, session=fake_session)
    print(f"  [OK] daily_color={status.daily_color}  sentiment={status.sentiment}")
    print(f"       mood: {status.mood_headline}")

    # CurationAgent
    from agents.curation_agent import CurationAgent
    print(f"\n  Running CurationAgent for {region_id.upper()}...")
    curation_agent = CurationAgent()
    curated = await curation_agent.run_region(
        stories,
        region_id=region_id,
        curation_bias=region_cfg.curation_bias,
        session=fake_session,
    )
    print(f"  [OK] {len(curated)} stories selected")
    print()
    for i, story in enumerate(curated, 1):
        print(
            f"  {i}. [{story.category}] score={story.significance_score:.2f}  "
            f"{story.title[:60]}"
        )
    print()


async def cmd_run(args: argparse.Namespace) -> None:
    from config import load_content_type, load_region

    payload = JobPayload(
        topic=args.topic,
        content_type=args.content_type,
        regions=[r.upper() for r in args.regions],
    )

    if args.dry_run:
        await _cmd_dry_run(payload, args)
        return

    logger.info(
        "cli_run",
        extra={
            "job_id": str(payload.id),
            "topic": payload.topic,
            "regions": payload.regions,
        },
    )

    async with AsyncSessionLocal() as session:
        drafts = await run_pipeline(
            payload,
            session=session,
            source_text=args.source_text,
        )

        # Cost report — query while session is still open
        report_rows = await query_cost_report(session, payload.id)

    # ── Print articles ──────────────────────────────────────────────────────
    for draft in drafts:
        print("\n" + "=" * 72)
        print(f"REGION: {draft.region_id}  |  {draft.word_count} words")
        print("=" * 72)
        print(draft.body)

        if args.output_dir:
            out_dir = Path(args.output_dir) / str(payload.id)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{draft.region_id.lower()}.md"
            out_file.write_text(draft.body, encoding="utf-8")
            print(f"\n  Saved to: {out_file}")

    # ── Cost report ─────────────────────────────────────────────────────────
    _print_cost_report(report_rows)

    print(f"\nJob complete. {len(drafts)} article(s) produced.")


async def _cmd_dry_run(payload: JobPayload, args: argparse.Namespace) -> None:
    """
    Validate configs and fetch sources — no agents are called, no DB writes.
    Prints a summary of what would run and exits.
    """
    from config import load_content_type, load_region
    from data_sources.rss_source import ManualSource, RSSSource

    print("\n" + "─" * 72)
    print(f"  DRY RUN — no agents will be called, no DB writes")
    print("─" * 72)
    print(f"  Job ID     : {payload.id}")
    print(f"  Topic      : {payload.topic}")
    print(f"  Content    : {payload.content_type}")
    print(f"  Regions    : {', '.join(payload.regions)}")

    # Validate content type
    try:
        ct = load_content_type(payload.content_type)
        print(f"\n  [OK] Content type '{ct.content_type}' — "
              f"{ct.output.min_words}–{ct.output.max_words} words")
    except Exception as exc:
        print(f"\n  [ERR] Content type load failed: {exc}")
        return

    # Validate region configs
    print()
    for region_id in payload.regions:
        try:
            r = load_region(region_id)
            print(f"  [OK] Region {r.region_id} — {r.display_name}")
        except Exception as exc:
            print(f"  [ERR] Region {region_id}: {exc}")

    # Fetch sources
    print(f"\n  Fetching sources for topic: '{payload.topic}'…")
    try:
        source = ManualSource(args.source_text) if args.source_text else RSSSource()
        articles = await source.fetch(payload.topic)
        print(f"  [OK] {len(articles)} source article(s) found")
        for i, a in enumerate(articles[:3], 1):
            print(f"       {i}. {a.title[:70]}")
        if len(articles) > 3:
            print(f"       … and {len(articles) - 3} more")
    except Exception as exc:
        print(f"  [ERR] Source fetch failed: {exc}")

    print("\n  Pipeline would run:")
    for region_id in payload.regions:
        print(f"    → {region_id}: research → write → edit (up to {4} iterations)")

    print("\n  Dry run complete. No tokens used, no DB writes.\n")


def _print_cost_report(rows: list[dict]) -> None:
    """Print a formatted token-usage and cost summary table."""
    if not rows:
        return

    # Aggregate by (region, agent_name)
    totals: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["region"], row["agent_name"])
        if key not in totals:
            totals[key] = {"in": 0, "out": 0, "cost": 0.0, "runs": 0, "ms": 0}
        totals[key]["in"]   += row["input_tokens"] or 0
        totals[key]["out"]  += row["output_tokens"] or 0
        totals[key]["cost"] += float(row["cost_usd"] or 0)
        totals[key]["runs"] += 1
        totals[key]["ms"]   += row["duration_ms"] or 0

    print("\n" + "─" * 72)
    print(f"{'COST REPORT':^72}")
    print("─" * 72)
    print(f"  {'Region':<8}  {'Agent':<18}  {'Runs':>4}  {'In Tok':>8}  {'Out Tok':>8}  {'USD':>8}")
    print("  " + "─" * 66)

    total_cost = 0.0
    total_in = 0
    total_out = 0
    for (region, agent), agg in sorted(totals.items()):
        print(
            f"  {region:<8}  {agent:<18}  {agg['runs']:>4}  "
            f"{agg['in']:>8,}  {agg['out']:>8,}  ${agg['cost']:>7.4f}"
        )
        total_cost += agg["cost"]
        total_in += agg["in"]
        total_out += agg["out"]

    print("  " + "─" * 66)
    print(
        f"  {'TOTAL':<8}  {'':18}  {'':4}  "
        f"{total_in:>8,}  {total_out:>8,}  ${total_cost:>7.4f}"
    )
    print("─" * 72)


def main() -> None:
    # Parse args first so --log-plain is available before setup
    parser = build_parser()
    args = parser.parse_args()

    # Initialise logging from settings
    settings = load_settings()
    setup_logging(
        level=settings.logging.level,
        json_format=not getattr(args, "log_plain", False),
    )

    if args.command == "run":
        asyncio.run(cmd_run(args))
    elif args.command == "collect":
        asyncio.run(cmd_collect(args))
    elif args.command == "curate":
        asyncio.run(cmd_curate(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
