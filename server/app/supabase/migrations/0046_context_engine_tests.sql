-- Migration: Context Engine Tests
-- Stores saved test configurations for the context engine testing page

CREATE TABLE IF NOT EXISTS context_engine_tests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT context_engine_tests_companion_name_unique UNIQUE (companion_id, name)
);

CREATE OR REPLACE FUNCTION touch_context_engine_tests_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_context_engine_tests_updated_at ON context_engine_tests;
CREATE TRIGGER trg_context_engine_tests_updated_at
    BEFORE UPDATE ON context_engine_tests
    FOR EACH ROW EXECUTE FUNCTION touch_context_engine_tests_updated_at();
