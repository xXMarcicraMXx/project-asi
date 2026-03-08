# Project ASI — Master Technical Specification
**Version:** 3.0
**Last Updated:** March 2026
**Status:** Pre-Development — Sprint Planning Complete

---

## 1. Project Vision

Project ASI is a **code-native, autonomous content generation platform** that produces multi-perspective media assets through a pipeline of specialized AI agents. The system is orchestrated entirely in Python — no visual workflow tools (n8n replaced).

**Core thesis:** The same news story, told through the editorial lens of different regions and cultures, creates a richer and more valuable media product than a single authoritative take.

### MVP Objective
Validate the product structure: can the system reliably produce N distinct, high-quality articles on the same topic — each written from a different regional editorial perspective — with minimal human intervention beyond a single approval click?

**The MVP is not about content volume or publishing scale. It is about proving the pipeline works and the regional differentiation is real and valuable.**

### Design Principles
- **Flexible by design:** Content type (journal article today, daily brief tomorrow) is a swappable YAML config — zero code changes to switch
- **Agent-first:** Every transformation step is owned by a discrete, persona-driven AI agent
- **Fail-safe:** Loops are bounded; unresolved conflicts escalate to humans automatically
- **Evolvable schema:** Database designed to accommodate the two planned evolutions without structural rework
- **Self-documenting:** All state, decisions, and agent exchanges persisted to PostgreSQL

---

## 2. Scope

### In Scope — 20-Day MVP
- Multi-regional journal article generation (one topic → N articles, one per region)
- Three-agent pipeline: ResearchAgent → WriterAgent → EditorAgent (with revision loop)
- YAML-driven configuration for content types and regional voices
- PostgreSQL state ledger with full agent audit trail
- Slack-based human approval gate (one-click approve/reject per article)
- Markdown file output for approved articles
- Deployment on existing VPS via Docker Compose
- CLI trigger and daily scheduler

### Explicitly Out of Scope — MVP
- Daily brief format (single coherent multi-region publication) — **Evolution 1**
- Agent-managed visual website layout — **Evolution 2**
- Video or image generation
- Twitter/X publishing
- Ghost CMS or any CMS integration
- Gemini evaluation committee (self-optimising layer)
- Multi-user access control
- Automated topic discovery
- Analytics or performance tracking
- Async parallel region execution (sequential first, upgrade in Sprint 3)

### Planned Evolutions (Post-MVP)

**Evolution 1 — Daily Brief Format**
The product pivots from N standalone regional articles to a single cohesive daily brief. One publishable unit per job, composed of regional sections that read together as a curated overview of what matters globally. The database schema is already designed for this — `briefs` and `content_pieces` tables support both formats without migration. Switching requires only a new content type YAML config and a brief-assembly step in the pipeline.

**Evolution 2 — Agent-Managed Website with Dynamic Visual Layouts**
A website where the visual layout of each published brief is partially determined by an AI agent. The LayoutAgent does not write CSS — it selects from a library of 3–5 pre-built React/Next.js templates and configures a bounded set of visual parameters (template name, accent palette, section order, typography weight) based on the topic's register (crisis, markets, culture, politics). The website reads the `layout_config` JSON from the API and renders accordingly. Each region can also carry its own visual identity. The `layout_templates` table and `briefs.layout_config` column are already in the schema waiting for this.

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        ENTRY LAYER                              │
│   CLI  /  APScheduler (daily cron)                              │
└───────────────────────────────┬─────────────────────────────────┘
                                │ JobPayload
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR CORE                          │
│   orchestrator/pipeline.py                                      │
│   Loads ContentTypeConfig + RegionConfigs                       │
│   Runs one AgentChain per region (sequential, Sprint 1–2)       │
│   Upgrades to asyncio.gather in Sprint 3                        │
└───────────────────────────────┬─────────────────────────────────┘
                                │ RegionTask × N (sequential)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AGENT CHAIN                              │
│   agents/chain.py                                               │
│                                                                 │
│   ① ResearchAgent  — summarises sources → ResearchBrief         │
│   ② WriterAgent    — produces draft using persona + config      │
│   ③ EditorAgent    — structured JSON verdict (approve/revise)   │
│                      loop ≤ 3 iterations                        │
│                      iteration 4 → status: human_review         │
└───────────────────────────────┬─────────────────────────────────┘
                                │ content_piece rows
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    STATE LEDGER (PostgreSQL)                     │
│   jobs / briefs / content_pieces / agent_runs / feedback_loops  │
│   layout_templates (created now, used in Evolution 2)           │
└──────────────────────┬──────────────────────┬───────────────────┘
                       │                      │
                       ▼                      ▼
        ┌──────────────────────┐   ┌─────────────────────────┐
        │    APPROVAL GATE     │   │     RAG LAYER           │
        │    Slack Bot         │   │     Pinecone            │
        │    Block Kit UI      │   │     Personas + templates│
        │    Approve / Reject  │   │     injected into       │
        └──────────┬───────────┘   │     WriterAgent         │
                   │ Approved      └─────────────────────────┘
                   ▼
        ┌──────────────────────┐
        │     PUBLISHER        │
        │  /output/{job}/{r}.md│
        └──────────────────────┘
```

---

## 4. Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.12 | Async support, Anthropic SDK, broad ecosystem |
| LLM | Anthropic Claude API | Prompt caching on static system prompts; structured outputs |
| LLM — Writing | Claude Sonnet | Full creative capacity for article drafting and editing |
| LLM — Parsing | Claude Haiku | Fast and cheap for research summarisation and JSON parsing |
| Vector DB | Pinecone | Persona and template RAG with metadata filtering |
| Primary DB | PostgreSQL 16 | State ledger; already running on VPS |
| HTTP Client | httpx | Async-native |
| Config | Pydantic v2 + YAML | Validated, version-controlled configuration |
| Scheduler | APScheduler | Cron-style triggers, no separate service needed |
| Approval Gate | Slack Bot (Block Kit) | Zero new infrastructure; one-click approve/reject |
| Containerisation | Docker Compose | Already running on VPS; add one service |
| News Source | RSS via feedparser | Free, no API key, full article body |
| Version Control | GitHub | See Section 7 |
| Secrets | `.env` + python-dotenv | Consistent with existing VPS setup |

---

## 5. Repository Structure

```
asi/
├── .github/
│   └── workflows/
│       └── deploy.yml          # Auto-deploy to VPS on push to main
├── .gitignore
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml          # Adds asi-app to existing stack
├── .env.example                # Template — committed; .env is never committed
│
├── config/
│   ├── settings.yaml           # Global: model names, Pinecone index, cost ceiling, log level
│   ├── content_types/
│   │   ├── journal_article.yaml
│   │   └── daily_brief.yaml    # Created in Evolution 1 — no code change required
│   └── regions/
│       ├── europe.yaml
│       ├── latam.yaml
│       ├── southeast_asia.yaml
│       └── north_america.yaml
│
├── orchestrator/
│   ├── __init__.py
│   ├── pipeline.py             # Entry point: loads config, runs agent chains
│   ├── job_model.py            # Pydantic: JobPayload, RegionTask, ResearchBrief, ArticleDraft, EditorVerdict
│   └── scheduler.py            # APScheduler daily cron
│
├── agents/
│   ├── __init__.py
│   ├── base_agent.py           # Abstract: static system prompt, run(), token tracking, retry/backoff
│   ├── chain.py                # Loop controller: iteration count, escalation logic
│   ├── research_agent.py       # Produces ResearchBrief from source articles
│   ├── writer_agent.py         # Produces draft using persona + content type config + RAG
│   └── editor_agent.py         # Returns structured JSON verdict
│
├── rag/
│   ├── __init__.py
│   ├── pinecone_client.py      # Upsert + metadata-filtered retrieval
│   ├── ingestion.py            # extract → chunk → tag → embed → upsert pipeline
│   └── schemas.py              # Pinecone metadata schema constants
│
├── data_sources/
│   ├── __init__.py
│   ├── base_source.py          # Abstract DataSource: fetch(topic) → list[Article]
│   └── rss_source.py           # RSS via feedparser; fallback: --source-text CLI flag
│
├── db/
│   ├── __init__.py
│   ├── models.py               # SQLAlchemy async ORM — 6 tables
│   ├── session.py              # Async session factory (asyncpg)
│   └── migrations/             # Alembic (introduced Sprint 3 when schema stabilises)
│
├── approval/
│   ├── __init__.py
│   └── slack_bot.py            # Slack Block Kit: post articles, handle approve/reject callbacks
│
├── publishers/
│   ├── __init__.py
│   ├── base_publisher.py       # Abstract Publisher interface
│   └── markdown_publisher.py   # Writes /output/{job_id}/{region}.md
│
├── ingestion/
│   └── run_ingestion.py        # CLI: seeds Pinecone with personas and golden samples
│
└── cli.py                      # python cli.py run --topic "..." --regions EU LATAM
```

---

## 6. Configuration System

Content production logic is entirely driven by YAML files. Switching content types or adding regions requires no code changes.

### 6.1 `config/settings.yaml`
```yaml
models:
  writer: claude-sonnet-4-20250514
  parser: claude-haiku-4-5-20251001

pinecone:
  index_name: asi-personas
  top_k: 2

cost:
  max_usd_per_job: 2.00       # Hard ceiling — job aborts if exceeded

logging:
  level: INFO
  format: json

scheduler:
  cron: "0 7 * * *"           # 07:00 UTC daily
  default_regions: [EU, LATAM, SEA, NA]
```

### 6.2 `config/content_types/journal_article.yaml`
```yaml
content_type: journal_article

output:
  format: markdown
  min_words: 600
  max_words: 1200

agent_chain:
  - research_agent
  - writer_agent
  - editor_agent

writer_instructions: |
  Write a formal journal article using markdown section headers (##).
  Structure: introduction, 3–4 body paragraphs, conclusion.
  Cite sources inline as [Source Name].

editor_criteria:
  - Word count is between 600 and 1200 words
  - Regional editorial voice is consistent throughout
  - No invented statistics or fabricated quotes
  - Headline is specific, not generic
  - Article reads as a standalone publishable piece

pinecone_filter:
  document_type: persona_guideline
```

### 6.3 `config/regions/europe.yaml`
```yaml
region_id: EU
display_name: Europe

editorial_voice: |
  You are a senior journalist writing for a pan-European broadsheet.
  Your perspective is shaped by EU institutional frameworks, multilateralism,
  and scepticism of unilateral action by any single power bloc.
  Tone: measured, analytical, formally structured.
  Avoid: American idioms, excessive informality, sensationalist framing.

demographic_anchor:
  location: Brussels
  cultural_lens: Western European liberal tradition

pinecone_metadata:
  department: editorial_EU
```

---

## 7. Version Control & GitHub Setup

### Repository
- **GitHub repo:** `your-org/project-asi` (private)
- **Default branch:** `main` — production-ready code only
- **Development branch:** `develop` — integration branch for features

### Branching Strategy
```
main          ← production; deploy triggers on push
  └─ develop  ← integration branch
       └─ feature/D1-db-schema
       └─ feature/D2-config-system
       └─ feature/D3-rss-source
       └─ feature/D5-agent-pipeline
       ...
```
Each deliverable gets its own feature branch. Merge into `develop` when complete. Merge `develop` into `main` at the end of each sprint.

### Commit Convention
```
type(scope): short description

Types: feat | fix | config | refactor | test | docs | chore

Examples:
feat(agents): add EditorAgent structured output parser
fix(db): correct asyncpg connection pool timeout
config(regions): add Southeast Asia editorial voice
docs(spec): update decision log sprint 2 findings
```

### `.gitignore` — Critical Entries
```
.env                    # Never committed — secrets live here
__pycache__/
*.pyc
.venv/
output/                 # Generated article files
*.log
.DS_Store
```

### `.env.example` — Committed Template
```
# Anthropic
ANTHROPIC_API_KEY=

# Pinecone
PINECONE_API_KEY=
PINECONE_INDEX_NAME=asi-personas

# PostgreSQL (matches existing docker-compose values)
POSTGRES_USER=
POSTGRES_PASSWORD=
POSTGRES_DB=
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}

# Slack
SLACK_BOT_TOKEN=
SLACK_CHANNEL_ID=

# News
RSS_FEED_URLS=https://feeds.bbci.co.uk/news/rss.xml,https://rss.nytimes.com/services/xml/rss/nyt/World.xml

# ASI
ASI_OUTPUT_DIR=/data/output
ASI_MAX_USD_PER_JOB=2.00
```

### GitHub Actions — Auto-Deploy on Push to Main
```yaml
# .github/workflows/deploy.yml
name: Deploy to VPS

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: SSH and pull
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd ~/n8n-asi
            git pull origin main
            sudo docker compose up -d --build asi-app
```
Add `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` as GitHub repository secrets.

---

## 8. Database Schema

Designed to support the MVP and both planned evolutions without structural rework.

```sql
-- A single pipeline run
CREATE TABLE jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           TEXT NOT NULL,
    content_type    VARCHAR(64) NOT NULL,
    regions         TEXT[] NOT NULL,
    status          VARCHAR(32) DEFAULT 'running',   -- running | complete | failed
    config_snapshot JSONB,       -- full YAML config at run time (reproducibility)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- The publishable unit
-- MVP: one brief per region per job
-- Evolution 1: one brief per job (the full daily brief)
CREATE TABLE briefs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES jobs(id),
    title           TEXT,
    summary         TEXT,
    status          VARCHAR(32) DEFAULT 'draft',  -- draft | approved | rejected | human_review | published
    layout_config   JSONB,       -- Evolution 2: agent-generated layout spec
    approved_by     VARCHAR(128),
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- The content atom — one regional perspective
-- MVP: one piece per brief (the article)
-- Evolution 1: N pieces per brief (regional sections of the daily brief)
CREATE TABLE content_pieces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brief_id        UUID REFERENCES briefs(id),
    region          VARCHAR(64) NOT NULL,
    content_type    VARCHAR(64) NOT NULL,   -- 'regional_article', 'brief_section'
    headline        TEXT,
    body            TEXT,
    word_count      INT,
    iteration_count INT DEFAULT 0,
    status          VARCHAR(32) DEFAULT 'draft',
    metadata        JSONB,       -- flexible: source_urls, reading_time, tags, etc.
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Full audit trail of every agent invocation
CREATE TABLE agent_runs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_piece_id UUID REFERENCES content_pieces(id),
    agent_name       VARCHAR(64) NOT NULL,
    iteration        INT DEFAULT 1,
    input_tokens     INT,
    output_tokens    INT,
    cost_usd         NUMERIC(10,6),
    duration_ms      INT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Editor feedback per revision cycle
CREATE TABLE feedback_loops (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_piece_id UUID REFERENCES content_pieces(id),
    iteration        INT NOT NULL,
    status           VARCHAR(16) NOT NULL,   -- 'approve' | 'revise'
    feedback         TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Layout template library — created now, used in Evolution 2
CREATE TABLE layout_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) UNIQUE NOT NULL,
    description     TEXT,           -- natural language, readable by the LayoutAgent
    config_schema   JSONB,          -- valid parameters and allowed values
    preview_url     TEXT,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Schema Evolution Map

| Future Change | Schema Impact |
|---|---|
| Switch to daily brief format | New content_type YAML only — zero schema change |
| Add a new region | New YAML file only — zero schema change |
| Add a new content type | New YAML file only — zero schema change |
| LayoutAgent (Evolution 2) | `briefs.layout_config` and `layout_templates` already exist |
| Cost reporting | `agent_runs.cost_usd` already tracked per call |
| New metadata fields | `content_pieces.metadata` JSONB absorbs anything new |
| Reproduce a past run | `jobs.config_snapshot` stores full config at run time |

---

## 9. Agent Specifications

### Agent Design Rules (All Agents)
- System prompt is **fully static** — no variable injection — to enable Anthropic prompt caching
- Regional context and task details are always injected into the **user message only**
- Every call is logged to `agent_runs` with token counts and cost
- Exponential backoff: 3 retries on 429/529 before raising
- Hard cost check against `ASI_MAX_USD_PER_JOB` before each call

### ResearchAgent
- **Model:** Claude Haiku (parsing task, not creative)
- **Input:** List of `Article` objects from RSS source
- **Task:** Extract key facts, data points, direct quotes, conflicting perspectives
- **Output:** `ResearchBrief` Pydantic model (structured, not free text)

### WriterAgent
- **Model:** Claude Sonnet
- **Input:** `ResearchBrief` + region config editorial voice + content type format instructions + RAG-retrieved persona doc (Sprint 3+)
- **Task:** Produce a complete draft matching format and regional voice
- **Output:** Raw markdown article text
- **Note:** In Sprints 1–2, persona comes entirely from YAML. RAG enriches this from Sprint 3.

### EditorAgent
- **Model:** Claude Sonnet
- **Input:** Draft text + editor criteria from content type config
- **Task:** Evaluate draft against all criteria
- **Output:** Strict JSON — `{"status": "approve" | "revise", "feedback": "..."}`
- **Loop cap:** 3 iterations maximum; on breach → `content_piece.status = human_review`

---

## 10. Pinecone Metadata Schema

Every document in Pinecone must carry these four keys:

```json
{
  "department":    "e.g. 'editorial_EU', 'editorial_LATAM'",
  "document_type": "e.g. 'persona_guideline', 'golden_sample', 'formatting_template'",
  "content_type":  "e.g. 'journal_article', 'daily_brief'",
  "access_level":  "'public' | 'internal_only'"
}
```

All RAG retrievals filter on `department` + `document_type` + `content_type` simultaneously to prevent cross-region contamination.

Seed documents per region (8 total for MVP):
- 1 × `persona_guideline` per region — defines the editorial voice in depth
- 1 × `golden_sample` per region — a hand-written exemplar article in that voice

---

## 11. VPS Deployment

The `asi-app` service runs alongside the existing n8n/Postgres/Caddy stack.

### `Dockerfile` (in `asi/`)
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "orchestrator/scheduler.py"]
```

### Addition to `docker-compose.yml`
```yaml
  asi-app:
    build: ./asi
    restart: always
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - PINECONE_API_KEY=${PINECONE_API_KEY}
      - PINECONE_INDEX_NAME=${PINECONE_INDEX_NAME}
      - SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN}
      - SLACK_CHANNEL_ID=${SLACK_CHANNEL_ID}
      - RSS_FEED_URLS=${RSS_FEED_URLS}
      - ASI_OUTPUT_DIR=/data/output
      - ASI_MAX_USD_PER_JOB=${ASI_MAX_USD_PER_JOB}
    volumes:
      - asi_output:/data/output
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  asi_output:
```

### New `.env` additions (append to existing file)
```
ANTHROPIC_API_KEY=sk-ant-...
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=asi-personas
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
RSS_FEED_URLS=https://feeds.bbci.co.uk/news/rss.xml,https://rss.nytimes.com/services/xml/rss/nyt/World.xml
ASI_MAX_USD_PER_JOB=2.00
```

---

## 12. 20-Day Sprint Plan

```
WEEK 1 (Days 1–5)    Foundation      → DB + config + Claude wrapper wired together
WEEK 2 (Days 6–11)   Core Loop       → Full single-region pipeline end-to-end
WEEK 3 (Days 12–16)  Scale Out       → Multi-region + RAG integration
WEEK 4 (Days 17–20)  Ship            → Slack approval + Docker deploy + scheduler
```

---

### Sprint 1 — Foundation (Days 1–5)
**Goal:** Accept a topic and produce one article for one region. No agent loop yet. Prove all components connect.

**Day 1 — Repo & Shared Models**
- GitHub repo initialised: `main` + `develop` branches, `.gitignore`, `.env.example`
- Feature branch: `feature/D1-foundation`
- **Pydantic shared models** (the most important file in the project — every deliverable depends on these contracts):
  - `JobPayload`, `RegionTask`, `Article`, `ResearchBrief`, `ArticleDraft`, `EditorVerdict`
- Raw SQL schema created directly on VPS Postgres (no Alembic yet): all 6 tables
- `db/session.py` asyncpg connection pool
- Validate: connection test script connects and queries successfully

**Day 2 — Config System**
- `config/settings.yaml`
- `config/content_types/journal_article.yaml`
- `config/regions/` — EU, LATAM, SEA, NA
- Pydantic config loaders with strict validation (fail fast on bad config)
- Validate: `python -c "from config import load_all; load_all()"` exits clean

**Day 3 — Claude API Wrapper**
- `agents/base_agent.py`:
  - Static system prompt (prompt caching compliant)
  - `run(user_message: str) → str`
  - Token counting + `agent_runs` DB write
  - Exponential backoff: 3 retries on 429/529
  - Cost ceiling check before each call
- Validate: single Haiku call logs correctly to `agent_runs` table

**Day 4 — RSS Data Source**
- `data_sources/rss_source.py` — `fetch(topic: str) → list[Article]`
  - Searches headlines across configured RSS feeds
  - Returns full article body via httpx fetch of article URL
  - Fallback: `--source-text` CLI flag for manual text input
- Validate: `fetch("interest rates")` returns ≥3 articles with full body text

**Day 5 — Smoke Test + Buffer**
- Wire straight line: CLI → load config → fetch sources → one Claude call → write to DB → print output
- Fix integration bugs
- Merge `feature/D1-foundation` → `develop`
- **Milestone:** `python cli.py run --topic "EU elections" --region EU` → article text printed, row in `content_pieces` table ✅

---

### Sprint 2 — Core Loop (Days 6–11)
**Goal:** Full single-region pipeline: Research → Write → Edit (with revision loop) → approved draft saved.

**Day 6 — ResearchAgent**
- Feature branch: `feature/D5-agents`
- `agents/research_agent.py`
- Input: `list[Article]` from RSS source
- Output: structured `ResearchBrief` (facts, quotes, data points — not free text)
- Model: Haiku
- Validate: `ResearchBrief` contains ≥5 distinct facts

**Day 7 — WriterAgent**
- `agents/writer_agent.py`
- Input: `ResearchBrief` + region config editorial voice + content type format instructions
- **No RAG yet** — all persona context from YAML only (unblocks this sprint)
- Model: Sonnet
- Validate: output ≥600 words, uses markdown headers, voice matches region config

**Day 8 — EditorAgent + Loop**
- `agents/editor_agent.py` — structured JSON output
- `agents/chain.py` — loop controller:
  - Passes `EditorVerdict.feedback` back to WriterAgent on revise
  - Hard cap at 3 iterations
  - Iteration 4 → `content_piece.status = human_review`, stop loop
- Validate: force a bad draft → confirm loop triggers, revision improves output, DB logs each iteration

**Day 9 — Regional Voice Validation**
⚠️ **The most important day in the project — product validation, not engineering.**
- Run full pipeline for EU, LATAM, and NA on the same topic
- Read all three articles side by side
- Core question: **are the articles meaningfully and visibly different?**
- If YES → proceed
- If NO → Day 10 is reserved to strengthen region configs until differentiation is real

**Day 10 — Refinement Buffer**
- Strengthen regional YAML configs based on Day 9 findings
- Add structured logging (JSON format, log level from settings.yaml)
- Cost report: print actual token usage + USD per run
- Fix any loop edge cases found in Day 8

**Day 11 — Sprint 2 Buffer + Milestone**
- Merge `feature/D5-agents` → `develop`
- **Milestone:** `python cli.py run --topic "AI regulation" --region EU` → approved article in `/output/`, all agent iterations logged in DB, regional voice is distinctly European ✅

---

### Sprint 3 — Scale Out (Days 12–16)
**Goal:** Four regions in one command. RAG adds persona depth.

**Day 12 — Multi-Region (Sequential)**
- Feature branch: `feature/D6-orchestrator`
- `orchestrator/pipeline.py` — loop over regions sequentially
- All `content_pieces` from same job linked by `brief_id` and `job_id`
- Validate: `python cli.py run --topic "..." --regions EU LATAM SEA NA` → 4 distinct articles

**Day 13 — Pinecone Ingestion**
- Feature branch: `feature/D4-rag`
- `rag/pinecone_client.py` + `rag/ingestion.py`
- `ingestion/run_ingestion.py` — seed: 4 persona docs (one per region) + 4 golden sample articles
- Validate: filtered query `department=editorial_EU, document_type=golden_sample` returns correct document

**Day 14 — RAG Integration**
- Connect Pinecone retrieval into WriterAgent (top-K: 2 documents)
- Inject into user message only (preserves system prompt cache)
- Validate: subjective quality check — does the article improve vs. YAML-only baseline?

**Day 15 — Async Fan-Out**
- Upgrade `pipeline.py` to `asyncio.gather()` for parallel region execution
- Error isolation: one region failing does not cancel others
- Validate: 4-region run completes in less time; all 4 rows in DB

**Day 16 — Sprint 3 Buffer + Alembic**
- Introduce Alembic now that schema is stable: generate initial migration from existing tables
- Performance: measure wall-clock time for 4-region job (target <3 min)
- Cost audit: measure actual cost per full job
- Merge both feature branches → `develop`
- **Milestone:** Full 4-region job in <3 minutes, cost <$1.00, articles readable ✅

---

### Sprint 4 — Ship (Days 17–20)
**Goal:** Live on VPS, Slack approval working, system runs unattended.

**Day 17 — Slack Approval Gate**
- Feature branch: `feature/D7-approval`
- `approval/slack_bot.py`:
  - On job completion: post one message per `content_piece` with headline + 200-word excerpt
  - Block Kit buttons: ✅ Approve / ❌ Reject
  - On approve: call `markdown_publisher.py` → write `/output/{job_id}/{region}.md`
  - On reject: update DB status, log reason if provided
- Validate: end-to-end test → Slack click → markdown file appears in output dir

**Day 18 — Docker Deployment**
- Write `asi/Dockerfile`
- Add `asi-app` service to `docker-compose.yml`
- Append new vars to `.env` on VPS
- `sudo docker compose up -d --build`
- Validate: `docker compose logs asi-app` shows clean startup, DB connects

**Day 19 — Scheduler + End-to-End Test**
- `orchestrator/scheduler.py` — APScheduler cron job from `settings.yaml`
- Default topic list in `settings.yaml` for unattended runs
- Full end-to-end on live VPS: scheduler fires → 4 articles → Slack message → approve → markdown files saved
- Fix any production environment issues

**Day 20 — Hardening & Handoff**
- `--dry-run` flag: validates config and connectivity without calling Claude API
- `README.md` with setup, deploy, and usage instructions
- Merge all branches → `develop` → `main` → triggers GitHub Actions deploy
- Final cost report: actual USD per daily run
- Update Section 14 (Decision Log) with sprint findings
- **Final milestone:** System runs unattended for 24 hours, produces articles, surfaces them in Slack ✅

---

## 13. Risk Register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Regional voice differentiation is too subtle | Medium | Critical | Day 9 validation gate; Day 10 buffer to strengthen configs |
| RSS article body fetch fails (paywalls) | High | Medium | `--source-text` fallback built on Day 4; rotate RSS sources |
| Anthropic API latency spike | Medium | Medium | Exponential backoff on Day 3; async isolation on Day 15 |
| Pinecone setup takes longer than expected | Low | Low | RAG is additive (Days 13–14); skip and retry if behind |
| Slack Bot webhook adds unexpected complexity | Low | Medium | Fallback: write articles to `/output/` only, skip Slack for MVP |
| Cost exceeds ceiling mid-job | Low | Medium | Hard ceiling check in `base_agent.py` before every call |
| Sprint 2 article quality insufficient | Low | High | Day 10 and Day 11 buffer exist for exactly this |

---

## 14. Decision Log

| Date | Decision | Rationale |
|---|---|---|
| Mar 2026 | Replaced n8n with Python orchestrator | Full version control, no visual tool dependency, easier testing and debugging |
| Mar 2026 | Pinecone retained from v1 design | RAG and metadata filtering architecture is sound; carries forward unchanged |
| Mar 2026 | Slack approval gate replaces FastAPI UI | Saves 1.5 days; zero new infrastructure; maps to original ASI approval pattern |
| Mar 2026 | RSS feeds replace NewsAPI as primary source | Free; no API key; returns full article body; NewsAPI remains an optional future adapter |
| Mar 2026 | Sequential region execution first, asyncio in Sprint 3 | Eliminates race conditions and partial DB write bugs during pipeline development; upgrade is a one-line change |
| Mar 2026 | Personas in YAML for Sprints 1–2; RAG additive in Sprint 3 | Removes Pinecone as a blocker for agent development; same end quality, two weeks earlier |
| Mar 2026 | Daily brief format deferred to Evolution 1 | MVP validates pipeline structure and regional differentiation first; schema already designed to support briefs without migration |
| Mar 2026 | Agent-managed website layout deferred to Evolution 2 | Premature for MVP; `layout_config` column and `layout_templates` table already in schema; LayoutAgent added post-validation |
| Mar 2026 | `articles` table renamed to `content_pieces` | More neutral entity name; supports both standalone articles (MVP) and brief sections (Evolution 1) without schema change |
| Mar 2026 | `briefs` table added as publishable unit above `content_pieces` | Enables clean Evolution 1 pivot; in MVP, one brief per region; in Evolution 1, one brief per job |
| Mar 2026 | Alembic introduced in Sprint 3, not Day 1 | Schema stabilises during Sprint 2; introducing migrations before the schema is confirmed adds friction without benefit |

---

## 15. Claude Code Session Prompt

Paste this block at the start of every Claude Code session:

```
Project: ASI — autonomous multi-regional journal article engine.
Spec document: PROJECT_ASI_MASTER_SPEC.md (attached or in Google Drive)

Current task: [DELIVERABLE NAME]
Branches already merged to develop: [list completed feature branches]

Tech stack: Python 3.12, FastAPI, SQLAlchemy async, Pydantic v2,
            Anthropic SDK, Pinecone SDK, asyncio, APScheduler,
            Docker, feedparser, httpx.

Key constraints:
- All agent system prompts must be fully static (no variable injection)
  to enable Anthropic prompt caching.
- Regional context always goes into the user message, never system prompt.
- All config-driven: content types and regions live in /config/ YAML.
- Every agent call must be logged to agent_runs table with token counts and cost_usd.
- Editor loop hard cap: 3 iterations, then set status human_review and stop.
- .env is never committed — use .env.example as the template.
- Branching: feature/[name] → develop → main (main triggers auto-deploy).

VPS: Ubuntu 24.04, Docker Compose already running (n8n + Postgres + Caddy).
Repo root on VPS: ~/n8n-asi/asi/
```
