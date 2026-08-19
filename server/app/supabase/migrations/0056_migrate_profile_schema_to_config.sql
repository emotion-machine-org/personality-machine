-- Migration: Copy profile schemas from companion_user_profile_schemas into companion_versions.config
--
-- This migration merges the profile_schema from the separate table into the
-- companion's latest version config. This is Phase 2 of the Profile Tab implementation.
--
-- After this migration:
-- - companion_versions.config will include profile_schema for companions that had one
-- - The companion_user_profile_schemas table remains (cleanup in Phase 5)
--
-- Safe to run multiple times: only updates versions that don't already have profile_schema

-- Step 1: Update the latest companion_version for each companion that has a profile schema
-- Uses a CTE to find the latest version_id for each companion
WITH latest_versions AS (
    SELECT DISTINCT ON (companion_id)
        id AS version_id,
        companion_id,
        config
    FROM companion_versions
    ORDER BY companion_id, version_number DESC, created_at DESC
),
schemas_to_migrate AS (
    SELECT
        lv.version_id,
        lv.companion_id,
        lv.config AS current_config,
        s.schema AS profile_schema
    FROM latest_versions lv
    INNER JOIN companion_user_profile_schemas s
        ON s.companion_id = lv.companion_id
    WHERE s.schema IS NOT NULL
      AND s.schema != '{}'::jsonb
      -- Only migrate if profile_schema not already set
      AND (lv.config IS NULL OR lv.config->'profile_schema' IS NULL)
)
UPDATE companion_versions cv
SET config = COALESCE(cv.config, '{}'::jsonb) ||
             jsonb_build_object('profile_schema', stm.profile_schema)
FROM schemas_to_migrate stm
WHERE cv.id = stm.version_id;

-- Step 2: Log migration stats
DO $$
DECLARE
    total_schemas INT;
    migrated_count INT;
    already_had_schema INT;
BEGIN
    -- Count total profile schemas in old table
    SELECT COUNT(*) INTO total_schemas
    FROM companion_user_profile_schemas
    WHERE schema IS NOT NULL AND schema != '{}'::jsonb;

    -- Count how many now have profile_schema in their config
    SELECT COUNT(DISTINCT cv.companion_id) INTO migrated_count
    FROM companion_versions cv
    WHERE cv.config->'profile_schema' IS NOT NULL
      AND cv.config->'profile_schema' != '{}'::jsonb;

    RAISE NOTICE '=== Profile Schema Migration Summary ===';
    RAISE NOTICE 'Total profile schemas in old table: %', total_schemas;
    RAISE NOTICE 'Companions with profile_schema in config: %', migrated_count;
    RAISE NOTICE '=========================================';
END $$;

-- Step 3: Add comment for documentation
COMMENT ON TABLE companion_user_profile_schemas IS
    'DEPRECATED: Profile schemas are now stored in companion_versions.config.profile_schema. '
    'This table will be dropped in a future migration after verification.';
