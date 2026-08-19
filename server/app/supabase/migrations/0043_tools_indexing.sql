-- 0041_tools_indexing.sql
-- Tool specs + indexed operations stored with embeddings for retrieval.

CREATE TABLE IF NOT EXISTS tool_specs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    companion_id UUID REFERENCES companions(id) ON DELETE CASCADE,
    spec_name TEXT,
    json_content JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_operations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    spec_id UUID NOT NULL REFERENCES tool_specs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    path TEXT NOT NULL,
    method TEXT NOT NULL,
    input_parameters JSONB,
    output_schema JSONB,
    embedding VECTOR(1536),
    embedding_model TEXT,
    api_key TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tool_specs_project ON tool_specs(project_id);
CREATE INDEX IF NOT EXISTS idx_tool_specs_companion ON tool_specs(companion_id);
CREATE INDEX IF NOT EXISTS idx_tool_operations_spec ON tool_operations(spec_id);
CREATE INDEX IF NOT EXISTS idx_tool_operations_project ON tool_operations(project_id);

CREATE INDEX IF NOT EXISTS idx_tool_operations_embedding
    ON tool_operations USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
