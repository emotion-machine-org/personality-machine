-- 0037_companion_classifier_config.sql
-- Backfill companion_versions.system_prompt JSON with classifier config and always_run flags

DO $$
DECLARE
    r RECORD;
    cfg JSONB;
    updated BOOLEAN;
    layers_arr JSONB;
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

        -- Add classifier config if missing
        IF NOT (cfg ? 'classifier') THEN
            cfg := jsonb_set(cfg, '{classifier}', '{
                "enabled": true,
                "model": "fast",
                "timeout_ms": 2000,
                "history_limit": 20
            }'::jsonb, true);
            updated := true;
        END IF;

        -- Convert layers array to include always_run if needed
        -- Old format: [{"category": "memory", "enabled": true, "params": {}}]
        -- New format keeps array but adds always_run to each item
        IF cfg ? 'layers' AND jsonb_typeof(cfg->'layers') = 'array' THEN
            SELECT jsonb_agg(
                CASE
                    WHEN NOT (layer ? 'always_run')
                    THEN layer || '{"always_run": false}'::jsonb
                    ELSE layer
                END
            )
            INTO layers_arr
            FROM jsonb_array_elements(cfg->'layers') AS layer;

            IF layers_arr IS NOT NULL AND layers_arr != cfg->'layers' THEN
                cfg := jsonb_set(cfg, '{layers}', layers_arr, true);
                updated := true;
            END IF;
        END IF;

        IF updated THEN
            UPDATE companion_versions
            SET system_prompt = cfg::text
            WHERE id = r.id;
        END IF;
    END LOOP;
END $$;

COMMENT ON COLUMN companion_versions.system_prompt IS 'JSON config including classifier settings and layer always_run flags';
