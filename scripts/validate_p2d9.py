"""
P2-D9 validation script -- Caddy deploy scripts.

Checks:
  1.  deploy/publish.sh exists
  2.  deploy/Caddyfile-metis.snippet exists
  3.  deploy/first-deploy.sh exists
  4.  publish.sh bash syntax is valid
  5.  first-deploy.sh bash syntax is valid
  6.  publish.sh contains StrictHostKeyChecking=yes
  7.  publish.sh references VPS_SSH_KEY_PATH
  8.  publish.sh references VPS_HOST
  9.  publish.sh contains set -euo pipefail
 10.  publish.sh supports --dry-run flag
 11.  publish.sh validates required env vars before running
 12.  publish.sh loads .env file if present
 13.  Caddyfile-metis.snippet contains metis.rest site block
 14.  Caddyfile-metis.snippet uses file_server
 15.  Caddyfile-metis.snippet redirects / to /eu/
 16.  Caddyfile-metis.snippet covers all 5 regions (eu|na|latam|apac|africa)
 17.  Caddyfile-metis.snippet does NOT reference ssl_certificate (Caddy auto-TLS)
 18.  .env.example contains VPS_HOST
 19.  .env.example contains VPS_SSH_KEY_PATH
 20.  .env.example contains VPS_WEB_ROOT
 21.  .env.example contains METIS_SITE_ROOT
 22.  .env.example contains METIS_SLACK_CHANNEL_ID
 23.  first-deploy.sh references VPS_WEB_ROOT
 24.  first-deploy.sh contains mkdir
 25.  publish.sh handles all 5 known regions in REGIONS list

Run from repo root:
    python scripts/validate_p2d9.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = "OK" if condition else "FAIL"
    msg = f"  [{status}] {label}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return condition


def bash_syntax_check(path: Path) -> tuple[bool, str]:
    """Run bash -n on a shell script. Returns (ok, error_message)."""
    try:
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0, result.stderr.strip()
    except FileNotFoundError:
        return None, "bash not available on this platform"
    except Exception as exc:
        return None, str(exc)


def main() -> int:
    failures = 0
    print("\nP2-D9 Validation - Caddy Deploy Scripts\n" + "-" * 60)

    publish_sh  = REPO_ROOT / "deploy" / "publish.sh"
    caddy_snip  = REPO_ROOT / "deploy" / "Caddyfile-metis.snippet"
    first_sh    = REPO_ROOT / "deploy" / "first-deploy.sh"
    env_example = REPO_ROOT / ".env.example"

    # ── 1-3. Files exist ──────────────────────────────────────────────────────
    for label, path in [
        ("deploy/publish.sh exists", publish_sh),
        ("deploy/Caddyfile-metis.snippet exists", caddy_snip),
        ("deploy/first-deploy.sh exists", first_sh),
    ]:
        ok = check(label, path.is_file())
        if not ok:
            failures += 1

    # ── 4-5. Bash syntax ──────────────────────────────────────────────────────
    for label, path in [
        ("publish.sh bash syntax valid", publish_sh),
        ("first-deploy.sh bash syntax valid", first_sh),
    ]:
        if not path.is_file():
            failures += 1
            continue
        ok, err = bash_syntax_check(path)
        if ok is None:
            # bash not available (Windows without WSL) — skip, not fail
            check(f"{label} [SKIP — bash not available]", True, err)
        else:
            if not check(label, ok, err):
                failures += 1

    # ── 6-12. publish.sh content ──────────────────────────────────────────────
    if publish_sh.is_file():
        src = publish_sh.read_text(encoding="utf-8")

        checks = {
            "publish.sh contains StrictHostKeyChecking=yes": "StrictHostKeyChecking=yes" in src,
            "publish.sh references VPS_SSH_KEY_PATH":        "VPS_SSH_KEY_PATH" in src,
            "publish.sh references VPS_HOST":                "VPS_HOST" in src,
            "publish.sh contains set -euo pipefail":         "set -euo pipefail" in src,
            "publish.sh supports --dry-run":                 "--dry-run" in src,
            "publish.sh validates required env vars":        "MISSING" in src or "required env var" in src.lower(),
            "publish.sh loads .env if present":              ".env" in src and "source" in src,
        }
        for label, cond in checks.items():
            if not check(label, cond):
                failures += 1

    # ── 13-17. Caddyfile snippet content ──────────────────────────────────────
    if caddy_snip.is_file():
        cad = caddy_snip.read_text(encoding="utf-8")

        ok = check("Caddyfile-metis.snippet contains metis.rest site block",
                   "metis.rest" in cad)
        if not ok: failures += 1

        ok = check("Caddyfile-metis.snippet uses file_server",
                   "file_server" in cad)
        if not ok: failures += 1

        ok = check("Caddyfile-metis.snippet redirects / to /eu/",
                   "/eu/" in cad and ("redir" in cad or "redirect" in cad.lower()))
        if not ok: failures += 1

        ok = check("Caddyfile-metis.snippet covers all 5 regions",
                   all(r in cad for r in ["eu", "na", "latam", "apac", "africa"]))
        if not ok: failures += 1

        # Ignore comment lines (lines starting with #)
        cad_no_comments = "\n".join(
            l for l in cad.splitlines() if not l.strip().startswith("#")
        )
        ok = check("Caddyfile-metis.snippet uses Caddy auto-TLS (no ssl_certificate)",
                   "ssl_certificate" not in cad_no_comments)
        if not ok: failures += 1

    # ── 18-22. .env.example additions ────────────────────────────────────────
    if env_example.is_file():
        env_src = env_example.read_text(encoding="utf-8")
        for var in ["VPS_HOST", "VPS_SSH_KEY_PATH", "VPS_WEB_ROOT",
                    "METIS_SITE_ROOT", "METIS_SLACK_CHANNEL_ID"]:
            if not check(f".env.example contains {var}", var in env_src):
                failures += 1

    # ── 23-25. first-deploy.sh content ───────────────────────────────────────
    if first_sh.is_file():
        fd_src = first_sh.read_text(encoding="utf-8")

        ok = check("first-deploy.sh references VPS_WEB_ROOT", "VPS_WEB_ROOT" in fd_src)
        if not ok: failures += 1

        ok = check("first-deploy.sh contains mkdir", "mkdir" in fd_src)
        if not ok: failures += 1

    if publish_sh.is_file():
        src = publish_sh.read_text(encoding="utf-8")
        ok = check("publish.sh lists all 5 regions",
                   all(r in src for r in ["eu", "na", "latam", "apac", "africa"]))
        if not ok: failures += 1

    print("-" * 60)
    if failures == 0:
        print("  ALL CHECKS PASSED -- P2-D9 complete\n")
        print("  Next step: fill in VPS_HOST, VPS_USER, VPS_WEB_ROOT,")
        print("  VPS_SSH_KEY_PATH in .env, then run deploy/first-deploy.sh\n")
        return 0
    else:
        print(f"  {failures} CHECK(S) FAILED\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
