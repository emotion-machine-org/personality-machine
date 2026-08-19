#!/usr/bin/env python3
"""
Backfill context fields (context_mode, layers, context) into companion_versions.config JSON blobs.

Usage:
    export DATABASE_URL=postgres://user:pass@host:port/dbname
    python scripts/backfill_context_fields.py --dry-run
"""

import argparse
import asyncio
import json
import os

import asyncpg

DEFAULT_CONTEXT = {
    "max_prompt_tokens": None,
    "target_prompt_fraction": 0.4,
    "reserved_completion_tokens": None,
}


async def _setup_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register JSONB codec to automatically encode/decode Python dicts."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def backfill(conn, dry_run: bool) -> int:
    # Read from config column, fallback to system_prompt for legacy data
    rows = await conn.fetch("SELECT id, config, system_prompt FROM companion_versions")
    updated = 0
    for row in rows:
        # Prefer config column, fallback to system_prompt
        cfg_raw = row["config"] if row["config"] else row["system_prompt"]
        if cfg_raw is None:
            continue

        # Handle both string (legacy) and dict (with codec) formats
        if isinstance(cfg_raw, str):
            try:
                cfg = json.loads(cfg_raw)
            except Exception:
                continue
        elif isinstance(cfg_raw, dict):
            cfg = cfg_raw
        else:
            continue

        if not isinstance(cfg, dict):
            continue

        changed = False
        if "context_mode" not in cfg:
            cfg["context_mode"] = "layered"
            changed = True
        if "layers" not in cfg:
            cfg["layers"] = []
            changed = True
        if "context" not in cfg:
            cfg["context"] = DEFAULT_CONTEXT.copy()
            changed = True

        if changed:
            updated += 1
            if not dry_run:
                # Write to config column (use json.dumps since we don't have codec on this connection)
                await conn.execute(
                    "UPDATE companion_versions SET config = $1 WHERE id = $2",
                    json.dumps(cfg),
                    row["id"],
                )
    return updated


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Inspect without writing changes")
    args = parser.parse_args()

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")

    conn = await asyncpg.connect(dsn)
    try:
        updated = await backfill(conn, args.dry_run)
        action = "would update" if args.dry_run else "updated"
        print(f"{action} {updated} companion_versions rows")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
