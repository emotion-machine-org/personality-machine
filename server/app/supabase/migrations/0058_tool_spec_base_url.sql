-- Add base_url to tool_specs so we can persist the OpenAPI servers[0].url
ALTER TABLE tool_specs ADD COLUMN IF NOT EXISTS base_url TEXT;
COMMENT ON COLUMN tool_specs.base_url IS 'Base API URL extracted from OpenAPI servers[0].url';
