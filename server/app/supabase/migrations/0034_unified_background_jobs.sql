-- 0031_unified_background_jobs.sql
-- Replace fragmented job tables with a single, well-designed jobs table.

BEGIN;

/* ---------------------------------------------------------------------- */
/* 1) Create new unified jobs table                                       */
/* ---------------------------------------------------------------------- */

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Job type (extensible, no CHECK constraint - validate in app)
    job_type TEXT NOT NULL,

    -- Status with clear lifecycle
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'running', 'completed', 'failed', 'cancelled')),

    -- Scheduling & priority
    priority INT NOT NULL DEFAULT 0,          -- higher = processed first
    run_at TIMESTAMPTZ,                       -- NULL = run immediately

    -- Retry logic
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,

    -- Scoping (all nullable - different jobs have different scopes)
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    companion_id UUID REFERENCES companions(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    external_user_id TEXT,                    -- end-user scope for actions

    -- Action-specific (for action_execution jobs)
    action_key TEXT,

    -- Flexible payload
    params JSONB NOT NULL DEFAULT '{}',       -- input parameters
    result JSONB,                             -- output/result data

    -- Error tracking
    error TEXT,                               -- final error message
    error_stack TEXT,                         -- stack trace for debugging

    -- Progress tracking (optional)
    total_items INT,
    processed_count INT DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,                   -- when worker claimed it
    started_at TIMESTAMPTZ,                   -- when execution started
    completed_at TIMESTAMPTZ,

    -- Worker tracking
    worker_id TEXT                            -- which worker is processing
);

-- Primary queue index: find pending jobs efficiently
CREATE INDEX idx_jobs_queue
    ON jobs (run_at NULLS FIRST, priority DESC, created_at)
    WHERE status = 'pending';

-- Find claimed/running jobs (for monitoring, timeouts)
CREATE INDEX idx_jobs_active
    ON jobs (status, claimed_at)
    WHERE status IN ('claimed', 'running');

-- Lookup by scope
CREATE INDEX idx_jobs_project ON jobs (project_id, created_at DESC) WHERE project_id IS NOT NULL;
CREATE INDEX idx_jobs_companion ON jobs (companion_id, created_at DESC) WHERE companion_id IS NOT NULL;
CREATE INDEX idx_jobs_conversation ON jobs (conversation_id, created_at DESC) WHERE conversation_id IS NOT NULL;
CREATE INDEX idx_jobs_owner ON jobs (owner_id, created_at DESC) WHERE owner_id IS NOT NULL;

-- Action-specific lookups
CREATE INDEX idx_jobs_action
    ON jobs (companion_id, external_user_id, action_key)
    WHERE action_key IS NOT NULL;

-- Prevent duplicate active jobs for same scope (optional, per job_type)
CREATE UNIQUE INDEX idx_jobs_unique_active_companion
    ON jobs (job_type, companion_id)
    WHERE companion_id IS NOT NULL AND status IN ('pending', 'claimed', 'running');

-- Updated_at trigger
CREATE OR REPLACE FUNCTION touch_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION touch_jobs_updated_at();

COMMENT ON TABLE jobs IS 'Unified job queue for all async operations: actions, webhooks, ingestion, etc.';
COMMENT ON COLUMN jobs.status IS 'pending=waiting, claimed=worker took it, running=executing, completed/failed/cancelled=terminal';
COMMENT ON COLUMN jobs.run_at IS 'Scheduled execution time. NULL means run immediately when a worker is available.';
COMMENT ON COLUMN jobs.worker_id IS 'Identifier of the worker processing this job (for debugging/monitoring).';

/* ---------------------------------------------------------------------- */
/* 2) Migrate data from existing tables                                   */
/* ---------------------------------------------------------------------- */

-- Migrate background_jobs
INSERT INTO jobs (
    id, job_type, status, priority,
    project_id, companion_id, conversation_id, owner_id,
    params, result, error,
    total_items, processed_count,
    created_at, started_at, completed_at, attempts
)
SELECT
    id,
    job_type,
    CASE status
        WHEN 'PENDING' THEN 'pending'
        WHEN 'RUNNING' THEN 'running'
        WHEN 'COMPLETED' THEN 'completed'
        WHEN 'FAILED' THEN 'failed'
        WHEN 'CANCELLED' THEN 'cancelled'
        ELSE 'pending'
    END,
    0,  -- priority
    NULL,  -- project_id (not in old table)
    companion_id,
    conversation_id,
    owner_id,
    COALESCE(params, '{}'),
    NULL,  -- result
    error,
    total_items,
    processed_count,
    created_at,
    started_at,
    completed_at,
    COALESCE(processed_count, 0)  -- use as proxy for attempts
FROM background_jobs
ON CONFLICT (id) DO NOTHING;

-- Migrate knowledge_ingestion_jobs
INSERT INTO jobs (
    id, job_type, status,
    project_id, companion_id, owner_id,
    params, error,
    created_at, started_at, completed_at
)
SELECT
    id,
    'knowledge_ingestion',
    CASE status
        WHEN 'queued' THEN 'pending'
        WHEN 'running' THEN 'running'
        WHEN 'succeeded' THEN 'completed'
        WHEN 'failed' THEN 'failed'
        ELSE 'pending'
    END,
    project_id,
    companion_id,
    submitted_by_user,
    jsonb_build_object(
        'source_type', source_type,
        'payload_ref', payload_ref,
        'asset_id', asset_id,
        'metadata', metadata
    ),
    error,
    created_at,
    started_at,
    completed_at
FROM knowledge_ingestion_jobs
ON CONFLICT (id) DO NOTHING;

/* ---------------------------------------------------------------------- */
/* 4) Create backwards-compatible views                                   */
/* ---------------------------------------------------------------------- */

CREATE OR REPLACE VIEW background_jobs_v AS
SELECT
    id,
    owner_id,
    job_type,
    CASE status
        WHEN 'pending' THEN 'PENDING'
        WHEN 'claimed' THEN 'RUNNING'
        WHEN 'running' THEN 'RUNNING'
        WHEN 'completed' THEN 'COMPLETED'
        WHEN 'failed' THEN 'FAILED'
        WHEN 'cancelled' THEN 'CANCELLED'
    END AS status,
    companion_id,
    conversation_id,
    created_at,
    started_at,
    completed_at,
    error,
    params,
    total_items,
    processed_count,
    0 AS error_count  -- not tracked in new schema
FROM jobs;

CREATE OR REPLACE VIEW knowledge_ingestion_jobs_v AS
SELECT
    id,
    project_id,
    companion_id,
    owner_id AS submitted_by_user,
    NULL::uuid AS submitted_by_key,
    (params->>'source_type')::text AS source_type,
    (params->>'payload_ref')::text AS payload_ref,
    (params->>'asset_id')::uuid AS asset_id,
    CASE status
        WHEN 'pending' THEN 'queued'
        WHEN 'claimed' THEN 'running'
        WHEN 'running' THEN 'running'
        WHEN 'completed' THEN 'succeeded'
        WHEN 'failed' THEN 'failed'
    END AS status,
    error,
    COALESCE(params->'metadata', '{}') AS metadata,
    created_at,
    updated_at,
    started_at,
    completed_at
FROM jobs
WHERE job_type = 'knowledge_ingestion';

/* ---------------------------------------------------------------------- */
/* 5) Rename old tables (keep for safety, drop later)                     */
/* ---------------------------------------------------------------------- */

ALTER TABLE IF EXISTS background_jobs RENAME TO _background_jobs_old;
ALTER TABLE IF EXISTS knowledge_ingestion_jobs RENAME TO _knowledge_ingestion_jobs_old;
ALTER TABLE IF EXISTS conversation_labeling_jobs RENAME TO _conversation_labeling_jobs_old;

COMMIT;
