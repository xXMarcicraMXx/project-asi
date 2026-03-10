# Project ASI

Autonomous multi-regional journal article engine.

Runs a daily cron job that researches a topic, writes four region-specific journal
articles (EU, LATAM, SEA, NA), posts them to Slack for human approval, and publishes
approved articles as markdown files.

---

## Architecture

```
APScheduler (daily cron)
  └─ run_pipeline(payload)
       ├─ RSSSource.fetch()            # research via RSS
       └─ asyncio.gather()             # all regions in parallel
            └─ AgentChain (per region)
                 ├─ ResearchAgent      # distil facts from RSS
                 ├─ WriterAgent        # draft article (+ Pinecone RAG context)
                 └─ EditorAgent        # approve or request revision (up to 4 iterations)
  └─ post_for_approval()              # Slack Block Kit buttons
  └─ MarkdownPublisher                # writes .md on approval

Webhook server (aiohttp, port 3000)
  POST /slack/interactive             # handles approve / reject callbacks
  GET  /health                        # Docker healthcheck
```

---

## Requirements

- Python 3.12
- PostgreSQL 14+
- Pinecone account (index `asi-personas`, 1024-dim, llama-text-embed-v2)
- Slack app with Bot Token + Signing Secret
- Anthropic API key

---

## Local setup

```bash
git clone <repo>
cd asi

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Fill in all values — see .env.example for descriptions
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `DATABASE_URL` | yes | `postgresql+asyncpg://user:pass@host:5432/dbname` |
| `PINECONE_API_KEY` | yes | Pinecone API key |
| `PINECONE_INDEX_NAME` | yes | `asi-personas` |
| `SLACK_BOT_TOKEN` | yes | `xoxb-…` Bot token |
| `ASI_SLACK_CHANNEL_ID` | yes | Channel to post approvals |
| `ASI_SLACK_SIGNING_SECRET` | yes | For webhook signature verification |
| `ASI_WEBHOOK_PORT` | no | Webhook server port (default: `3000`) |
| `ASI_OUTPUT_DIR` | no | Directory for published markdown files |

---

## Database migrations

```bash
# First deploy — stamp baseline then migrate up
alembic stamp 001
alembic upgrade head

# Subsequent deploys
alembic upgrade head
```

The migration version table is `alembic_version_asi` (isolated from any other
project sharing the same database).

---

## Run a job (CLI)

```bash
# Full 4-region run
python cli.py run --topic "EU elections" --regions EU LATAM SEA NA

# Single region, plain logs
python cli.py run --topic "AI regulation" --regions EU --log-plain

# Use custom source text instead of RSS
python cli.py run --topic "custom topic" --regions EU --source-text "paste text here"

# Save articles to disk
python cli.py run --topic "trade war" --regions EU NA --output-dir ./output

# Validate config + sources without calling any agents (no tokens, no DB writes)
python cli.py run --topic "test topic" --regions EU NA --dry-run
```

---

## Docker deployment

```bash
# Build and start
docker compose up -d --build

# View logs
docker compose logs -f asi-app

# Health check
curl http://localhost:3000/health

# Trigger a manual pipeline run
docker compose exec asi-app python -c "
import asyncio
from orchestrator.scheduler import run_scheduled_job
asyncio.run(run_scheduled_job())
"
```

### docker-compose.yml (minimal example)

```yaml
services:
  asi-app:
    build: .
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://asi:${POSTGRES_PASSWORD}@postgres:5432/asi
    ports:
      - "3000:3000"
    depends_on:
      - postgres
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  postgres:
    image: postgres:16
    env_file: .env
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

---

## Scheduler

The scheduler fires once daily at the time set in `config/settings.yaml`
(`scheduler.cron`, default `0 7 * * *` UTC). It round-robins through
`scheduler.default_topics` by day-of-year.

To change the schedule, edit `config/settings.yaml` and restart the container.

---

## Slack approval gate

After each pipeline run, one Slack message per region is posted with:
- Headline and a 200-word excerpt
- ✅ Approve and ❌ Reject buttons

Approving a piece writes the full article as a markdown file to `ASI_OUTPUT_DIR`
(or `/app/output` inside the container) and sets `content_pieces.status = approved`.

The webhook server must be reachable from Slack. On VPS deployments expose port 3000
and configure the Slack app's Interactivity Request URL to
`https://<your-domain>:3000/slack/interactive`.

---

## Ingestion (Pinecone personas)

```bash
python ingestion/run_ingestion.py
```

Uploads persona guideline and golden sample documents for each region into the
`asi-personas` Pinecone index. Run once after initial setup or when personas change.

---

## Validation scripts

| Script | Purpose |
|---|---|
| `python scripts/validate_day19.py` | Full end-to-end pipeline + scheduler |
| `python scripts/validate_day19.py --skip-pipeline` | Config + topic rotation only |
| `python scripts/validate_day20.py` | Final hardening checks |

---

## Configuration

All config lives in `config/`:

| File | Description |
|---|---|
| `settings.yaml` | Models, Pinecone index, cost ceiling, logging, scheduler |
| `content_types/journal_article.yaml` | Word limits, agent chain, editor criteria |
| `regions/europe.yaml` etc. | Regional editorial voice, demographic anchor, Pinecone metadata |
