-- Add sample companion data for testing conversation persistence
-- This ensures that conversation creation doesn't fail due to missing deployment_id
-- Using actual user ID: 1b6f2fc4-0ea1-410d-a9c7-ba5faf2634ba

-- Insert sample companion for the existing user
INSERT INTO companions (id, owner_id, name, description)
VALUES ('1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bb', '1b6f2fc4-0ea1-410d-a9c7-ba5faf2634ba', 'My First Companion', 'A helpful AI companion for testing and conversation')
ON CONFLICT (id) DO NOTHING;

-- Insert companion version
INSERT INTO companion_versions (id, companion_id, version_number, system_prompt, status)
VALUES ('1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bc', '1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bb', 1, 'You are a helpful and friendly AI companion. Keep your responses conversational and engaging. You have a warm personality and enjoy helping users with various tasks and conversations.', 'DEPLOYED')
ON CONFLICT (id) DO NOTHING;

-- Insert deployment for the companion
INSERT INTO deployments (id, companion_id, version_id, slug, is_active)
VALUES ('1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bd', '1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bb', '1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bc', 'my-first-companion', true)
ON CONFLICT (id) DO NOTHING;

-- Also keep the original placeholder deployment for backward compatibility
INSERT INTO deployments (id, companion_id, version_id, slug, is_active)
VALUES ('00000000-0000-4000-8000-000000000001', '1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bb', '1b6f2fc4-0ea1-410d-a9c7-ba5faf2634bc', 'test-placeholder', true)
ON CONFLICT (id) DO NOTHING;
