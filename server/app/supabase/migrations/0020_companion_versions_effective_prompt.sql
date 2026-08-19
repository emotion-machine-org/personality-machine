-- 0020_companion_versions_effective_prompt.sql
-- Cache the effective prompt (builder prompt + core memories) used at runtime

ALTER TABLE companion_versions
  ADD COLUMN IF NOT EXISTS effective_system_prompt TEXT;
