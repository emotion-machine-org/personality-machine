#!/usr/bin/env python3
"""Ensure tools catalog tables exist and seed joke_tool if missing."""

import asyncio
import os

import asyncpg

DDL = """
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

CREATE INDEX IF NOT EXISTS idx_companion_tool_links_companion ON companion_tool_links(companion_id);
CREATE INDEX IF NOT EXISTS idx_tools_file_name ON tools(file_name);

INSERT INTO tools (file_name, name, summary, spec, category)
SELECT 'joke_tool.py', 'Joke Tool', 'Generate a short joke; accepts optional random_seed integer.',
       '{"type":"function","parameters":{"type":"object","properties":{"random_seed":{"type":"integer"}},"required":[]}}'::jsonb,
       'builtin'
WHERE NOT EXISTS (SELECT 1 FROM tools WHERE file_name = 'joke_tool.py');
"""


async def main() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(DDL)
        print("Tools catalog ensured; joke_tool seeded if missing.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
