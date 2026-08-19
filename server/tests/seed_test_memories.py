#!/usr/bin/env python
"""
Seed test memories for a companion.

Usage:
    # Using DATABASE_DSN from environment or .env file
    uv run python tests/seed_test_memories.py <companion_id>

    # Or specify DSN directly
    DATABASE_DSN="postgresql://..." uv run python tests/seed_test_memories.py <companion_id>

    # With optional user_id (defaults to "test_user_seed")
    uv run python tests/seed_test_memories.py <companion_id> --user-id my_test_user

    # Specify number of memories (default 50)
    uv run python tests/seed_test_memories.py <companion_id> --count 100

    # Clear existing memories first
    uv run python tests/seed_test_memories.py <companion_id> --clear
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import load_dotenv

# Add the app directory to the path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.repositories.memory_v2_repository import MemoryV2Repository
from app.repositories.relationship_repository import RelationshipRepository

# Probability of adding a suffix to vary memory content
SUFFIX_VARIATION_PROBABILITY = 0.3

# Sample memories organized by type
SAMPLE_MEMORIES = {
    "identity": [
        "User's name is Alex and they work as a software engineer at a startup",
        "User has a golden retriever named Max who is 3 years old",
        "User grew up in Seattle but now lives in San Francisco",
        "User's favorite programming language is Python, followed by TypeScript",
        "User is learning Japanese and has been studying for about 6 months",
        "User prefers dark mode in all applications and IDEs",
        "User is lactose intolerant and avoids dairy products",
        "User has a twin sister named Jordan who works in finance",
        "User's birthday is March 15th",
        "User graduated from UC Berkeley with a CS degree in 2019",
        "User is an early riser and usually wakes up at 5:30 AM",
        "User's favorite coffee is a cortado with oat milk",
        "User drives a blue Tesla Model 3",
        "User is allergic to cats but loves dogs",
        "User prefers tabs over spaces (controversial opinion)",
    ],
    "daily": [
        "Had a productive morning standup - the new feature is on track for Friday release",
        "Went for a 5K run this morning despite the rain",
        "Tried the new ramen place near the office - the miso was excellent",
        "Had a frustrating bug with async race conditions, finally fixed it after 3 hours",
        "Watched the latest episode of The Bear - the kitchen chaos scenes are intense",
        "Cooked pad thai for dinner, turned out pretty good this time",
        "Started reading 'Designing Data-Intensive Applications' - really enjoying it so far",
        "Had a great one-on-one with manager, discussed promotion timeline",
        "Attended a TypeScript meetup downtown, learned about new TS 5.0 features",
        "Practiced Japanese with language exchange partner for an hour",
        "Fixed the memory leak issue in production that was causing OOM crashes",
        "Went to the dentist for a routine cleaning",
        "Helped a junior developer debug their first Kubernetes deployment",
        "Made progress on the side project - got authentication working",
        "Had a video call with parents, they're planning to visit next month",
    ],
    "something": [
        "User mentioned interest in learning Rust when they have more time",
        "User seems stressed about the upcoming product launch deadline",
        "User has been thinking about getting a mechanical keyboard",
        "User expressed frustration with the current project management tool",
        "User mentioned wanting to travel to Japan next year",
        "User has been considering adopting another dog as a companion for Max",
        "User seems interested in learning more about AI and machine learning",
        "User mentioned they might be looking for a new apartment soon",
        "User expressed interest in trying rock climbing",
        "User has been thinking about starting a tech blog",
        "User mentioned wanting to improve their system design skills",
        "User seems to enjoy collaborative problem-solving sessions",
        "User prefers detailed technical explanations over high-level overviews",
        "User values clean code and good documentation",
        "User mentioned interest in open source contributing",
    ],
    "preference": [
        "Prefers concise responses rather than lengthy explanations",
        "Likes when code examples include comments explaining the logic",
        "Prefers ES6+ syntax over older JavaScript patterns",
        "Likes having context about why a solution works, not just the solution",
        "Prefers async/await over callback-based patterns",
        "Likes structured responses with bullet points for complex topics",
        "Prefers incremental refactoring over big rewrites",
        "Likes having test examples alongside code solutions",
        "Prefers explicit type annotations in TypeScript",
        "Likes having alternatives presented when there are multiple approaches",
    ],
    "other": [
        "Mentioned enjoying board games, especially Wingspan and Terraforming Mars",
        "Has a home espresso machine that they're still learning to use",
        "Mentioned listening to lo-fi beats while coding",
        "Has a standing desk but alternates between sitting and standing",
        "Uses Vim keybindings in VS Code",
        "Has multiple monitors for coding - main for code, secondary for docs",
        "Mentioned enjoying hiking on weekends when the weather is good",
        "Plays guitar as a hobby, mostly acoustic indie songs",
        "Mentioned being interested in home automation projects",
        "Collects vinyl records, mostly indie and electronic music",
    ],
}


async def setup_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register JSONB codec for the connection."""
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def get_relationship_id(
    conn: asyncpg.Connection,
    companion_id: UUID,
    user_id: str,
) -> UUID:
    """Get or create a relationship for the companion + user."""
    relationship, created = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=companion_id,
        user_id=user_id,
    )
    if created:
        print(f"Created new relationship: {relationship.id}")
    else:
        print(f"Using existing relationship: {relationship.id}")
    return relationship.id


def generate_memories(count: int) -> list[tuple[str, str | None]]:
    """
    Generate a list of (content, type) tuples.

    Returns varied memories across all types with realistic distribution:
    - identity: ~15%
    - daily: ~30%
    - something: ~25%
    - preference: ~15%
    - other: ~15%
    """
    weights = {
        "identity": 0.15,
        "daily": 0.30,
        "something": 0.25,
        "preference": 0.15,
        "other": 0.15,
    }

    memories = []
    types = list(SAMPLE_MEMORIES.keys())
    type_weights = [weights[t] for t in types]

    for _ in range(count):
        memory_type = random.choices(types, weights=type_weights, k=1)[0]
        content = random.choice(SAMPLE_MEMORIES[memory_type])
        # Add some variation by appending a random suffix occasionally
        if random.random() < SUFFIX_VARIATION_PROBABILITY:
            suffixes = [
                " (mentioned in passing)",
                " - this came up in our conversation",
                "",
                "",
                "",
            ]
            content += random.choice(suffixes)
        memories.append((content, memory_type))

    return memories


async def seed_memories(
    companion_id: str,
    user_id: str = "test_user_seed",
    count: int = 50,
    clear_existing: bool = False,
) -> None:
    """Seed test memories for a companion."""
    # Load environment
    load_dotenv()

    database_url = os.getenv("DATABASE_DSN")
    if not database_url:
        print("ERROR: DATABASE_DSN environment variable is required")
        print("Set it in .env file or pass directly: DATABASE_DSN='...' uv run python ...")
        sys.exit(1)

    # Validate companion_id format
    try:
        companion_uuid = UUID(companion_id)
    except ValueError:
        print(f"ERROR: Invalid companion_id format: {companion_id}")
        print("Expected a valid UUID, e.g., 550e8400-e29b-41d4-a716-446655440000")
        sys.exit(1)

    print("Connecting to database...")
    conn = await asyncpg.connect(database_url)
    await setup_jsonb_codec(conn)

    try:
        # Verify companion exists
        companion = await conn.fetchrow(
            "SELECT id, name FROM companions WHERE id = $1",
            companion_uuid,
        )
        if not companion:
            print(f"ERROR: Companion {companion_id} not found")
            sys.exit(1)

        print(f"Found companion: {companion['name']} ({companion['id']})")

        # Get or create relationship
        relationship_id = await get_relationship_id(conn, companion_uuid, user_id)

        # Clear existing memories if requested
        if clear_existing:
            deleted = await MemoryV2Repository.clear_all(conn, relationship_id)
            print(f"Cleared {deleted} existing memories")

        # Check current count
        current_count = await MemoryV2Repository.get_entry_count(conn, relationship_id)
        print(f"Current memory count: {current_count}")

        # Generate and insert memories
        memories = generate_memories(count)
        print(f"Inserting {count} test memories...")

        inserted = 0
        for content, memory_type in memories:
            try:
                await MemoryV2Repository.create_entry(
                    conn,
                    relationship_id,
                    content,
                    memory_type,
                )
                inserted += 1
                if inserted % 10 == 0:
                    print(f"  Inserted {inserted}/{count}...")
            except Exception as e:
                print(f"  Warning: Failed to insert memory: {e}")

        # Final count
        final_count = await MemoryV2Repository.get_entry_count(conn, relationship_id)
        print(f"\nDone! Final memory count: {final_count}")
        print(f"Inserted {inserted} new memories for user '{user_id}'")

    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Seed test memories for a companion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    uv run python tests/seed_test_memories.py 550e8400-e29b-41d4-a716-446655440000
    uv run python tests/seed_test_memories.py 550e8400-... --user-id custom_user
    uv run python tests/seed_test_memories.py 550e8400-... --count 100 --clear
        """,
    )
    parser.add_argument(
        "companion_id",
        help="UUID of the companion to seed memories for",
    )
    parser.add_argument(
        "--user-id",
        default="test_user_seed",
        help="User ID for the relationship (default: test_user_seed)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of memories to create (default: 50)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing memories before seeding",
    )

    args = parser.parse_args()

    asyncio.run(
        seed_memories(
            companion_id=args.companion_id,
            user_id=args.user_id,
            count=args.count,
            clear_existing=args.clear,
        )
    )


if __name__ == "__main__":
    main()
