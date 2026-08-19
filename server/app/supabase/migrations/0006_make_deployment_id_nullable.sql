-- 0006_make_deployment_id_nullable.sql
-- Make deployment_id nullable to support direct companion conversations

/* ---------- MAKE DEPLOYMENT_ID NULLABLE -------------------------------- */

-- Allow conversations to exist without deployments (for development/testing)
ALTER TABLE conversations
ALTER COLUMN deployment_id DROP NOT NULL;

-- Add comment for clarity
COMMENT ON COLUMN conversations.deployment_id IS 'Deployment ID (nullable for direct companion conversations in development)';
COMMENT ON COLUMN conversations.companion_id IS 'Direct companion reference (always required)';
