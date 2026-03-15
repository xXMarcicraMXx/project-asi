# Metis (ASI v2) — Lessons & Patterns

Patterns locked during planning. Read before implementing any new agent or pipeline component.

---

## Session start ritual

**Read `BLOCKERS.md` before starting any implementation day.**
Check the "Blocks" column against today's day number (P1-D1, P2-D9, etc.).
If the day is listed under a blocker: STOP. Do not write any code.
Surface the blocker to the user and wait for resolution.

Days P1-D1 through P2-D8 are fully unblocked. Phase 2 Day 9 onward requires
all VPS blockers resolved. Phase 3 Day 10 requires the Slack app decision.

---

## Agent patterns

**Adversarial text warning is mandatory in every SYSTEM_PROMPT.**
Every agent receives untrusted article content. Without the warning, the agent can be
manipulated by prompt injection in news body text. No exceptions.

**`curation_bias` goes in the user message, not the SYSTEM_PROMPT.**
The SYSTEM_PROMPT must be a static class-level constant for Anthropic prompt caching to work.
Region-specific context (bias, grid_type history, etc.) is always injected per-call in the
user message. Violating this wastes prompt-cache tokens on every invocation.

**All agent output must be parsed through Pydantic before use.**
Never access raw LLM JSON with dict keys. Every new agent returns a typed Pydantic model.
Invalid output = retry once with explicit constraints in user message, then fallback or fail.

**Haiku for structured JSON tasks, Sonnet for creative writing.**
LayoutAgent = Haiku (JSON generation, Pydantic catches quality gaps).
NewsletterWriterAgent = Sonnet (editorial prose quality matters here).
StatusAgent and CurationAgent = Haiku (fast classification tasks).

---

## Pipeline patterns

**Per-region cost ceiling = total_ceiling / N_regions.**
The v1 bug: every parallel region started with `job_cost_so_far=0.0`, making the ceiling
effectively `$2 × 5 = $10`. The fix: divide the ceiling equally across regions before
passing to BaseAgent. One region hitting the ceiling must not silently overspend.

**Every error path must produce a log or Slack alert. No silent failures.**
"Silent failure" = a CRITICAL GAP. If the site isn't updated today, the operator must
know within 60 seconds. The 8 mandatory Slack alerts are listed in `tasks/todo.md`.

---

## DB / Alembic patterns

**`grid_type` (not `layout_id`) is the no-repeat key in `asi2_layout_history`.**
`layout_id` is `"{region}-{date}"` — always unique, enforcing no-repeat on it does nothing.
The 5-day no-repeat window must be on `grid_type`. Hard DB override if agent ignores it.

**All new tables use the `asi2_` prefix. Reuse `alembic_version_asi` chain.**
One Alembic chain, one `upgrade head` command. The prefix is the isolation mechanism —
no separate version table needed. Oracle uses its own `alembic_version_oracle` chain.

---

## Publishing patterns

**Atomic HTML writes only: render to `.tmp`, then `os.replace(tmp, final)`.**
A crash mid-write without atomicity leaves a corrupt `index.html` on the public site.
Always back up the previous `index.html` to `last-good/index.html` before the write.

**Jinja2 `autoescape=True` always. `| safe` is banned on agent output.**
CSS color values from LayoutAgent must be validated against `^#[0-9a-fA-F]{6}$`.
Story URLs must be validated to `http/https` scheme before injection into `href` attributes.

---

## Cancel gate patterns

**DB-backed polling loop, never `asyncio.sleep`.**
`asyncio.sleep(1800)` is lost on container restart. The correct pattern:
- Pipeline sets `edition.publish_at = now() + 30min`
- A polling coroutine (started at app startup) checks every 30s for due editions
- On restart, the poller re-queries the DB and picks up any in-flight publishes
- Cancel = set `cancelled_at` on the edition row; polling loop skips cancelled editions

---

## Deferred work

**Agent personas are Phase 5.** The `curation_bias` YAML field is the MVP regional
differentiation mechanism. Pinecone RAG, golden samples, and editorial voice fine-tuning
do not start until Phases 1–4 are running reliably in production.
