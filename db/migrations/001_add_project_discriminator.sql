-- Migration 001: Add project discriminator column to jobs table
--
-- Purpose: ASI and ORACLE share the same Postgres instance.
--          This column prevents data bleed between projects.
--
-- Run once on the VPS before either project writes to jobs:
--   docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
--     -f /dev/stdin < ~/n8n-asi/asi/db/migrations/001_add_project_discriminator.sql
--
-- Safe to run multiple times — IF NOT EXISTS guards are used throughout.
-- Existing rows (from Sprint 1-2 testing) default to 'asi'.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS project VARCHAR(32) NOT NULL DEFAULT 'asi';

-- Backfill any rows created before this migration (all ASI)
UPDATE jobs SET project = 'asi' WHERE project IS NULL;

-- Index for fast per-project queries
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs (project);
