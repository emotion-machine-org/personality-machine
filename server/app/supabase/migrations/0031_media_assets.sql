-- 0031_media_assets.sql
-- Media assets table for storing images (and potentially other media types later)

-- Drop existing table if re-running migration
DROP TABLE IF EXISTS media_assets CASCADE;

CREATE TABLE media_assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Core relationships (minimal approach)
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    message_id UUID REFERENCES messages(id) ON DELETE SET NULL,

    -- Asset type
    asset_type TEXT NOT NULL DEFAULT 'image',  -- 'image', 'audio', 'video' (future)

    -- Storage info
    storage_path TEXT NOT NULL,  -- S3 key
    filename TEXT NOT NULL,  -- Original filename
    mime_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    checksum TEXT,  -- SHA256 for deduplication

    -- Image-specific metadata
    width INT,
    height INT,

    -- Extracted content (from vision model)
    description TEXT,
    description_model TEXT,  -- e.g., 'gemini-2.0-flash'
    description_extracted_at TIMESTAMPTZ,

    -- Status tracking
    status TEXT NOT NULL DEFAULT 'uploaded',  -- 'uploaded', 'processing', 'ready', 'failed'
    error_message TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for common query patterns
CREATE INDEX idx_media_assets_conversation ON media_assets(conversation_id, created_at DESC);
CREATE INDEX idx_media_assets_message ON media_assets(message_id);
CREATE INDEX idx_media_assets_status ON media_assets(status) WHERE status != 'ready';

-- Update trigger for updated_at
DROP TRIGGER IF EXISTS trg_media_assets_updated_at ON media_assets;
CREATE TRIGGER trg_media_assets_updated_at
    BEFORE UPDATE ON media_assets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
