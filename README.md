# Project ASI

Autonomous multi-regional journal article engine.

## Setup

```bash
cp .env.example .env
# Fill in all values in .env

pip install -r requirements.txt
```

## Apply DB Schema (VPS)

```bash
psql $DATABASE_URL -f db/schema.sql
```

## Validate DB Connection

```bash
python scripts/validate_db.py
```

## Run a Job

```bash
python cli.py run --topic "EU elections" --regions EU LATAM SEA NA
```

## Deploy

Push to `main` — GitHub Actions handles the rest.
See `.github/workflows/deploy.yml`.
