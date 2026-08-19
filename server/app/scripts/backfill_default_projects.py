#!/usr/bin/env python3
"""Backfill default projects for users missing one and link their companions.

Usage:
    cd server
    uv run python app/scripts/backfill_default_projects.py

Requires DATABASE_DSN (exported or present in .env).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.repositories.project import ProjectRepository

load_dotenv()

DATABASE_DSN = os.getenv("DATABASE_DSN")


def _rows_from_command_tag(tag: str) -> int:
    try:
        return int(tag.split()[-1])
    except (IndexError, ValueError):
        return 0


async def _assign_owner_companions(conn: asyncpg.Connection, project_id, owner_id) -> int:
    """Set project_id on companions owned by owner_id that don't have one yet."""
    command_tag = await conn.execute(
        """
        UPDATE companions c
        SET project_id = $1
        WHERE c.owner_id = $2
          AND c.project_id IS DISTINCT FROM $1
        """,
        project_id,
        owner_id,
    )
    return _rows_from_command_tag(command_tag)


async def main() -> None:
    if not DATABASE_DSN:
        raise RuntimeError("DATABASE_DSN is required")

    conn = await asyncpg.connect(DATABASE_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT u.id, u.email
            FROM users u
            WHERE NOT EXISTS (
                SELECT 1 FROM projects p WHERE p.owner_id = u.id
            )
            ORDER BY u.created_at ASC
            """
        )

        print(f"Found {len(rows)} users missing projects")

        created_projects = 0
        linked_companions = 0

        for row in rows:
            user_id = row["id"]
            email = row["email"] or "<unknown>"

            project = await ProjectRepository.ensure_default_project(
                conn,
                user_id,
                seed_source="script-backfill",
            )
            created_projects += 1

            linked = await _assign_owner_companions(conn, project.id, user_id)
            linked_companions += linked

            print(f"  -> Provisioned project {project.id} for {email}; linked {linked} companions")

        print(
            f"Done. Created {created_projects} projects and linked {linked_companions} companions."
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
