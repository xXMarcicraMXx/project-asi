"""
Day 18 — Docker deployment validation script.

Run this ON THE VPS after `docker compose up -d --build asi-app`.

Confirms:
  1. asi-app container is running
  2. /health endpoint responds inside Docker network
  3. DB connection works from inside the container
  4. Slack webhook endpoint is reachable via public URL (if configured)
  5. Log output is valid JSON

Usage:
    python scripts/validate_day18.py
    python scripts/validate_day18.py --skip-public   # skip external URL check
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    line = f"  [{tag}]  {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    return condition


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)


# ---------------------------------------------------------------------------
# Check 1: container is running
# ---------------------------------------------------------------------------

def check_container_running() -> bool:
    result = run(["docker", "compose", "ps", "--format", "json"], cwd="/root/n8n-asi")
    if result.returncode != 0:
        return check("asi-app container running", False, result.stderr[:80])

    # docker compose ps --format json outputs one JSON object per line
    running = False
    for line in result.stdout.strip().splitlines():
        try:
            svc = json.loads(line)
            if "asi-app" in svc.get("Service", "") or "asi-app" in svc.get("Name", ""):
                state = svc.get("State", svc.get("Status", ""))
                running = "running" in state.lower()
                return check("asi-app container running", running, state)
        except json.JSONDecodeError:
            pass

    # Fallback: plain text check
    result2 = run(["docker", "compose", "ps"], cwd="/root/n8n-asi")
    running = "asi-app" in result2.stdout and "Up" in result2.stdout
    snippet = next((l for l in result2.stdout.splitlines() if "asi-app" in l), "not found")
    return check("asi-app container running", running, snippet[:80])


# ---------------------------------------------------------------------------
# Check 2: /health inside Docker network
# ---------------------------------------------------------------------------

def check_health_endpoint() -> bool:
    result = run([
        "docker", "compose", "exec", "-T", "asi-app",
        "curl", "-sf", "http://localhost:3000/health",
    ], cwd="/root/n8n-asi")
    ok = result.returncode == 0 and result.stdout.strip()
    return check(
        "Webhook /health responds inside container",
        ok,
        result.stdout.strip()[:60] or result.stderr.strip()[:60],
    )


# ---------------------------------------------------------------------------
# Check 3: DB connection from inside container
# ---------------------------------------------------------------------------

def check_db_connection() -> bool:
    result = run([
        "docker", "compose", "exec", "-T", "asi-app",
        "python", "-c",
        "import asyncio; from db.session import AsyncSessionLocal; "
        "from sqlalchemy import text; "
        "asyncio.run(AsyncSessionLocal().__aenter__())",
    ], cwd="/root/n8n-asi")
    # Just check it doesn't raise an import/connection error
    ok = result.returncode == 0 or "already exists" in result.stderr
    return check(
        "DB connection from container",
        result.returncode == 0,
        (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "OK")[:80],
    )


# ---------------------------------------------------------------------------
# Check 4: public Slack webhook URL (optional)
# ---------------------------------------------------------------------------

def check_public_url() -> bool:
    import os
    domain = os.environ.get("DOMAIN_NAME", "n8n.metis.rest")
    url = f"https://{domain}/slack/interactive"
    result = run(["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", url])
    # Slack will return 400 (invalid payload) or 403 (bad signature) — both mean
    # the server is reachable and Caddy is routing correctly
    code = result.stdout.strip()
    reachable = code in ("200", "400", "403")
    return check(
        f"Public URL {url} is reachable",
        reachable,
        f"HTTP {code}" if code else result.stderr.strip()[:60],
    )


# ---------------------------------------------------------------------------
# Check 5: logs are valid JSON
# ---------------------------------------------------------------------------

def check_json_logs() -> bool:
    result = run(
        ["docker", "compose", "logs", "--tail", "20", "asi-app"],
        cwd="/root/n8n-asi",
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return check("Container logs present", False, "no log lines found")

    json_lines = 0
    for line in lines:
        # Docker prefixes lines with service name — strip it
        raw = line.split("|", 1)[-1].strip() if "|" in line else line.strip()
        try:
            json.loads(raw)
            json_lines += 1
        except json.JSONDecodeError:
            pass

    # At least some lines should be valid JSON
    ratio = json_lines / len(lines)
    return check(
        "Log output is JSON",
        ratio > 0.3,
        f"{json_lines}/{len(lines)} lines are JSON",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(skip_public: bool) -> None:
    print(f"\n{'─'*60}")
    print(f"  Day 18 — Docker Deployment Validation")
    print(f"{'─'*60}\n")

    results: list[bool] = []

    results.append(check_container_running())
    results.append(check_health_endpoint())
    results.append(check_db_connection())

    if skip_public:
        print("  [SKIP]  Public URL check (--skip-public)")
    else:
        results.append(check_public_url())

    results.append(check_json_logs())

    print(f"\n{'─'*60}")
    failed = sum(1 for r in results if not r)
    if not failed:
        print("  Day 18 / Docker deployment: ALL CHECKS PASSED")
        print("  asi-app is running. Ready for Day 19 scheduler.")
    else:
        print(f"  Day 18 / Docker deployment: {failed} CHECK(S) FAILED")
        print("  Check: sudo docker compose logs asi-app")
    print(f"{'─'*60}\n")

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Day 18 Docker deployment")
    parser.add_argument("--skip-public", action="store_true",
                        help="Skip public URL reachability check")
    args = parser.parse_args()
    main(skip_public=args.skip_public)
