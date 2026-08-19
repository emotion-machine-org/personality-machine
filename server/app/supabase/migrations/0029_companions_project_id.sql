-- 0029_companions_project_id.sql
-- Move companion → project relationship into companions table (single-project assumption).
-- Backfill using existing project_companions links when present; otherwise fall back to owner's default project.

/* ---------- SCHEMA CHANGES -------------------------------------------------- */

ALTER TABLE companions
    ADD COLUMN IF NOT EXISTS project_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_name = 'companions'
          AND constraint_type = 'FOREIGN KEY'
          AND constraint_name = 'companions_project_id_fkey'
    ) THEN
        ALTER TABLE companions
            ADD CONSTRAINT companions_project_id_fkey
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_companions_project ON companions(project_id);

/* ---------- DATA BACKFILL --------------------------------------------------- */

-- Ensure every user has a default project (mirrors 0026 logic, idempotent)
WITH existing_defaults AS (
    SELECT owner_id FROM projects WHERE is_default = TRUE
)
INSERT INTO projects (id, owner_id, name, slug, is_default, metadata)
SELECT uuid_generate_v4(),
       u.id,
       'Default Project',
       'default-' || left(u.id::text, 8),
       TRUE,
       jsonb_build_object('seeded_at', now(), 'seed_source', '0029_companions_project_id')
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM existing_defaults ed WHERE ed.owner_id = u.id
);

-- 1) Backfill from project_companions where there is exactly one linked project.
WITH single_link AS (
    SELECT companion_id,
           MAX(project_id::text)::uuid AS project_id
    FROM project_companions
    GROUP BY companion_id
    HAVING COUNT(DISTINCT project_id) = 1
)
UPDATE companions c
SET project_id = sl.project_id
FROM single_link sl
WHERE c.id = sl.companion_id
  AND c.project_id IS NULL;

-- 2) For companions linked to multiple projects, pick the most recent link.
WITH ranked_links AS (
    SELECT companion_id,
           project_id,
           linked_at,
           ROW_NUMBER() OVER (
               PARTITION BY companion_id
               ORDER BY linked_at DESC NULLS LAST, project_id DESC
           ) AS rn,
           COUNT(*) OVER (PARTITION BY companion_id) AS cnt
    FROM project_companions
),
chosen AS (
    SELECT companion_id, project_id
    FROM ranked_links
    WHERE cnt > 1 AND rn = 1
)
UPDATE companions c
SET project_id = ch.project_id
FROM chosen ch
WHERE c.id = ch.companion_id
  AND c.project_id IS NULL;

-- 3) For companions still null, set to owner’s default project.
WITH default_projects AS (
    SELECT owner_id, id AS project_id
    FROM projects
    WHERE is_default = TRUE
)
UPDATE companions c
SET project_id = dp.project_id
FROM default_projects dp
WHERE c.owner_id = dp.owner_id
  AND c.project_id IS NULL;

/* ---------- ENFORCE NOT NULL ------------------------------------------------ */

ALTER TABLE companions
    ALTER COLUMN project_id SET NOT NULL;

/* ---------- NOTE ------------------------------------------------------------ */
-- project_companions is retained for now; drop in a later migration after code completes the move.
