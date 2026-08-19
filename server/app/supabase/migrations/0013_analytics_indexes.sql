-- 0013_analytics_indexes.sql
-- Indexes to speed up labeling queries and search

-- Ensure pg_trgm for fast ILIKE queries
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Label filters
CREATE INDEX IF NOT EXISTS idx_conv_labels_engagement ON conversation_labels (engagement_label);
CREATE INDEX IF NOT EXISTS idx_conv_labels_risk ON conversation_labels (dependency_risk_label);
CREATE INDEX IF NOT EXISTS idx_conv_labels_status ON conversation_labels (status);

-- Search on external_user_id
CREATE INDEX IF NOT EXISTS idx_conversations_ext_user_trgm ON conversations USING gin (external_user_id gin_trgm_ops);
