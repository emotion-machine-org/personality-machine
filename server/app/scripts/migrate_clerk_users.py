#!/usr/bin/env python3
"""Migrate users from Clerk dev instance to production instance.

This script updates the clerk_user_id in your database to match the new
production Clerk IDs after migrating from dev to production.

Usage:
    cd server

    # Dry run (default) - shows what would be updated
    uv run python app/scripts/migrate_clerk_users.py users.csv

    # Actually perform the migration
    uv run python app/scripts/migrate_clerk_users.py users.csv --apply

Requires:
    - DATABASE_DSN: Your Supabase connection string
    - CLERK_SECRET_KEY: Your PRODUCTION Clerk secret key (sk_live_xxx)

CSV Format:
    The CSV should have at least an 'email' column (or 'email_address').
    Example:
        email,name
        user1@example.com,User One
        user2@gmail.com,User Two
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Load .env from server directory
env_path = ROOT_DIR / ".env"
if env_path.exists():
    load_dotenv(env_path)
else:
    load_dotenv()  # Try default locations

DATABASE_DSN = os.getenv("DATABASE_DSN")
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")


def load_emails_from_csv(csv_path: str) -> list[str]:
    """Load email addresses from CSV file."""
    emails = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        # Find the email column (could be 'email' or 'email_address')
        fieldnames = reader.fieldnames or []
        email_col = None
        for col in ["email", "primary_email_address", "email_address", "Email", "Email Address"]:
            if col in fieldnames:
                email_col = col
                break

        if not email_col:
            raise ValueError(
                f"CSV must have an 'email' or 'email_address' column. Found columns: {fieldnames}"
            )

        for row in reader:
            email = row[email_col].strip()
            if email:
                emails.append(email)

    return emails


def lookup_clerk_user_by_email(http_client: httpx.Client, email: str) -> str | None:
    """Look up a user in Clerk by email using REST API, return their user ID."""
    try:
        # Clerk REST API: GET /users with email_address filter
        response = http_client.get(
            "https://api.clerk.com/v1/users", params={"email_address": email, "limit": 10}
        )
        response.raise_for_status()
        users = response.json()

        # Find exact email match
        for user in users:
            for email_obj in user.get("email_addresses", []):
                if email_obj.get("email_address", "").lower() == email.lower():
                    return user["id"]
        return None
    except Exception as e:
        print(f"  [ERROR] Failed to lookup {email} in Clerk: {e}")
        return None


async def get_db_users_by_emails(conn: asyncpg.Connection, emails: list[str]) -> dict[str, dict]:
    """Fetch existing users from DB by email."""
    rows = await conn.fetch(
        """
        SELECT id, email, clerk_user_id, display_name
        FROM users
        WHERE email = ANY($1)
        """,
        emails,
    )
    return {row["email"]: dict(row) for row in rows}


async def check_clerk_id_exists(conn: asyncpg.Connection, clerk_id: str) -> dict | None:
    """Check if a clerk_user_id already exists in the database."""
    row = await conn.fetchrow(
        "SELECT id, email, clerk_user_id FROM users WHERE clerk_user_id = $1",
        clerk_id,
    )
    return dict(row) if row else None


async def update_clerk_user_id(
    conn: asyncpg.Connection, email: str, new_clerk_id: str
) -> tuple[bool, str | None]:
    """Update a user's clerk_user_id by email.

    Returns (success, error_message).
    """
    # First check if the new clerk_id already exists
    existing = await check_clerk_id_exists(conn, new_clerk_id)
    if existing:
        if existing["email"] == email:
            # Same user, already updated
            return True, None
        else:
            # Different user has this clerk_id - conflict!
            return False, f"clerk_user_id already used by {existing['email']}"

    try:
        result = await conn.execute(
            """
            UPDATE users
            SET clerk_user_id = $1, updated_at = now()
            WHERE email = $2
            """,
            new_clerk_id,
            email,
        )
        return "UPDATE 1" in result, None
    except Exception as e:
        return False, str(e)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Clerk user IDs from dev to production")
    parser.add_argument("csv_file", help="Path to CSV file with user emails")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes (default is dry-run)",
    )
    args = parser.parse_args()

    if not DATABASE_DSN:
        raise RuntimeError("DATABASE_DSN environment variable is required")
    if not CLERK_SECRET_KEY:
        raise RuntimeError("CLERK_SECRET_KEY environment variable is required")
    if not CLERK_SECRET_KEY.startswith("sk_live_"):
        print(
            "[WARNING] CLERK_SECRET_KEY doesn't start with 'sk_live_' - "
            "are you sure this is a production key?"
        )

    # Load emails from CSV
    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        raise RuntimeError(f"CSV file not found: {csv_path}")

    emails = load_emails_from_csv(str(csv_path))
    print(f"Loaded {len(emails)} emails from {csv_path.name}")

    # Initialize HTTP client for Clerk API
    http_client = httpx.Client(
        headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
        timeout=30.0,
    )

    # Connect to database
    conn = await asyncpg.connect(DATABASE_DSN)

    try:
        # Get existing users from DB
        db_users = await get_db_users_by_emails(conn, emails)
        print(f"Found {len(db_users)} matching users in database\n")

        # Track results
        updated = 0
        skipped_not_in_db = 0
        skipped_not_in_clerk = 0
        skipped_already_correct = 0
        errors = 0

        for email in emails:
            db_user = db_users.get(email)

            if not db_user:
                print(f"  [SKIP] {email} - not found in database")
                skipped_not_in_db += 1
                continue

            old_clerk_id = db_user["clerk_user_id"]
            display_name = db_user["display_name"] or email

            # Look up new Clerk ID
            new_clerk_id = lookup_clerk_user_by_email(http_client, email)

            if not new_clerk_id:
                print(f"  [SKIP] {email} - not found in production Clerk")
                skipped_not_in_clerk += 1
                continue

            if old_clerk_id == new_clerk_id:
                print(f"  [OK] {email} - already has correct clerk_user_id")
                skipped_already_correct += 1
                continue

            # Show what we'd update
            print(f"  [UPDATE] {display_name} ({email})")
            print(f"           Old: {old_clerk_id or '(none)'}")
            print(f"           New: {new_clerk_id}")

            if args.apply:
                success, error_msg = await update_clerk_user_id(conn, email, new_clerk_id)
                if success:
                    updated += 1
                else:
                    print(f"           [ERROR] {error_msg or 'Update failed!'}")
                    errors += 1
            else:
                updated += 1  # Count as "would update" in dry-run

        # Summary
        print("\n" + "=" * 50)
        if args.apply:
            print("MIGRATION COMPLETE")
        else:
            print("DRY RUN COMPLETE (use --apply to execute)")
        print("=" * 50)
        print(f"  Would update / Updated:    {updated}")
        print(f"  Already correct:           {skipped_already_correct}")
        print(f"  Not in database:           {skipped_not_in_db}")
        print(f"  Not in production Clerk:   {skipped_not_in_clerk}")
        if errors:
            print(f"  Errors:                    {errors}")

    finally:
        await conn.close()
        http_client.close()


if __name__ == "__main__":
    asyncio.run(main())
