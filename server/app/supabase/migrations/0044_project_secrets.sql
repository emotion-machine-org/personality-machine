-- Project Secrets Table
-- Stores encrypted API keys and secrets at the project level.
-- Secrets are referenced by name in tool_specs for runtime resolution.

CREATE TABLE IF NOT EXISTS project_secrets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    secret_name TEXT NOT NULL,              -- developer-defined: "openai_key", "stripe_api_key"
    encrypted_value BYTEA NOT NULL,         -- AES-256-GCM encrypted
    description TEXT,                       -- optional description for UI
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(project_id, secret_name)
);

CREATE INDEX IF NOT EXISTS idx_project_secrets_project ON project_secrets(project_id);

-- Add secrets_config to tool_specs to map header names to secret names
-- Example: {"Authorization": "my_backend_key", "X-OpenAI-Key": "openai_key"}
ALTER TABLE tool_specs ADD COLUMN IF NOT EXISTS secrets_config JSONB DEFAULT '{}';

-- Remove api_key from tool_operations (secrets are now at spec level)
ALTER TABLE tool_operations DROP COLUMN IF EXISTS api_key;

COMMENT ON TABLE project_secrets IS 'Encrypted API keys and secrets, scoped to projects, referenced by name in tool specs';
COMMENT ON COLUMN project_secrets.secret_name IS 'Developer-defined identifier for the secret';
COMMENT ON COLUMN project_secrets.encrypted_value IS 'AES-256-GCM encrypted secret value';
COMMENT ON COLUMN tool_specs.secrets_config IS 'Maps HTTP header names to project secret names for runtime resolution';
