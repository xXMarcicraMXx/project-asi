#!/usr/bin/env bash
# deploy/publish.sh — rsync Metis HTML to VPS
#
# Usage:
#   ./deploy/publish.sh                   # deploy all 5 regions
#   ./deploy/publish.sh eu                # deploy one region
#   ./deploy/publish.sh --dry-run         # dry-run all regions (no transfer)
#   ./deploy/publish.sh --dry-run eu      # dry-run one region
#
# Required env vars (set in .env or export before running):
#   VPS_HOST          hostname or IP of the VPS
#   VPS_USER          SSH user (e.g. ubuntu)
#   VPS_WEB_ROOT      absolute path on VPS (e.g. /home/ubuntu/metis-site)
#   VPS_SSH_KEY_PATH  path to SSH private key (e.g. ~/.ssh/id_ed25519)
#
# Exit codes:
#   0  all rsync calls succeeded
#   1  one or more rsync calls failed or required env var is missing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Load .env if present ──────────────────────────────────────────────────────
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

# ── Parse arguments ───────────────────────────────────────────────────────────
DRY_RUN=""
REGION=""

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="--dry-run" ;;
        eu|na|latam|apac|africa) REGION="$arg" ;;
        *)
            echo "ERROR: unknown argument: $arg" >&2
            echo "Usage: $0 [--dry-run] [eu|na|latam|apac|africa]" >&2
            exit 1
            ;;
    esac
done

ALL_REGIONS=(eu na latam apac africa)
if [[ -n "$REGION" ]]; then
    REGIONS=("$REGION")
else
    REGIONS=("${ALL_REGIONS[@]}")
fi

# ── Validate required env vars ────────────────────────────────────────────────
MISSING=()
for var in VPS_HOST VPS_USER VPS_WEB_ROOT VPS_SSH_KEY_PATH; do
    if [[ -z "${!var:-}" ]]; then
        MISSING+=("$var")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "ERROR: required env vars not set: ${MISSING[*]}" >&2
    echo "Set them in .env or export before running." >&2
    exit 1
fi

# Expand ~ in key path
VPS_SSH_KEY_PATH="${VPS_SSH_KEY_PATH/#\~/$HOME}"

# ── Deploy ────────────────────────────────────────────────────────────────────
SITE_ROOT="${METIS_SITE_ROOT:-${REPO_ROOT}/site}"
FAILURES=0

if [[ -n "$DRY_RUN" ]]; then
    echo "DRY RUN — no files will be transferred"
fi

for region in "${REGIONS[@]}"; do
    local_dir="${SITE_ROOT}/${region}/"

    if [[ ! -d "$local_dir" ]]; then
        echo "SKIP ${region} — local directory not found: ${local_dir}"
        continue
    fi

    echo "Deploying ${region}..."

    # shellcheck disable=SC2086
    if rsync -avz --delete $DRY_RUN \
        -e "ssh -i ${VPS_SSH_KEY_PATH} -o StrictHostKeyChecking=yes -o BatchMode=yes" \
        "${local_dir}" \
        "${VPS_USER}@${VPS_HOST}:${VPS_WEB_ROOT}/${region}/"; then
        echo "OK ${region}"
    else
        echo "FAIL ${region}" >&2
        FAILURES=$((FAILURES + 1))
    fi
done

if [[ $FAILURES -gt 0 ]]; then
    echo "ERROR: ${FAILURES} region(s) failed to deploy" >&2
    exit 1
fi

echo "Done."
