-- 0027_knowledge_assets.sql
-- Introduce knowledge asset storage for uploaded documents and link ingestion jobs to assets.

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS knowledge_assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    storage_path TEXT NOT NULL,
    checksum TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_assets_project
    ON knowledge_assets (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_assets_companion
    ON knowledge_assets (companion_id, created_at DESC);

ALTER TABLE knowledge_ingestion_jobs
    ADD COLUMN IF NOT EXISTS asset_id UUID REFERENCES knowledge_assets(id) ON DELETE SET NULL;

DROP TRIGGER IF EXISTS trg_knowledge_assets_updated_at ON knowledge_assets;
CREATE TRIGGER trg_knowledge_assets_updated_at
    BEFORE UPDATE ON knowledge_assets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
