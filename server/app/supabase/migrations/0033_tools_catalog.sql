-- 0030_tools_catalog.sql

CREATE TABLE IF NOT EXISTS tools (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_name TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    spec JSONB NOT NULL,
    category TEXT NOT NULL DEFAULT 'developer_ingested',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS companion_tool_links (
    tool_id UUID NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    companion_id UUID NOT NULL REFERENCES companions(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    priority INT NOT NULL DEFAULT 50,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (tool_id, companion_id)
);

-- Optional indexes for faster lookup
CREATE INDEX IF NOT EXISTS idx_companion_tool_links_companion ON companion_tool_links(companion_id);
CREATE INDEX IF NOT EXISTS idx_tools_file_name ON tools(file_name);

-- Seed a single default "joke_tool" in tools table if absent
INSERT INTO tools (file_name, name, summary, spec, category)
SELECT 'joke_tool.py', 'Joke Tool', 'Generate a short joke; accepts optional random_seed integer.',
       '{"type":"function","parameters":{"type":"object","properties":{"random_seed":{"type":"integer"}},"required":[]}}'::jsonb,
       'builtin'
WHERE NOT EXISTS (SELECT 1 FROM tools WHERE file_name = 'joke_tool.py');
