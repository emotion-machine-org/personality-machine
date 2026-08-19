-- Add classifier_summary column to tool_specs for intent classifier context
ALTER TABLE tool_specs ADD COLUMN IF NOT EXISTS classifier_summary TEXT;
