#!/usr/bin/env python3
"""One-off helper to mint project API keys.

Usage:
    cd server
    uv run python app/scripts/create_project_api_key.py

Ensure DATABASE_DSN is available (via .env or exported) and update
PROJECT_ID / PROJECT_OWNER_ID below as needed.
"""

import os
import sys
from pathlib import Path
from uuid import UUID

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.api_keys import generate_project_api_key

load_dotenv()

DATABASE_DSN = os.getenv("DATABASE_DSN")
PROJECT_ID = UUID(os.environ["EM_PROJECT_ID"])
PROJECT_OWNER_ID = UUID(os.environ["EM_PROJECT_OWNER_ID"])


def main() -> None:
    if not DATABASE_DSN:
        raise RuntimeError("DATABASE_DSN is required")

    print("Connecting to database …", flush=True)
    conn = psycopg2.connect(DATABASE_DSN, connect_timeout=15)
    conn.autocommit = True

    with conn.cursor() as cur:
        cur.execute("SELECT owner_id FROM projects WHERE id = %s", (str(PROJECT_ID),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Project {PROJECT_ID} not found")
        owner_id_raw = row[0]
        owner_id = UUID(str(owner_id_raw))
        print(f"Project owner: {owner_id}")
        print(f"Project owner ID: {PROJECT_OWNER_ID}")
        if PROJECT_OWNER_ID and owner_id != PROJECT_OWNER_ID:
            raise RuntimeError("Project owner mismatch; adjust PROJECT_OWNER_ID")

        full_key, prefix, salt, secret_hash = generate_project_api_key()
        cur.execute(
            """
            INSERT INTO project_api_keys (
                project_id,
                created_by,
                name,
                prefix,
                secret_hash,
                salt,
                scopes,
                metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, prefix
            """,
            (
                str(PROJECT_ID),
                str(PROJECT_OWNER_ID) if PROJECT_OWNER_ID else None,
                "cli-generated",
                prefix,
                psycopg2.Binary(secret_hash),
                psycopg2.Binary(salt),
                ["read", "write"],
                Json({}),
            ),
        )
        key_id, stored_prefix = cur.fetchone()

    conn.close()

    print("Successfully created project API key:")
    print(f"  id:     {key_id}")
    print(f"  prefix: {stored_prefix}")
    print(f"  key:    {full_key}")


if __name__ == "__main__":
    main()
