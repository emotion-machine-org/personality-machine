-- 0008_add_memory_eval_prompts.sql
-- Add memory evaluation prompt to companion versions

ALTER TABLE companion_versions
    ADD COLUMN IF NOT EXISTS memory_evaluation_prompt TEXT;
