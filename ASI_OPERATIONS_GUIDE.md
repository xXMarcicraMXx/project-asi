# Project ASI — Operations & Customisation Guide

A complete reference for understanding the system, tuning editorial output,
and querying the database. Written at Day 20 completion.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Deep-Dive](#2-architecture-deep-dive)
3. [Data Model](#3-data-model)
4. [How to Modify Editorial Output](#4-how-to-modify-editorial-output)
   - 4.1 [The three layers of editorial control](#41-the-three-layers-of-editorial-control)
   - 4.2 [Layer 1 — Region YAML (fast, no re-ingestion)](#42-layer-1--region-yaml-fast-no-re-ingestion)
   - 4.3 [Layer 2 — Pinecone persona guidelines (rich context, requires re-ingestion)](#43-layer-2--pinecone-persona-guidelines-rich-context-requires-re-ingestion)
   - 4.4 [Layer 3 — Golden samples (style exemplars, requires re-ingestion)](#44-layer-3--golden-samples-style-exemplars-requires-re-ingestion)
   - 4.5 [How to add a real persona instead of a generic character](#45-how-to-add-a-real-persona-instead-of-a-generic-character)
   - 4.6 [Writer system prompt](#46-writer-system-prompt)
   - 4.7 [Editor criteria (what triggers a revision)](#47-editor-criteria-what-triggers-a-revision)
   - 4.8 [Changing article length and structure](#48-changing-article-length-and-structure)
5. [Re-ingesting personas into Pinecone](#5-re-ingesting-personas-into-pinecone)
6. [Scheduler Configuration](#6-scheduler-configuration)
7. [SQL Reference](#7-sql-reference)
   - 7.1 [Job monitoring](#71-job-monitoring)
   - 7.2 [Article content](#72-article-content)
   - 7.3 [Cost and token usage](#73-cost-and-token-usage)
   - 7.4 [Editorial quality signals](#74-editorial-quality-signals)
   - 7.5 [Full audit trail for a job](#75-full-audit-trail-for-a-job)
   - 7.6 [Operational dashboards](#76-operational-dashboards)

---

## 1. System Overview

Project ASI is a **fully autonomous multi-regional journal article engine**.

Once deployed it runs unattended. Every day at 07:00 UTC it:
1. Picks a topic from a rotating list
2. Fetches current source articles via RSS
3. Runs a **Research → Write → Edit** agent chain for each of four regions **in parallel**
4. Posts the results to Slack for human approval
5. Publishes approved articles as markdown files

**No human in the loop is required for the pipeline itself.** The Slack gate is optional
— if unanswered, articles remain in `human_review` state in the database.

**Cost per daily run:** approximately $0.05–0.15 USD depending on topic complexity and
revision cycles. Hard ceiling: $2.00 per job (configurable).

---

## 2. Architecture Deep-Dive

```
┌─────────────────────────────────────────────────────────────────┐
│  app.py  (Docker CMD)                                           │
│                                                                 │
│  AsyncIOScheduler (APScheduler)                                 │
│    └─ CronTrigger  "0 7 * * *" UTC                              │
│         └─ run_scheduled_job()                                  │
│              └─ run_pipeline(JobPayload)                        │
│                                                                 │
│  aiohttp webhook server  port 3000                              │
│    POST /slack/interactive   ← Slack button callbacks           │
│    GET  /health              ← Docker healthcheck               │
└─────────────────────────────────────────────────────────────────┘

run_pipeline()
│
├─ Create Job row (status='running')
├─ RSSSource.fetch(topic)            ← one RSS fetch for all regions
│
└─ asyncio.gather()                  ← all 4 regions run in parallel
     │
     └─ _run_region_task(region_id)  ← own AsyncSession per region
          │
          ├─ Create Brief + ContentPiece rows
          │
          └─ AgentChain.run()
               │
               ├─ ResearchAgent       claude-haiku  — extracts facts from RSS
               │    └─ ResearchBrief (key_facts, data_points, direct_quotes)
               │
               ├─ _fetch_rag_context()              — Pinecone RAG (non-blocking)
               │    ├─ persona_guideline (top_k=1)
               │    └─ golden_sample    (top_k=1)
               │
               └─ loop: up to 4 iterations
                    ├─ WriterAgent     claude-sonnet — drafts article
                    └─ EditorAgent     claude-sonnet — approve or revise
                         ├─ approve → return draft
                         └─ revise  → carry feedback into next iteration

post-gather:
├─ Update job.status  ('complete' | 'partial' | 'failed')
├─ post_for_approval()          ← Slack Block Kit messages
└─ return drafts
```

### Key files

| Path | Purpose |
|---|---|
| `app.py` | Entrypoint: starts scheduler + webhook server on same event loop |
| `orchestrator/scheduler.py` | APScheduler setup, topic picker, `run_scheduled_job()` |
| `orchestrator/pipeline.py` | `run_pipeline()` + `_run_region_task()` + cost report query |
| `agents/chain.py` | AgentChain: Research → Write → Edit loop, RAG context injection |
| `agents/research_agent.py` | Haiku: extracts ResearchBrief JSON from RSS articles |
| `agents/writer_agent.py` | Sonnet: produces article draft from brief + persona context |
| `agents/editor_agent.py` | Sonnet: evaluates draft, returns approve/revise JSON |
| `approval/slack_bot.py` | Posts Block Kit messages, handles button callbacks |
| `publishers/markdown_publisher.py` | Writes approved articles to disk as `.md` |
| `ingestion/run_ingestion.py` | Seeds Pinecone with personas + golden samples |
| `rag/pinecone_client.py` | Pinecone v4 SDK wrapper (query + upsert) |
| `config/__init__.py` | Pydantic config loader — validates all YAML on startup |
| `db/models.py` | SQLAlchemy ORM (Job, Brief, ContentPiece, AgentRun, FeedbackLoop) |
| `cli.py` | Manual pipeline trigger with cost report + `--dry-run` |

---

## 3. Data Model

```
jobs
  id              UUID PK
  project         'asi' | 'oracle'          ← multi-project isolation
  topic           TEXT
  content_type    'journal_article'
  regions         TEXT[]                    ← ['EU','LATAM','SEA','NA']
  status          running | complete | partial | failed
  config_snapshot JSONB                     ← full YAML snapshot at run time
  created_at      TIMESTAMPTZ
  completed_at    TIMESTAMPTZ

briefs
  id              UUID PK
  job_id          FK → jobs.id
  status          draft | approved | rejected | human_review | published
  created_at      TIMESTAMPTZ

content_pieces
  id              UUID PK
  brief_id        FK → briefs.id
  region          'EU' | 'LATAM' | 'SEA' | 'NA'
  content_type    'regional_article'
  headline        TEXT
  body            TEXT                      ← full markdown article
  word_count      INTEGER
  iteration_count INTEGER                   ← how many write/edit cycles ran
  status          draft | approved | human_review
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ

agent_runs                                  ← one row per agent call
  id              UUID PK
  content_piece_id FK → content_pieces.id
  agent_name      'research_agent' | 'writer_agent' | 'editor_agent'
  iteration       INTEGER
  input_tokens    INTEGER
  output_tokens   INTEGER
  cost_usd        NUMERIC(10,6)
  duration_ms     INTEGER
  created_at      TIMESTAMPTZ

feedback_loops                              ← one row per editor verdict
  id              UUID PK
  content_piece_id FK → content_pieces.id
  iteration       INTEGER
  status          'approve' | 'revise'
  feedback        TEXT                      ← editor's instruction to writer
  created_at      TIMESTAMPTZ
```

---

## 4. How to Modify Editorial Output

### 4.1 The three layers of editorial control

The WriterAgent receives editorial context from three stacked layers:

```
┌──────────────────────────────────────────────────────────────┐
│  Layer 3 — Pinecone golden sample                            │
│  Concrete exemplar article showing the desired voice.        │
│  Highest influence on style. Requires re-ingestion to update.│
├──────────────────────────────────────────────────────────────┤
│  Layer 2 — Pinecone persona guideline                        │
│  Detailed editorial persona: framing hierarchy, vocabulary   │
│  markers, forbidden patterns, critical posture.              │
│  Requires re-ingestion to update.                            │
├──────────────────────────────────────────────────────────────┤
│  Layer 1 — Region YAML (config/regions/<region>.yaml)        │
│  editorial_voice: short voice description injected directly  │
│  into every writer prompt. No re-ingestion needed.           │
│  Takes effect immediately on next run.                       │
└──────────────────────────────────────────────────────────────┘
```

All three are injected into the writer's user message in this order:
```
EDITORIAL VOICE:            ← from Layer 1 (YAML)
FORMAT INSTRUCTIONS:        ← from content_types/journal_article.yaml
ADDITIONAL PERSONA CONTEXT: ← from Layer 2 + 3 (Pinecone, if available)
EDITOR FEEDBACK:            ← from previous iteration (if revision pass)
RESEARCH BRIEF:             ← facts extracted from RSS
```

---

### 4.2 Layer 1 — Region YAML (fast, no re-ingestion)

**File:** `config/regions/<region>.yaml`
**Takes effect:** immediately on next pipeline run (no restart needed)

```yaml
# config/regions/europe.yaml
region_id: EU
display_name: Europe

editorial_voice: |
  You are a senior journalist writing for a pan-European broadsheet...
  [this entire block is injected verbatim into the writer prompt]

demographic_anchor:
  location: Brussels
  cultural_lens: Western European liberal-institutional tradition

pinecone_metadata:
  department: editorial_EU   # must match Pinecone filter used in ingestion
```

**To modify:** edit `editorial_voice` directly. Keep it under ~300 words —
it appears in every single writer call and affects prompt cache efficiency.

---

### 4.3 Layer 2 — Pinecone persona guidelines (rich context, requires re-ingestion)

**File:** `ingestion/run_ingestion.py`, dict `_PERSONAS`
**Takes effect:** after running `python ingestion/run_ingestion.py`

The persona guideline is a richer, longer document (typically 300–600 words) covering:
- **Framing hierarchy** — which dimension leads (institutional, economic, social)
- **Voice and tone** — sentence architecture, analytical posture
- **Critical posture** — what the persona challenges vs accepts at face value
- **Explicit avoids** — idioms, framings, reference points to suppress

**Current personas (summary):**

| Region | Outlet type | Lead frame | Distinctive posture |
|---|---|---|---|
| EU | Pan-European broadsheet | Institutional/regulatory | Sceptical of unilateral action, names member states specifically |
| LATAM | Pan-regional news outlet | Political economy / power | Names IMF/World Bank as political actors, centres distribution |
| SEA | Pan-regional commercial pub | Strategic/commercial | Non-alignment as structural necessity, differentiates ASEAN countries |
| NA | Major North American newspaper | Direct domestic impact | Lead with news, shorter paragraphs, no beltway shorthand |

---

### 4.4 Layer 3 — Golden samples (style exemplars, requires re-ingestion)

**File:** `ingestion/run_ingestion.py`, dict `_GOLDEN_SAMPLES`
**Takes effect:** after running `python ingestion/run_ingestion.py`

The golden sample is a complete **hand-written exemplar article** (~400–600 words)
on a neutral topic (currently: central bank rate policy). The writer receives it with
the instruction:

```
EXEMPLAR ARTICLE (reference voice only — do not copy):
[sample text]
```

**What makes an effective golden sample:**
- Written in the exact voice you want, at the exact length you want
- On a topic unrelated to the actual topic being generated (avoids copy-paste temptation)
- Demonstrates the lead, body structure, and closing that you want the agent to replicate
- Includes the vocabulary markers you want to see ("structural", "regulatory framework", etc.)
- Does NOT include a preamble, byline, or metadata — raw markdown article only

**To write a new golden sample for a region:**
1. Write the article yourself, or use Claude interactively to draft it with explicit style instructions
2. Replace the relevant entry in `_GOLDEN_SAMPLES` in `ingestion/run_ingestion.py`
3. Update the document ID to bump the version: `editorial_EU-golden-v2`
4. Re-run ingestion (see Section 5)

> **Tip:** The golden sample has the highest per-token influence of the three layers.
> If output quality is poor, improving the golden sample is the highest-leverage change.

---

### 4.5 How to add a real persona instead of a generic character

The current personas describe outlet archetypes. To replace them with a specific
named editorial voice (e.g. a specific columnist style, house style of a real publication,
or a character with a name and background):

**Step 1 — Write the persona in `_PERSONAS`**

Replace the region's entry. Include:
```
EDITORIAL PERSONA — [Name / Publication / Character]

[2–3 sentences describing who this person is and who they write for]

FRAMING HIERARCHY
1. [What leads]
2. [Second priority]
3. [Third priority]

VOICE AND TONE
[Sentence structure, register, paragraph length, how they open and close]

CRITICAL POSTURE
[What this persona challenges, what they accept, what they name explicitly]

SIGNATURE PATTERNS
[Specific constructions, phrases, rhetorical moves that identify this voice]

AVOID
[Explicit list of idioms, framings, words this persona never uses]
```

**Step 2 — Write a new golden sample in their voice**

The golden sample is a concrete demonstration. Abstract persona instructions
alone are less reliable than a concrete example. Write ~400 words in the voice
you want before re-ingesting.

**Step 3 — Re-ingest (Section 5)**

**Step 4 — Optionally update the YAML `editorial_voice`**

The YAML field is shown in every prompt even before the Pinecone context loads.
Keep it as a compact version of the persona (3–5 key instructions, ≤150 words).

---

### 4.6 Writer system prompt

**File:** `agents/writer_agent.py`, class attribute `SYSTEM_PROMPT`

```python
SYSTEM_PROMPT = """You are a professional journalist and editor.
...
- Adopt the editorial voice described in each request...
- Output raw markdown only. No preamble, no explanation, no sign-off."""
```

This is the **static system prompt** — it is the same for every region and every topic.
It defines the writer's core constraints. The regional character comes entirely from
the user message (editorial voice + persona context).

**To make the writer more concise** — add to SYSTEM_PROMPT:
```
- Prefer shorter sentences. Average sentence length should not exceed 20 words.
- Keep paragraphs to 3–4 sentences maximum.
- Never use nominalisations where a verb is available.
```

**To suppress a pattern you see in every output** — add an explicit prohibition:
```
- Never use the phrase "It is worth noting". Never open with "In recent months".
- Do not summarise in the conclusion — close with an unresolved question.
```

> Note: system prompt changes take effect immediately on next run, no restart or
> re-ingestion needed.

---

### 4.7 Editor criteria (what triggers a revision)

**File:** `config/content_types/journal_article.yaml`

```yaml
editor_criteria:
  - Word count is between 600 and 1200 words
  - Regional editorial voice is consistent throughout
  - No invented statistics or fabricated quotes
  - Headline is specific, not generic
  - Article reads as a standalone publishable piece
```

Each criterion is evaluated independently. If any fails, the editor returns a
`revise` verdict with specific feedback for the writer. The pipeline retries up
to **4 iterations** before escalating to `human_review`.

**To tighten quality:** add stricter criteria:
```yaml
  - No paragraph exceeds 5 sentences
  - Opening sentence does not begin with "In recent months", "As", or "The"
  - Conclusion ends with a forward-looking question or implication, not a summary
  - At least two specific named actors (institutions, countries, or individuals) appear
```

**To reduce revision loops** (faster + cheaper): remove criteria or make them looser.

---

### 4.8 Changing article length and structure

**File:** `config/content_types/journal_article.yaml`

```yaml
output:
  min_words: 600      # editor rejects if word_count < this
  max_words: 1200     # editor rejects if word_count > this

writer_instructions: |
  Write a formal journal article using markdown section headers (##).
  Structure: introduction, 3–4 body paragraphs, conclusion.
  Cite sources inline as [Source Name].
```

**For shorter, more focused articles:**
```yaml
output:
  min_words: 350
  max_words: 600

writer_instructions: |
  Write a concise analytical note — no headers, flowing prose only.
  Structure: one-sentence news lead, two analytical paragraphs, one closing implication.
  Maximum four paragraphs total. Cite sources inline as [Source Name].
```

**For longer deep-dive pieces:**
```yaml
output:
  min_words: 1000
  max_words: 2000

writer_instructions: |
  Write a long-form journal article with markdown section headers (##).
  Structure: introduction, 5–6 body sections each with a sub-header, conclusion.
  Each section must advance a distinct argument. Cite sources inline as [Source Name].
```

> Changes to `min_words` / `max_words` take effect immediately.
> The editor uses these exact numbers in its criteria evaluation.

---

## 5. Re-ingesting personas into Pinecone

After editing personas or golden samples in `ingestion/run_ingestion.py`:

```bash
# Validate without uploading
python ingestion/run_ingestion.py --dry-run

# Upload to Pinecone (idempotent — safe to re-run)
python ingestion/run_ingestion.py
```

Pinecone upsert is **idempotent on document ID**. Existing vectors with the same ID
are overwritten. New document IDs are added.

**Versioning convention:** bump the version suffix when replacing a document:
```python
doc_id="editorial_EU-persona-v2"   # was v1
doc_id="editorial_EU-golden-v2"    # was v1
```

The query in `rag/pinecone_client.py` fetches `top_k=1` per document type — it returns
whichever vector is most semantically similar to the current topic. If you have both v1
and v2 in the index, the query may return either depending on the topic. **Delete old
versions** from the Pinecone console, or use a Pinecone delete call before re-running:

```python
# In Python — delete old version before upserting new one
from rag.pinecone_client import PineconeClient
client = PineconeClient.from_settings()
client._index.delete(ids=["editorial_EU-persona-v1"])
```

---

## 6. Scheduler Configuration

**File:** `config/settings.yaml`

```yaml
scheduler:
  cron: "0 7 * * *"           # daily at 07:00 UTC  (crontab format)
  default_regions: [EU, LATAM, SEA, NA]
  default_topics:
    - "global economic outlook and central bank policy"
    - "geopolitical tensions and trade policy implications"
    - "artificial intelligence regulation and governance"
    - "energy transition and climate policy economics"
    - "emerging market debt and currency pressures"
```

**Topic rotation:** topics rotate by `day_of_year % len(topics)`. With 5 topics and
a daily run, each topic recurs approximately every 5 days.

**To trigger a run immediately (without waiting for the cron):**
```bash
# On VPS, with container running
docker compose exec asi-app python -c "
import asyncio
from orchestrator.scheduler import run_scheduled_job
asyncio.run(run_scheduled_job())
"

# Or directly on host
python -c "
import asyncio
from orchestrator.scheduler import run_scheduled_job
asyncio.run(run_scheduled_job())
"
```

**To run a one-off job on a specific topic:**
```bash
python cli.py run --topic "your topic here" --regions EU LATAM SEA NA
```

---

## 7. SQL Reference

All queries assume `project = 'asi'` to isolate from any other project sharing
the database (e.g. ORACLE).

---

### 7.1 Job monitoring

**All jobs, most recent first:**
```sql
SELECT
    id,
    topic,
    status,
    regions,
    created_at AT TIME ZONE 'UTC' AS created_utc,
    completed_at AT TIME ZONE 'UTC' AS completed_utc,
    EXTRACT(EPOCH FROM (completed_at - created_at)) AS duration_seconds
FROM jobs
WHERE project = 'asi'
ORDER BY created_at DESC
LIMIT 20;
```

**Jobs by status:**
```sql
SELECT status, COUNT(*) AS count
FROM jobs
WHERE project = 'asi'
GROUP BY status
ORDER BY count DESC;
```

**Failed or partial jobs in the last 7 days:**
```sql
SELECT id, topic, status, created_at
FROM jobs
WHERE project = 'asi'
  AND status IN ('failed', 'partial')
  AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

**Jobs currently running (stuck check):**
```sql
SELECT id, topic, created_at,
       EXTRACT(EPOCH FROM (NOW() - created_at))/60 AS minutes_running
FROM jobs
WHERE project = 'asi'
  AND status = 'running'
ORDER BY created_at;
```

---

### 7.2 Article content

**All approved articles:**
```sql
SELECT
    j.topic,
    cp.region,
    cp.headline,
    cp.word_count,
    cp.iteration_count,
    cp.status,
    cp.updated_at AT TIME ZONE 'UTC' AS approved_at
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND cp.status = 'approved'
ORDER BY cp.updated_at DESC;
```

**Articles pending human review (iteration cap reached):**
```sql
SELECT
    j.id AS job_id,
    j.topic,
    cp.region,
    cp.headline,
    cp.word_count,
    cp.iteration_count
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND cp.status = 'human_review'
ORDER BY j.created_at DESC;
```

**Full article body for a specific job + region:**
```sql
SELECT cp.headline, cp.body, cp.word_count, cp.status
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
WHERE b.job_id = '<job_uuid>'
  AND cp.region = 'EU';
```

**Word count distribution by region:**
```sql
SELECT
    cp.region,
    ROUND(AVG(cp.word_count)) AS avg_words,
    MIN(cp.word_count) AS min_words,
    MAX(cp.word_count) AS max_words,
    COUNT(*) AS article_count
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND cp.word_count IS NOT NULL
GROUP BY cp.region
ORDER BY cp.region;
```

---

### 7.3 Cost and token usage

**Total cost per job:**
```sql
SELECT
    j.id,
    j.topic,
    j.created_at AT TIME ZONE 'UTC' AS run_date,
    SUM(ar.cost_usd)::NUMERIC(10,4) AS total_cost_usd,
    SUM(ar.input_tokens) AS total_input_tokens,
    SUM(ar.output_tokens) AS total_output_tokens
FROM agent_runs ar
JOIN content_pieces cp ON ar.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
GROUP BY j.id, j.topic, j.created_at
ORDER BY j.created_at DESC
LIMIT 20;
```

**Cost breakdown by agent and region for a specific job:**
```sql
SELECT
    cp.region,
    ar.agent_name,
    COUNT(*) AS calls,
    SUM(ar.input_tokens) AS input_tokens,
    SUM(ar.output_tokens) AS output_tokens,
    SUM(ar.cost_usd)::NUMERIC(10,6) AS cost_usd,
    ROUND(AVG(ar.duration_ms)) AS avg_duration_ms
FROM agent_runs ar
JOIN content_pieces cp ON ar.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
WHERE b.job_id = '<job_uuid>'
GROUP BY cp.region, ar.agent_name
ORDER BY cp.region, ar.agent_name;
```

**Daily cost over the last 30 days:**
```sql
SELECT
    DATE(j.created_at AT TIME ZONE 'UTC') AS run_date,
    SUM(ar.cost_usd)::NUMERIC(10,4) AS total_cost_usd,
    COUNT(DISTINCT j.id) AS jobs_run
FROM agent_runs ar
JOIN content_pieces cp ON ar.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND j.created_at > NOW() - INTERVAL '30 days'
GROUP BY DATE(j.created_at AT TIME ZONE 'UTC')
ORDER BY run_date DESC;
```

**Most expensive topics:**
```sql
SELECT
    j.topic,
    COUNT(DISTINCT j.id) AS runs,
    AVG(job_costs.cost_usd)::NUMERIC(10,4) AS avg_cost_usd,
    MAX(job_costs.cost_usd)::NUMERIC(10,4) AS max_cost_usd
FROM jobs j
JOIN (
    SELECT b.job_id, SUM(ar.cost_usd) AS cost_usd
    FROM agent_runs ar
    JOIN content_pieces cp ON ar.content_piece_id = cp.id
    JOIN briefs b ON cp.brief_id = b.id
    GROUP BY b.job_id
) job_costs ON job_costs.job_id = j.id
WHERE j.project = 'asi'
GROUP BY j.topic
ORDER BY avg_cost_usd DESC;
```

**Token usage by model (inferred from agent name):**
```sql
SELECT
    ar.agent_name,
    -- research_agent uses Haiku, writer/editor use Sonnet
    CASE
        WHEN ar.agent_name = 'research_agent' THEN 'claude-haiku'
        ELSE 'claude-sonnet'
    END AS model,
    COUNT(*) AS calls,
    SUM(ar.input_tokens) AS total_input_tokens,
    SUM(ar.output_tokens) AS total_output_tokens,
    SUM(ar.cost_usd)::NUMERIC(10,4) AS total_cost_usd
FROM agent_runs ar
JOIN content_pieces cp ON ar.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
GROUP BY ar.agent_name
ORDER BY total_cost_usd DESC;
```

---

### 7.4 Editorial quality signals

**Revision rate by region (how often each region needs revisions):**
```sql
SELECT
    cp.region,
    COUNT(*) AS total_pieces,
    SUM(CASE WHEN cp.iteration_count = 1 THEN 1 ELSE 0 END) AS approved_first_pass,
    SUM(CASE WHEN cp.iteration_count > 1 THEN 1 ELSE 0 END) AS needed_revision,
    SUM(CASE WHEN cp.status = 'human_review' THEN 1 ELSE 0 END) AS escalated,
    ROUND(AVG(cp.iteration_count), 2) AS avg_iterations
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND cp.iteration_count > 0
GROUP BY cp.region
ORDER BY avg_iterations DESC;
```

**Editor feedback — what reasons trigger revisions most often:**
```sql
SELECT
    fl.feedback,
    COUNT(*) AS occurrences
FROM feedback_loops fl
JOIN content_pieces cp ON fl.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND fl.status = 'revise'
ORDER BY occurrences DESC
LIMIT 20;
```

**All feedback for a specific content piece (audit trail):**
```sql
SELECT iteration, status, feedback, created_at
FROM feedback_loops
WHERE content_piece_id = '<piece_uuid>'
ORDER BY iteration;
```

**Pieces that consistently hit the iteration cap:**
```sql
SELECT
    cp.region,
    j.topic,
    cp.iteration_count,
    cp.word_count,
    cp.headline
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND cp.iteration_count >= 4
ORDER BY j.created_at DESC;
```

---

### 7.5 Full audit trail for a job

Replace `'<job_uuid>'` with a real job ID. Gives the complete execution story.

**Step 1 — Job overview:**
```sql
SELECT id, topic, status, regions, created_at, completed_at,
       EXTRACT(EPOCH FROM (completed_at - created_at)) AS duration_sec
FROM jobs WHERE id = '<job_uuid>';
```

**Step 2 — All content pieces for the job:**
```sql
SELECT cp.region, cp.headline, cp.word_count, cp.iteration_count, cp.status
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
WHERE b.job_id = '<job_uuid>'
ORDER BY cp.region;
```

**Step 3 — All agent calls with timing and cost:**
```sql
SELECT
    cp.region,
    ar.agent_name,
    ar.iteration,
    ar.input_tokens,
    ar.output_tokens,
    ar.cost_usd,
    ar.duration_ms,
    ar.created_at AT TIME ZONE 'UTC' AS called_at
FROM agent_runs ar
JOIN content_pieces cp ON ar.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
WHERE b.job_id = '<job_uuid>'
ORDER BY ar.created_at;
```

**Step 4 — Editor feedback sequence:**
```sql
SELECT
    cp.region,
    fl.iteration,
    fl.status,
    fl.feedback
FROM feedback_loops fl
JOIN content_pieces cp ON fl.content_piece_id = cp.id
JOIN briefs b ON cp.brief_id = b.id
WHERE b.job_id = '<job_uuid>'
ORDER BY cp.region, fl.iteration;
```

---

### 7.6 Operational dashboards

**30-day health summary:**
```sql
SELECT
    COUNT(*) FILTER (WHERE status = 'complete') AS complete,
    COUNT(*) FILTER (WHERE status = 'partial') AS partial,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed,
    COUNT(*) FILTER (WHERE status = 'running') AS still_running,
    SUM(job_costs.cost) FILTER (WHERE j.status = 'complete')::NUMERIC(10,2) AS total_cost_usd
FROM jobs j
LEFT JOIN (
    SELECT b.job_id, SUM(ar.cost_usd) AS cost
    FROM agent_runs ar
    JOIN content_pieces cp ON ar.content_piece_id = cp.id
    JOIN briefs b ON cp.brief_id = b.id
    GROUP BY b.job_id
) job_costs ON job_costs.job_id = j.id
WHERE j.project = 'asi'
  AND j.created_at > NOW() - INTERVAL '30 days';
```

**Articles produced today:**
```sql
SELECT cp.region, cp.headline, cp.word_count, cp.status
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND DATE(j.created_at AT TIME ZONE 'UTC') = CURRENT_DATE
ORDER BY cp.region;
```

**Pending Slack approvals:**
```sql
SELECT
    j.topic,
    cp.region,
    cp.headline,
    cp.word_count,
    j.created_at AT TIME ZONE 'UTC' AS job_created
FROM content_pieces cp
JOIN briefs b ON cp.brief_id = b.id
JOIN jobs j ON b.job_id = j.id
WHERE j.project = 'asi'
  AND cp.status IN ('draft', 'human_review')
  AND j.status IN ('complete', 'partial')
ORDER BY j.created_at DESC;
```

**Average pipeline duration (completed jobs only):**
```sql
SELECT
    ROUND(AVG(EXTRACT(EPOCH FROM (completed_at - created_at)))) AS avg_seconds,
    ROUND(MIN(EXTRACT(EPOCH FROM (completed_at - created_at)))) AS min_seconds,
    ROUND(MAX(EXTRACT(EPOCH FROM (completed_at - created_at)))) AS max_seconds,
    COUNT(*) AS sample_size
FROM jobs
WHERE project = 'asi'
  AND status = 'complete'
  AND completed_at IS NOT NULL;
```
