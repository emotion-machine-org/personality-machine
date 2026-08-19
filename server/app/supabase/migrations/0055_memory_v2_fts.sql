-- Memory V2 Full-Text Search: Hybrid tsvector + pg_trgm
-- Combines PostgreSQL's native FTS with trigram similarity for optimal search

-- Enable extensions (idempotent)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Add generated search_vector column for full-text search
-- Type is weighted higher (A) than content (B) for better relevance ranking
ALTER TABLE memory_v2_entries
ADD COLUMN IF NOT EXISTS search_vector tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('english', coalesce(type, '')), 'A') ||
  setweight(to_tsvector('english', coalesce(content, '')), 'B')
) STORED;

-- GIN index for full-text search (tsvector)
CREATE INDEX IF NOT EXISTS idx_memory_v2_search_vector
ON memory_v2_entries USING gin(search_vector);

-- GIN index for trigram similarity (fuzzy search)
CREATE INDEX IF NOT EXISTS idx_memory_v2_content_trgm
ON memory_v2_entries USING gin(content gin_trgm_ops);

-- Composite index for type filtering with relationship_id
CREATE INDEX IF NOT EXISTS idx_memory_v2_type
ON memory_v2_entries(relationship_id, type);

-- Comments
COMMENT ON COLUMN memory_v2_entries.search_vector IS 'Generated tsvector for FTS with type weighted higher than content';
