-- 0030_drop_project_companions.sql
-- Remove project_companions now that companions.project_id is authoritative.

DROP TABLE IF EXISTS project_companions;

-- Cleanup any lingering index names (defensive; DROP TABLE should remove them)
-- No-op if already gone.
