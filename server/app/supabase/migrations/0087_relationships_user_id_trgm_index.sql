-- 0087_relationships_user_id_trgm_index.sql
-- Add trigram index for fast ILIKE prefix search on relationships.user_id
-- Used by the dashboard relationships search endpoint

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_relationships_user_id_trgm
    ON relationships USING gin (user_id gin_trgm_ops);
