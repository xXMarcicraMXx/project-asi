#!/usr/bin/env bash
# deploy/first-deploy.sh — one-time VPS setup for Metis static file serving
#
# Run this ONCE before the first publish.sh call.
# Creates the directory structure on the VPS that Caddy will serve.
#
# Usage:
#   ./deploy/first-deploy.sh
#
# Required env vars: VPS_HOST, VPS_USER, VPS_WEB_ROOT, VPS_SSH_KEY_PATH

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

MISSING=()
for var in VPS_HOST VPS_USER VPS_WEB_ROOT VPS_SSH_KEY_PATH; do
    if [[ -z "${!var:-}" ]]; then
        MISSING+=("$var")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "ERROR: required env vars not set: ${MISSING[*]}" >&2
    exit 1
fi

VPS_SSH_KEY_PATH="${VPS_SSH_KEY_PATH/#\~/$HOME}"

echo "Setting up Metis directory structure on ${VPS_USER}@${VPS_HOST}..."

ssh -i "${VPS_SSH_KEY_PATH}" \
    -o StrictHostKeyChecking=yes \
    -o BatchMode=yes \
    "${VPS_USER}@${VPS_HOST}" \
    "mkdir -p ${VPS_WEB_ROOT}/{eu,na,latam,apac,africa} && chmod -R 755 ${VPS_WEB_ROOT} && echo 'Directory structure created:' && ls ${VPS_WEB_ROOT}"

echo "Done. VPS is ready for deploy/publish.sh."
