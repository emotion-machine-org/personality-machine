-- Migration: Rename system_prompt to config in companion_versions
-- This migration renames the misleadingly named 'system_prompt' column to 'config'
-- and transforms the data structure to the new InferenceConfig-based format.
--
-- IMPORTANT: This migration should be run AFTER all server code has been updated
-- to use the new column name. The Python validators handle backward compatibility
-- during the transition period.
--
-- Before running this migration:
-- 1. Deploy server code with new models and validators
-- 2. Verify all endpoints work with the new structure
-- 3. Run this migration during a maintenance window

-- Step 1: Add the new config column (JSONB for full companion configuration)
ALTER TABLE companion_versions ADD COLUMN IF NOT EXISTS config JSONB;

-- Step 1b: Make system_prompt nullable (new code writes to config, not system_prompt)
ALTER TABLE companion_versions ALTER COLUMN system_prompt DROP NOT NULL;

-- Step 2: Migrate data with schema transformation
-- This transforms the old structure to the new one:
-- - Moves model, temperature, max_output_tokens into inference block
-- - Migrates voice.popular_options[0] to voice.preset (with key migration)
-- - Migrates voice.voice[0] to voice.voice_name
-- - Preserves all other fields
UPDATE companion_versions
SET config = jsonb_build_object(
    'system_prompt', COALESCE(system_prompt::jsonb->'system_prompt', '{}'::jsonb),
    'memory', COALESCE(system_prompt::jsonb->'memory', '{}'::jsonb),
    'inference', jsonb_build_object(
        'model', COALESCE(
            system_prompt::jsonb->>'model',
            -- Extract model from voice preset if available
            CASE (system_prompt::jsonb->'voice'->'popular_options'->>0)
                WHEN 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)' THEN 'claude-sonnet-4.5'
                WHEN 'sonnet-4.5 - Anthropic (LLM) - Elevenlabs (STT - TTS)' THEN 'claude-sonnet-4.5'
                WHEN 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)' THEN 'claude-sonnet-4'
                WHEN 'sonnet-4 - Anthropic (LLM) - Elevenlabs (STT - TTS)' THEN 'claude-sonnet-4'
                WHEN 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)' THEN 'claude-sonnet-3.7'
                WHEN 'sonnet-3.7 - Anthropic (LLM) - Elevenlabs (STT - TTS)' THEN 'claude-sonnet-3.7'
                WHEN 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)' THEN 'openai-gpt4o'
                WHEN 'gpt4o - OpenAI (LLM) - Elevenlabs (STT - TTS)' THEN 'openai-gpt4o'
                WHEN 'gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)' THEN 'openai-gpt4o-mini'
                WHEN 'gpt4o-mini - OpenAI (STT) - OpenAI (LLM) - Elevenlabs (TTS)' THEN 'openai-gpt4o-mini'
                WHEN 'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)' THEN 'gemini-2.5-flash'
                WHEN 'gemini-2.5-flash - Google (LLM) - Elevenlabs (STT - TTS)' THEN 'gemini-2.5-flash'
                WHEN 'kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)' THEN 'moonshot-kimi-k2'
                WHEN 'kimi-k2 - Moonshot (LLM) - Elevenlabs (STT - TTS)' THEN 'moonshot-kimi-k2'
                WHEN 'OpenAI - speech-to-speech' THEN 'openai-gpt4o'
                WHEN 'OpenAI - speech-to-speech (Mini)' THEN 'openai-gpt4o-mini'
                ELSE NULL
            END
        ),
        'temperature', COALESCE(
            (system_prompt::jsonb->>'temperature')::numeric,
            (system_prompt::jsonb->'voice'->>'temperature')::numeric,
            0.7
        ),
        'max_output_tokens', (system_prompt::jsonb->>'max_output_tokens')::integer
    ),
    'voice', jsonb_build_object(
        'preset', (
            CASE (system_prompt::jsonb->'voice'->'popular_options'->>0)
                -- Anthropic + ElevenLabs
                WHEN 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)' THEN 'sonnet-4.5-elevenlabs'
                WHEN 'sonnet-4.5 - Anthropic (LLM) - Elevenlabs (STT - TTS)' THEN 'sonnet-4.5-elevenlabs'
                WHEN 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)' THEN 'sonnet-4-elevenlabs'
                WHEN 'sonnet-4 - Anthropic (LLM) - Elevenlabs (STT - TTS)' THEN 'sonnet-4-elevenlabs'
                WHEN 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Elevenlabs (TTS)' THEN 'sonnet-3.7-elevenlabs'
                WHEN 'sonnet-3.7 - Anthropic (LLM) - Elevenlabs (STT - TTS)' THEN 'sonnet-3.7-elevenlabs'
                -- Anthropic + Cartesia
                WHEN 'sonnet-4.5 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)' THEN 'sonnet-4.5-cartesia'
                WHEN 'sonnet-4.5 - Anthropic (LLM) - Cartesia (STT - TTS)' THEN 'sonnet-4.5-cartesia'
                WHEN 'sonnet-4 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)' THEN 'sonnet-4-cartesia'
                WHEN 'sonnet-4 - Anthropic (LLM) - Cartesia (STT - TTS)' THEN 'sonnet-4-cartesia'
                WHEN 'sonnet-3.7 - Cartesia (STT) - Anthropic (LLM) - Cartesia (TTS)' THEN 'sonnet-3.7-cartesia'
                WHEN 'sonnet-3.7 - Anthropic (LLM) - Cartesia (STT - TTS)' THEN 'sonnet-3.7-cartesia'
                -- OpenAI
                WHEN 'gpt4o - Cartesia (STT) - OpenAI (LLM) - Elevenlabs (TTS)' THEN 'gpt4o-elevenlabs'
                WHEN 'gpt4o - OpenAI (LLM) - Elevenlabs (STT - TTS)' THEN 'gpt4o-elevenlabs'
                WHEN 'gpt4o-mini - Deepgram (STT) - OpenAI (LLM) - Elevenlabs (TTS)' THEN 'gpt4o-mini-elevenlabs'
                WHEN 'gpt4o-mini - OpenAI (STT) - OpenAI (LLM) - Elevenlabs (TTS)' THEN 'gpt4o-mini-elevenlabs'
                -- Gemini
                WHEN 'gemini-2.5-flash - Cartesia (STT) - Google (LLM) - Elevenlabs (TTS)' THEN 'gemini-flash-elevenlabs'
                WHEN 'gemini-2.5-flash - Google (LLM) - Elevenlabs (STT - TTS)' THEN 'gemini-flash-elevenlabs'
                -- Moonshot
                WHEN 'kimi-k2 - Cartesia (STT) - Moonshot (LLM) - Elevenlabs (TTS)' THEN 'kimi-k2-elevenlabs'
                WHEN 'kimi-k2 - Moonshot (LLM) - Elevenlabs (STT - TTS)' THEN 'kimi-k2-elevenlabs'
                -- Legacy
                WHEN 'OpenAI - speech-to-speech' THEN 'gpt4o-elevenlabs'
                WHEN 'OpenAI - speech-to-speech (Mini)' THEN 'gpt4o-mini-elevenlabs'
                ELSE NULL
            END
        ),
        'voice_name', system_prompt::jsonb->'voice'->'voice'->>0
    ),
    'context_mode', COALESCE(system_prompt::jsonb->>'context_mode', 'legacy'),
    'layers', COALESCE(system_prompt::jsonb->'layers', '[]'::jsonb),
    'context', COALESCE(system_prompt::jsonb->'context', '{}'::jsonb)
)
WHERE system_prompt IS NOT NULL AND config IS NULL;

-- Step 3: Set NOT NULL constraint (only after data migration)
-- Uncomment this after verifying data migration is complete
-- ALTER TABLE companion_versions ALTER COLUMN config SET NOT NULL;

-- Step 4: Drop old column (only after all code is updated)
-- Uncomment this after all server code references 'config' instead of 'system_prompt'
-- ALTER TABLE companion_versions DROP COLUMN system_prompt;

-- Step 5: Add comment for documentation
COMMENT ON COLUMN companion_versions.config IS
    'Full CompanionConfig JSON: system_prompt, memory, inference, voice, layers, context. Replaces legacy "system_prompt" column.';

-- Create index on config for querying by model
CREATE INDEX IF NOT EXISTS idx_companion_versions_inference_model
    ON companion_versions USING btree ((config->'inference'->>'model'));
