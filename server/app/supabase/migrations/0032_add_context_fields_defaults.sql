-- 0029_add_context_fields_defaults.sql
-- Safely backfill companion_versions.system_prompt JSON blobs with context fields
-- (context_mode, layers, context) when the stored prompt is valid JSON.

DO $$
DECLARE
    r RECORD;
    cfg JSONB;
    updated BOOLEAN;
BEGIN
    FOR r IN SELECT id, system_prompt FROM companion_versions LOOP
        updated := false;

        -- Skip rows that are not valid JSON
        BEGIN
            cfg := r.system_prompt::jsonb;
        EXCEPTION WHEN others THEN
            CONTINUE;
        END;

        IF jsonb_typeof(cfg) <> 'object' THEN
            CONTINUE;
        END IF;

        IF NOT (cfg ? 'context_mode') THEN
            cfg := jsonb_set(cfg, '{context_mode}', '"legacy"'::jsonb, true);
            updated := true;
        END IF;

        IF NOT (cfg ? 'layers') THEN
            cfg := jsonb_set(cfg, '{layers}', '[]'::jsonb, true);
            updated := true;
        END IF;

        IF NOT (cfg ? 'context') THEN
            cfg := jsonb_set(
                cfg,
                '{context}',
                '{"max_prompt_tokens": null, "target_prompt_fraction": 0.4, "reserved_completion_tokens": null}'::jsonb,
                true
            );
            updated := true;
        END IF;

        IF updated THEN
            UPDATE companion_versions
            SET system_prompt = cfg::text
            WHERE id = r.id;
        END IF;
    END LOOP;
END $$;
