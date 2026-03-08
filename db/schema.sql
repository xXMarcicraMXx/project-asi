-- ASI Database Schema — Sprint 1
-- Run directly on VPS Postgres (no Alembic until Sprint 3)
-- Usage: psql $DATABASE_URL -f db/schema.sql

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- jobs — a single pipeline run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           TEXT NOT NULL,
    content_type    VARCHAR(64) NOT NULL,
    regions         TEXT[] NOT NULL,
    status          VARCHAR(32) DEFAULT 'running',      -- running | complete | failed
    config_snapshot JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- briefs — the publishable unit
-- MVP: one brief per region per job
-- Evolution 1: one brief per job (full daily brief)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS briefs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES jobs(id),
    title           TEXT,
    summary         TEXT,
    status          VARCHAR(32) DEFAULT 'draft',        -- draft | approved | rejected | human_review | published
    layout_config   JSONB,                              -- Evolution 2: agent-generated layout spec
    approved_by     VARCHAR(128),
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- content_pieces — the content atom, one regional perspective
-- MVP: one piece per brief (the article)
-- Evolution 1: N pieces per brief (regional sections of the daily brief)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS content_pieces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brief_id        UUID REFERENCES briefs(id),
    region          VARCHAR(64) NOT NULL,
    content_type    VARCHAR(64) NOT NULL,               -- 'regional_article' | 'brief_section'
    headline        TEXT,
    body            TEXT,
    word_count      INT,
    iteration_count INT DEFAULT 0,
    status          VARCHAR(32) DEFAULT 'draft',
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- agent_runs — full audit trail of every agent invocation
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_runs (
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

-- ---------------------------------------------------------------------------
-- feedback_loops — editor feedback per revision cycle
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback_loops (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_piece_id UUID REFERENCES content_pieces(id),
    iteration        INT NOT NULL,
    status           VARCHAR(16) NOT NULL,              -- 'approve' | 'revise'
    feedback         TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- layout_templates — created now, used in Evolution 2
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS layout_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(64) UNIQUE NOT NULL,
    description     TEXT,
    config_schema   JSONB,
    preview_url     TEXT,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_briefs_job_id ON briefs(job_id);
CREATE INDEX IF NOT EXISTS idx_content_pieces_brief_id ON content_pieces(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_content_piece_id ON agent_runs(content_piece_id);
CREATE INDEX IF NOT EXISTS idx_feedback_loops_content_piece_id ON feedback_loops(content_piece_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_content_pieces_status ON content_pieces(status);
