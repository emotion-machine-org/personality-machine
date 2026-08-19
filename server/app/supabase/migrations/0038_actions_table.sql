-- 0036_actions_table.sql
-- Actions table for developer-defined behaviors (sandbox execution)
-- Uses link table pattern (like tools) for companion association

BEGIN;

-- Actions are defined globally, then linked to companions
CREATE TABLE IF NOT EXISTS actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Action identity
    key TEXT NOT NULL UNIQUE,                 -- Global unique key
    name TEXT NOT NULL,
    description TEXT,
    version INT NOT NULL DEFAULT 1,           -- Auto-increment on every update

    -- Sandbox code (stored in DB, injected at runtime)
    source_code TEXT NOT NULL,                -- Python code defining async execute(ctx) function
    dependencies JSONB DEFAULT '[]',          -- pip packages: ["pandas", "requests>=2.28"]

    -- Sandbox security options
    block_network BOOLEAN NOT NULL DEFAULT TRUE,  -- Secure by default
    timeout_seconds INT NOT NULL DEFAULT 60,

    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Link table: associates actions with companions + per-companion config
CREATE TABLE IF NOT EXISTS companion_action_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    action_id UUID NOT NULL REFERENCES actions(id) ON DELETE CASCADE,

    -- Per-companion trigger configuration
    -- Example: [{"type": "keyword", "keywords": ["sad", "help"]}, {"type": "every_n", "n": 10}]
    triggers JSONB NOT NULL DEFAULT '[]',

    -- Per-companion classifier integration
    classifier_eligible BOOLEAN NOT NULL DEFAULT FALSE,
    classifier_hint TEXT,

    -- Per-companion execution mode
    priority BOOLEAN NOT NULL DEFAULT FALSE,  -- true = orchestrator waits, false = background

    -- Per-companion webhook for developer notifications
    webhook_url TEXT,                         -- Called after action completes (with fresh state)

    -- Per-companion params (passed to action as action_params)
    params JSONB DEFAULT '{}',

    -- Enable/disable per companion
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Each action linked once per companion
    UNIQUE(companion_id, action_id)
);

-- Index for link table lookups
CREATE INDEX idx_companion_action_links_companion
    ON companion_action_links(companion_id)
    WHERE enabled = TRUE;

-- Index for classifier-eligible actions per companion
CREATE INDEX idx_companion_action_links_classifier
    ON companion_action_links(companion_id, classifier_eligible)
    WHERE enabled = TRUE AND classifier_eligible = TRUE;

-- Updated_at triggers (reuse existing function from 0031)
CREATE TRIGGER trg_actions_updated_at
    BEFORE UPDATE ON actions
    FOR EACH ROW
    EXECUTE FUNCTION touch_jobs_updated_at();

CREATE TRIGGER trg_companion_action_links_updated_at
    BEFORE UPDATE ON companion_action_links
    FOR EACH ROW
    EXECUTE FUNCTION touch_jobs_updated_at();

-- Auto-increment version on action update
CREATE OR REPLACE FUNCTION increment_action_version()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.source_code IS DISTINCT FROM OLD.source_code
       OR NEW.dependencies IS DISTINCT FROM OLD.dependencies THEN
        NEW.version := OLD.version + 1;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_actions_version
    BEFORE UPDATE ON actions
    FOR EACH ROW
    EXECUTE FUNCTION increment_action_version();

COMMENT ON TABLE actions IS 'Global action definitions with source code (executed in Modal Sandboxes)';
COMMENT ON TABLE companion_action_links IS 'Links actions to companions with per-companion config (triggers, priority, webhook)';
COMMENT ON COLUMN actions.source_code IS 'Python code defining async execute(ctx: ActionContext) -> ActionOutput';
COMMENT ON COLUMN actions.dependencies IS 'JSON array of pip packages to install in sandbox';
COMMENT ON COLUMN actions.block_network IS 'If TRUE, sandbox cannot make outbound network calls (secure by default)';
COMMENT ON COLUMN companion_action_links.priority IS 'TRUE = orchestrator waits for completion, FALSE = background execution';
COMMENT ON COLUMN companion_action_links.webhook_url IS 'Developer endpoint called after action completes with result and fresh state';

COMMIT;
