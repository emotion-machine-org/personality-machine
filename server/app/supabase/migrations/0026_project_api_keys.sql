-- 0026_project_api_keys.sql
-- Introduce projects (multi-companion scope), project API keys, profile schemas,
-- and knowledge ingestion job tracking.

/* ---------- PROJECTS & ASSOCIATIONS ------------------------------------- */

CREATE TABLE IF NOT EXISTS projects (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE,
    is_default      BOOLEAN NOT NULL DEFAULT FALSE,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id, created_at DESC);

-- Ensure project names are unique per owner (case-insensitive)
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_owner_name_unique
    ON projects(owner_id, lower(name));

CREATE TABLE IF NOT EXISTS project_companions (
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    companion_id    UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, companion_id)
);

CREATE INDEX IF NOT EXISTS idx_project_companions_companion
    ON project_companions(companion_id);

/* ---------- PROJECT API KEYS -------------------------------------------- */

CREATE TABLE IF NOT EXISTS project_api_keys (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    name            TEXT,
    prefix          TEXT NOT NULL,
    secret_hash     BYTEA NOT NULL,
    salt            BYTEA NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active', -- active | revoked
    scopes          TEXT[] NOT NULL DEFAULT ARRAY['read','write'],
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_api_keys_prefix
    ON project_api_keys(prefix);

CREATE INDEX IF NOT EXISTS idx_project_api_keys_project
    ON project_api_keys(project_id, status);

/* ---------- COMPANION USER PROFILE SCHEMAS ----------------------------------- */

CREATE TABLE IF NOT EXISTS companion_user_profile_schemas (
    companion_id    UUID PRIMARY KEY REFERENCES companions(id) ON DELETE CASCADE,
    project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    schema          JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      UUID REFERENCES users(id) ON DELETE SET NULL
);

/* ---------- KNOWLEDGE INGESTION JOBS ------------------------------------ */

CREATE TABLE IF NOT EXISTS knowledge_ingestion_jobs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    companion_id        UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    submitted_by_user   UUID REFERENCES users(id) ON DELETE SET NULL,
    submitted_by_key    UUID REFERENCES project_api_keys(id) ON DELETE SET NULL,
    source_type         TEXT NOT NULL, -- text | markdown | json | key | url
    payload_ref         TEXT,
    status              TEXT NOT NULL DEFAULT 'queued', -- queued | running | succeeded | failed
    error               TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_companion
    ON knowledge_ingestion_jobs(companion_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_jobs_project_status
    ON knowledge_ingestion_jobs(project_id, status, created_at DESC);

/* ---------- DATA BACKFILL ------------------------------------------------ */

-- Create a default project for every existing user (if not present).
WITH existing_defaults AS (
    SELECT owner_id FROM projects WHERE is_default = TRUE
)
INSERT INTO projects (id, owner_id, name, slug, is_default, metadata)
SELECT uuid_generate_v4(),
       u.id,
       'Default Project',
       'default-' || left(u.id::text, 8),
       TRUE,
       jsonb_build_object('seeded_at', now())
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM existing_defaults ed WHERE ed.owner_id = u.id
);

-- Link all companions to their owner's default project.
WITH default_projects AS (
    SELECT owner_id, id AS project_id
    FROM projects
    WHERE is_default = TRUE
)
INSERT INTO project_companions (project_id, companion_id, linked_at)
SELECT dp.project_id, c.id, now()
FROM companions c
JOIN default_projects dp ON dp.owner_id = c.owner_id
ON CONFLICT (project_id, companion_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'companion_profile_schemas'
    ) THEN
        INSERT INTO companion_user_profile_schemas (companion_id, project_id, schema, updated_at, updated_by)
        SELECT
            cps.companion_id,
            dp.project_id,
            cps.schema,
            cps.updated_at,
            cps.updated_by
        FROM companion_profile_schemas cps
        JOIN default_projects dp
          ON dp.owner_id = (
              SELECT owner_id FROM companions WHERE id = cps.companion_id
          )
        ON CONFLICT (companion_id) DO UPDATE
            SET project_id = EXCLUDED.project_id,
                schema = EXCLUDED.schema,
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by;

        DELETE FROM companion_profile_schemas;
        DROP TABLE IF EXISTS companion_profile_schemas;
    END IF;
END;
$$;

-- knowledge_ingestion_jobs is new; no backfill required.

/* ---------- TRIGGERS & HOUSEKEEPING ------------------------------------- */

CREATE OR REPLACE FUNCTION touch_projects_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_projects_touch ON projects;
CREATE TRIGGER trg_projects_touch
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION touch_projects_updated_at();

CREATE OR REPLACE FUNCTION touch_knowledge_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_knowledge_jobs_touch ON knowledge_ingestion_jobs;
CREATE TRIGGER trg_knowledge_jobs_touch
    BEFORE UPDATE ON knowledge_ingestion_jobs
    FOR EACH ROW
    EXECUTE FUNCTION touch_knowledge_jobs_updated_at();
