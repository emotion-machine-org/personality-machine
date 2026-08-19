-- 0002_init_schema.sql
/* ---------- USERS -------------------------------------------------------- */
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email       TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

/* ---------- VOICE CATALOG ----------------------------------------------- */
CREATE TABLE IF NOT EXISTS voices (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,          -- e.g. “Soprano-Soft”
    provider    TEXT NOT NULL,          -- e.g. “ElevenLabs”
    provider_key TEXT NOT NULL,         -- voice_id or config blob
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(provider, provider_key)
);

/* ---------- COMPANIONS (the _product_) ---------------------------------- */
CREATE TABLE IF NOT EXISTS companions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id    UUID       REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);

/* ---------- COMPANION VERSIONS (immutable) ------------------------------ */
CREATE TABLE IF NOT EXISTS companion_versions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    companion_id    UUID REFERENCES companions(id) ON DELETE CASCADE,
    version_number  INT  NOT NULL,     -- starts at 1, monotonic
    system_prompt   TEXT NOT NULL,
    voice_id        UUID REFERENCES voices(id),
    memory_enabled  BOOLEAN DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'DRAFT', -- DRAFT | DEPLOYED | ARCHIVED
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(companion_id, version_number)
);

/* Helper to auto-increment version_number on INSERT */
CREATE OR REPLACE FUNCTION next_companion_version() RETURNS TRIGGER AS $$
BEGIN
    NEW.version_number := COALESCE(
        (SELECT MAX(version_number) + 1 FROM companion_versions
         WHERE companion_id = NEW.companion_id), 1);
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_version_autonumber
    BEFORE INSERT ON companion_versions
    FOR EACH ROW EXECUTE FUNCTION next_companion_version();

/* ---------- DEPLOYMENTS -------------------------------------------------- */
CREATE TABLE IF NOT EXISTS deployments (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    companion_id     UUID REFERENCES companions(id) ON DELETE CASCADE,
    version_id       UUID REFERENCES companion_versions(id),
    slug             TEXT UNIQUE NOT NULL,          -- used in /c/{slug}
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT now()
);

/* ---------- CONVERSATIONS & MESSAGES ------------------------------------ */
CREATE TABLE IF NOT EXISTS conversations (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_id UUID REFERENCES deployments(id) ON DELETE CASCADE,
    external_user_id TEXT,                      -- cookie / auth from visitor
    started_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    role           TEXT NOT NULL,               -- system|user|assistant
    content        TEXT NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT now()
);

/* ---------- LONG-TERM MEMORY (vector store) ----------------------------- */
CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    companion_id UUID REFERENCES companions(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    embedding   VECTOR(1536),
    created_at  TIMESTAMPTZ DEFAULT now()
);

/* ---------- OPTIONAL INDICES -------------------------------------------- */
CREATE INDEX IF NOT EXISTS idx_companions_owner   ON companions(owner_id);
CREATE INDEX IF NOT EXISTS idx_versions_companion ON companion_versions(companion_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_deployment ON conversations(deployment_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conv     ON messages(conversation_id, created_at);

-- pgvector ANN (HNSW) for fast similarity search on memories
CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
