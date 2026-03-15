# Metis — Implementation Blockers

**Read this file at the start of every implementation day.**
Check your current day number against the "Blocks" column.
If your day is listed: STOP. Do not write code. Resolve the blocker first.

Last updated: 2026-03-15

---

## Status legend
- `UNRESOLVED` — cannot proceed past the listed day without an answer
- `RESOLVED` — answer confirmed, day can proceed

---

## BLOCKER 1 — VPS Web Root Path
**Status:** `UNRESOLVED`
**Blocks:** P2-D9 (Nginx + rsync deploy) and every day after

### What breaks without it
`deploy/publish.sh` cannot be written. The rsync command requires the exact remote
path: `rsync ... user@host:/var/www/???/eu/`. If this is wrong, every rsync call
silently deploys to the wrong directory, the Nginx config points nowhere, and
`metis.rest/eu/` returns 404.

The Nginx config template in `review-eng.md` uses `{{WEB_ROOT}}` as a placeholder —
it cannot be committed until this is filled in.

### Evidence from plan
`review-eng.md §6` — `VPS_WEB_ROOT=/var/www/metis` is the assumed value, marked
"CONFIRM THIS before P2-D9". `todo.md §BLOCKED` row 1.

### What is needed
Exact directory path on the VPS where Nginx serves static files for metis.rest.
Example answers: `/var/www/metis`, `/srv/metis`, `/home/deploy/metis`

Run on VPS to find it:
```bash
grep -r "root " /etc/nginx/sites-enabled/ 2>/dev/null
# or check where existing sites are served from
ls /var/www/
```

---

## BLOCKER 2 — SSL Certificate Status
**Status:** `UNRESOLVED`
**Blocks:** P2-D9 (Nginx config) and the live site going HTTPS

### What breaks without it
If Let's Encrypt is NOT configured for `metis.rest`, the Nginx config in
`deploy/nginx-metis.conf` will fail to start — it references:
```
ssl_certificate     /etc/letsencrypt/live/metis.rest/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/metis.rest/privkey.pem;
```
If these files don't exist, Nginx refuses to start and the entire site is offline.

If it IS already configured, P2-D9 is unblocked with no extra work.

### Evidence from plan
`review-eng.md §6 — First-deploy checklist`: "Install certbot + SSL (if not already)".
The first-deploy steps are conditional on this answer.

### What is needed
Run on VPS:
```bash
certbot certificates 2>/dev/null | grep -A3 "metis.rest"
# If output shows metis.rest → RESOLVED (SSL exists)
# If no output → needs certbot setup before P2-D9
```

If SSL is NOT set up, the first-deploy checklist in `review-eng.md §6` must run
before P2-D9 proceeds.

---

## BLOCKER 3 — VPS SSH Key Path
**Status:** `UNRESOLVED`
**Blocks:** P2-D9 (rsync deploy), P3-D10 (cancel gate auto-publish), P3-D11 (validation)

### What breaks without it
`deploy/publish.sh` hardcodes the SSH key path:
```bash
-e "ssh -i ${VPS_SSH_KEY_PATH} -o StrictHostKeyChecking=yes"
```
If `VPS_SSH_KEY_PATH` is wrong or the key doesn't have access to the VPS,
every rsync call fails with `Permission denied (publickey)`. The cancel gate
polling loop then fires Slack alerts on every 30-second tick.

`StrictHostKeyChecking=yes` (required for security — prevents MITM) means rsync
will hard-fail rather than prompt, so a wrong key path = complete deploy failure,
no fallback.

### Evidence from plan
`todo.md §Pre-implementation checklist` item 13: "`publish.sh` uses SSH key auth,
`StrictHostKeyChecking=yes` (no password)". `review-eng.md §6 env vars`:
`VPS_SSH_KEY_PATH=~/.ssh/id_metis # never use password auth`.

### What is needed
The exact path to the SSH private key that has access to the VPS.
Example answers: `~/.ssh/id_rsa`, `~/.ssh/id_metis`, `~/.ssh/id_ed25519`

Run on local machine to check:
```bash
ls ~/.ssh/*.pub
# Match the public key to what's in the VPS authorized_keys
ssh -i ~/.ssh/YOUR_KEY user@your-vps "echo ok"
```

---

## BLOCKER 4 — Slack App Decision
**Status:** `UNRESOLVED`
**Blocks:** P3-D10 (Slack cancel-window gate)

### What breaks without it
P3-D10 wires the cancel gate to Slack. The implementation differs depending on
the decision:

**Option A — Reuse existing ASI Slack app:**
- Existing `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` work
- The cancel webhook URL must be updated to point at the Metis app server
- Risk: ASI and Metis cancel buttons land in the same Slack channel — confusing

**Option B — New Slack app for Metis:**
- New app, new `SLACK_BOT_TOKEN`, new channel
- Cleaner separation: ASI alerts in #asi-ops, Metis alerts in #metis-ops
- ~15 min setup at api.slack.com

If this is not decided before P3-D10, the cancel gate cannot be tested end-to-end.
The code path `if not SLACK_BOT_TOKEN: publish_immediately()` exists as a fallback,
but that means the 30-min cancel window is silently bypassed — which defeats the
entire point of the gate.

### Evidence from plan
`todo.md §BLOCKED` row 4. `review-eng.md §10`: "Slack app reuse | P3-D10 | User".
`todo.md P3-D10`: "BLOCKED on Slack app decision (reuse ASI app vs new Metis app)".

### What is needed
Answer: **A** (reuse ASI app) or **B** (new app).

If A: confirm the existing `SLACK_BOT_TOKEN` is in `.env` and note which channel
Metis alerts should go to.

If B: create the app at api.slack.com, add `chat:write` and
`chat:write.public` scopes, install to workspace, copy the bot token to
`.env` as `METIS_SLACK_BOT_TOKEN`.

---

## Quick reference

| # | Blocker | Blocks | Status |
|---|---|---|---|
| 1 | VPS web root path | P2-D9+ | `UNRESOLVED` |
| 2 | SSL cert status | P2-D9 | `UNRESOLVED` |
| 3 | VPS SSH key path | P2-D9+ | `UNRESOLVED` |
| 4 | Slack app decision | P3-D10 | `UNRESOLVED` |

**Days P1-D1 through P2-D8 are fully unblocked.**
You can implement all of Phase 1 and the first 3 days of Phase 2 without resolving anything above.
