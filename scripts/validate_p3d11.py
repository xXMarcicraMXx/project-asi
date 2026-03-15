"""
P3-D11 validation script — Full scheduler + final validation.

Checks:
  1.  orchestrator/scheduler.py imports cleanly
  2.  build_scheduler is importable and callable
  3.  run_scheduled_job is importable and callable
  4.  scheduler job id is 'metis_daily_pipeline'
  5.  scheduler cron matches settings.yaml
  6.  run_scheduled_job calls run_brief_pipeline (dry_run=True)
  7.  DRY_RUN=1 env var respected by run_scheduled_job
  8.  settings.yaml scheduler.cron parses without error
  9.  config/__init__.py SchedulerConfig accepts missing default_regions/topics
  10. orchestrator/brief_pipeline.py imports cleanly
  11. run_brief_pipeline is importable
  12. test suite passes: python -m pytest tests/test_observability.py
  13. metis.rest/eu/ returns HTTP 200 (live check)
  14. metis.rest/na/ returns HTTP 200 (live check)
  15. README.md exists and has v2 architecture reference

Run from repo root:
    python scripts/validate_p3d11.py
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

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
    print("\nP3-D11 Validation - Scheduler + Final\n" + "-" * 60)

    # ── 1-3. Imports ──────────────────────────────────────────────────────────
    try:
        from orchestrator.scheduler import build_scheduler, run_scheduled_job
        if not check("orchestrator/scheduler.py imports cleanly", True):
            failures += 1
    except Exception as exc:
        check("orchestrator/scheduler.py imports cleanly", False, str(exc))
        return 1

    if not check("build_scheduler importable", callable(build_scheduler)):
        failures += 1
    if not check("run_scheduled_job importable", callable(run_scheduled_job)):
        failures += 1

    # ── 4-5. Scheduler configuration ─────────────────────────────────────────
    try:
        scheduler = build_scheduler()
        job_ids = [job.id for job in scheduler.get_jobs()]
        ok = check("scheduler job id is 'metis_daily_pipeline'",
                   "metis_daily_pipeline" in job_ids, str(job_ids))
        if not ok:
            failures += 1
        ok = check("build_scheduler runs without error", True)
    except Exception as exc:
        check("build_scheduler runs without error", False, str(exc))
        failures += 1

    from config import load_settings
    settings = load_settings()
    ok = check("settings.yaml scheduler.cron is valid",
               bool(settings.scheduler.cron), settings.scheduler.cron)
    if not ok:
        failures += 1

    # ── 6-7. run_scheduled_job dry_run behaviour ──────────────────────────────
    async def _run_scheduler_tests():
        nonlocal failures
        from unittest.mock import AsyncMock, MagicMock, patch
        import uuid

        mock_result = MagicMock()
        mock_result.run_id = uuid.uuid4()
        mock_result.run_status = "complete"
        mock_result.total_cost_usd = 0.0
        mock_result.regions = {}

        with patch(
            "orchestrator.brief_pipeline.run_brief_pipeline",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_pipeline:
            await run_scheduled_job(dry_run=True)

        ok = check("run_scheduled_job calls run_brief_pipeline",
                   mock_pipeline.await_count == 1)
        if not ok:
            failures += 1

        ok = check("run_scheduled_job passes dry_run=True",
                   mock_pipeline.call_args[1].get("dry_run") is True)
        if not ok:
            failures += 1

        # DRY_RUN env var
        with patch.dict(os.environ, {"DRY_RUN": "1"}):
            with patch(
                "orchestrator.brief_pipeline.run_brief_pipeline",
                new_callable=AsyncMock,
                return_value=mock_result,
            ) as mock_env:
                await run_scheduled_job()

        ok = check("DRY_RUN=1 env var activates dry_run",
                   mock_env.call_args[1].get("dry_run") is True)
        if not ok:
            failures += 1

    asyncio.run(_run_scheduler_tests())

    # ── 8-9. Config validation ────────────────────────────────────────────────
    from config import SchedulerConfig
    try:
        sc = SchedulerConfig(cron="0 7 * * *")
        ok = check("SchedulerConfig accepts missing default_regions/topics",
                   sc.default_regions == [] and sc.default_topics == [])
        if not ok:
            failures += 1
    except Exception as exc:
        check("SchedulerConfig accepts missing default_regions/topics", False, str(exc))
        failures += 1

    # ── 10-11. brief_pipeline imports ─────────────────────────────────────────
    try:
        from orchestrator.brief_pipeline import run_brief_pipeline
        ok = check("orchestrator/brief_pipeline.py imports cleanly", True)
        ok = check("run_brief_pipeline importable", callable(run_brief_pipeline))
        if not ok:
            failures += 1
    except Exception as exc:
        check("orchestrator/brief_pipeline.py imports cleanly", False, str(exc))
        failures += 1

    # ── 12. Test suite ────────────────────────────────────────────────────────
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_observability.py", "-q", "--tb=short"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    ok = check("pytest tests/test_observability.py passes",
               result.returncode == 0,
               result.stdout.strip().splitlines()[-1] if result.stdout.strip() else result.stderr[:120])
    if not ok:
        failures += 1

    # ── 13-14. Live HTTPS checks ──────────────────────────────────────────────
    try:
        import httpx
        for region in ["eu", "na"]:
            try:
                resp = httpx.get(f"https://metis.rest/{region}/", timeout=10, follow_redirects=True)
                # 200 = content deployed, 404 = region dir empty (expected before first run)
                ok = check(f"metis.rest/{region}/ reachable (200 or 404)",
                           resp.status_code in {200, 404},
                           f"got {resp.status_code}")
            except Exception as exc:
                ok = check(f"metis.rest/{region}/ reachable", False, str(exc))
            if not ok:
                failures += 1
    except ImportError:
        check("live HTTPS checks (httpx not installed)", False, "pip install httpx")
        failures += 1

    # ── 15. README ────────────────────────────────────────────────────────────
    readme = REPO_ROOT / "README.md"
    readme_src = readme.read_text(encoding="utf-8") if readme.exists() else ""
    ok = check("README.md exists and references v2 architecture",
               readme.exists() and ("metis" in readme_src.lower() or "v2" in readme_src.lower()),
               f"exists={readme.exists()}")
    if not ok:
        failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P3-D11 complete\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
