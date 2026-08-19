-- 0038_actions_project_scoping.sql
-- Add project_id to actions and tools tables for proper multi-tenancy.
-- Action keys and tool file_names become unique per-project instead of globally.

BEGIN;

/* ---------- ACTIONS TABLE -------------------------------------------------- */

-- Add project_id column (nullable initially for backfill)
ALTER TABLE actions
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;

-- Backfill: assign actions to the project of their first linked companion
UPDATE actions a
SET project_id = (
    SELECT c.project_id
    FROM companion_action_links cal
    JOIN companions c ON cal.companion_id = c.id
    WHERE cal.action_id = a.id
    LIMIT 1
)
WHERE a.project_id IS NULL;

-- For any orphaned actions (no links), assign to a default project of any user
-- This is a safety net; in practice all actions should have links
UPDATE actions a
SET project_id = (
    SELECT id FROM projects WHERE is_default = TRUE LIMIT 1
)
WHERE a.project_id IS NULL;

-- Drop the old global unique constraint on key
ALTER TABLE actions DROP CONSTRAINT IF EXISTS actions_key_key;

-- Add new unique constraint scoped to project
ALTER TABLE actions
    ADD CONSTRAINT actions_project_key_unique UNIQUE (project_id, key);

-- Add index for project lookups
CREATE INDEX IF NOT EXISTS idx_actions_project ON actions(project_id);

-- Make project_id NOT NULL after backfill
DO $$
DECLARE
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_count FROM actions WHERE project_id IS NULL;
    IF null_count = 0 THEN
        ALTER TABLE actions ALTER COLUMN project_id SET NOT NULL;
    ELSE
        RAISE NOTICE 'Skipping NOT NULL on actions.project_id: % rows still NULL', null_count;
    END IF;
END;
$$;

/* ---------- TOOLS TABLE ---------------------------------------------------- */

-- Add project_id column (nullable initially for backfill)
ALTER TABLE tools
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;

-- Backfill: assign tools to the project of their first linked companion
UPDATE tools t
SET project_id = (
    SELECT c.project_id
    FROM companion_tool_links ctl
    JOIN companions c ON ctl.companion_id = c.id
    WHERE ctl.tool_id = t.id
    LIMIT 1
)
WHERE t.project_id IS NULL;

-- For builtin/orphaned tools, assign to NULL (they're global)
-- Or we could assign to a system project - for now keep NULL for builtins
-- Actually, let's mark builtins with a special handling

-- For non-builtin orphaned tools, assign to a default project
UPDATE tools t
SET project_id = (
    SELECT id FROM projects WHERE is_default = TRUE LIMIT 1
)
WHERE t.project_id IS NULL AND t.category != 'builtin';

-- Drop the old global unique constraint on file_name
ALTER TABLE tools DROP CONSTRAINT IF EXISTS tools_file_name_key;

-- Add new unique constraint scoped to project (allowing NULL project_id for builtins)
-- For tools, we use a partial unique index to handle NULL project_id (builtins)
CREATE UNIQUE INDEX IF NOT EXISTS idx_tools_project_file_name_unique
    ON tools(project_id, file_name)
    WHERE project_id IS NOT NULL;

-- Builtins (NULL project_id) still need global uniqueness
CREATE UNIQUE INDEX IF NOT EXISTS idx_tools_builtin_file_name_unique
    ON tools(file_name)
    WHERE project_id IS NULL;

-- Add index for project lookups
CREATE INDEX IF NOT EXISTS idx_tools_project ON tools(project_id);

COMMIT;
