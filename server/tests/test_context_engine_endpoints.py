"""Test script for context engine endpoints.

Tests both legacy and layered context engines on chat endpoints.
Run from server directory: uv run python tests/test_context_engine_endpoints.py
"""

import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
from uuid import uuid4

from app.db import init_db
from app.repositories.companion import CompanionRepository
from app.repositories.conversation import (
    get_conversation_by_id,
    set_conversation_context_engine,
)
from app.routers.client_api import (
    ChatRequest,
    _chat_layered,
    _chat_legacy,
)


async def test_chat_endpoints():
    pool = await init_db()
    async with pool.acquire() as conn:
        # Get a companion to test with
        row = await conn.fetchrow("SELECT id, owner_id FROM companions LIMIT 1")
        if not row:
            print("ERROR: No companions in DB - create one first")
            return False

        companion_id = row["id"]
        row["owner_id"]

        # Get full companion detail
        companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
        if not companion:
            print("ERROR: Could not load companion")
            return False

        # Get project for this companion (project_id is now directly on companions table)
        project_row = await conn.fetchrow(
            "SELECT project_id FROM companions WHERE id = $1",
            companion_id,
        )
        if not project_row or not project_row["project_id"]:
            print("ERROR: No project linked to companion")
            return False

        project_id = project_row["project_id"]
        test_user = f"test-user-{uuid4().hex[:8]}"

        print(f"Testing with companion: {companion_id}")
        print(f"Project: {project_id}")
        print(f"Test user: {test_user}")
        print("-" * 60)

        # Create test payload
        payload = ChatRequest(
            external_user_id=test_user,
            message="Hello, this is a test message.",
            model="openai-gpt4o-mini",
            temperature=0.7,
        )

        # ── Test 1: Legacy endpoint ──────────────────────────────────────
        print("\n[TEST 1] Legacy chat endpoint")
        try:
            # Create conversation for legacy test
            from app.repositories.conversation import create_conversation_for_companion

            legacy_conv_id = await create_conversation_for_companion(conn, companion_id, test_user)
            await set_conversation_context_engine(conn, legacy_conv_id, "legacy")

            # Add user message
            from app.repositories.conversation import add_message_returning

            await add_message_returning(
                conn, legacy_conv_id, "user", payload.message, input_modality="text"
            )

            response = await _chat_legacy(
                companion=companion,
                companion_id=companion_id,
                conversation_id=legacy_conv_id,
                payload=payload,
                project_id=project_id,
                conn=conn,
            )

            meta = response.choices[0].emotion_machine.get("metadata", {})
            content = response.choices[0].message.content
            print("  Status: OK")
            print(f"  Context engine: {meta.get('context_engine')}")
            print(f"  Build ms: {meta.get('build_ms', 'N/A')}")
            print(f"  Response length: {len(content)} chars")
            print(f"  Response preview: {content[:180]}{'...' if len(content) > 180 else ''}")

            # Verify conversation has context_engine set
            conv = await get_conversation_by_id(conn, legacy_conv_id)
            print(f"  Conversation context_engine in DB: {conv.get('context_engine', 'NOT SET')}")

            if meta.get("context_engine") != "legacy":
                print("  FAIL: Expected context_engine='legacy'")
                return False

            print("  PASS")

        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback

            traceback.print_exc()
            return False

        # ── Test 2: Layered endpoint ─────────────────────────────────────
        print("\n[TEST 2] Layered chat endpoint")
        try:
            # Create conversation for layered test
            layered_conv_id = await create_conversation_for_companion(conn, companion_id, test_user)
            await set_conversation_context_engine(conn, layered_conv_id, "layered")

            # Add user message
            await add_message_returning(
                conn, layered_conv_id, "user", payload.message, input_modality="text"
            )

            response = await _chat_layered(
                companion=companion,
                companion_id=companion_id,
                conversation_id=layered_conv_id,
                payload=payload,
                project_id=project_id,
                conn=conn,
            )

            meta = response.choices[0].emotion_machine.get("metadata", {})
            content = response.choices[0].message.content
            print("  Status: OK")
            print(f"  Context engine: {meta.get('context_engine')}")
            print(f"  Build ms: {meta.get('build_ms', 'N/A')}")
            print(f"  Response length: {len(content)} chars")
            print(f"  Response preview: {content[:180]}{'...' if len(content) > 180 else ''}")

            # Verify conversation has context_engine set
            conv = await get_conversation_by_id(conn, layered_conv_id)
            print(f"  Conversation context_engine in DB: {conv.get('context_engine', 'NOT SET')}")

            if meta.get("context_engine") != "layered":
                print("  FAIL: Expected context_engine='layered'")
                return False

            if meta.get("build_ms") is None:
                print("  FAIL: Expected build_ms to be set")
                return False

            print("  PASS")

        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback

            traceback.print_exc()
            return False

        # ── Test 3: Verify build_ms in messages table ────────────────────
        print("\n[TEST 3] Verify build_ms stored in messages table")
        try:
            # Check layered conversation's assistant message
            msg_row = await conn.fetchrow(
                """
                SELECT build_ms FROM messages
                WHERE conversation_id = $1 AND role = 'assistant'
                ORDER BY created_at DESC LIMIT 1
                """,
                layered_conv_id,
            )
            if msg_row and msg_row["build_ms"] is not None:
                print(f"  build_ms in DB: {msg_row['build_ms']} ms")
                print("  PASS")
            else:
                print(f"  FAIL: build_ms not stored (got: {msg_row})")
                return False

        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback

            traceback.print_exc()
            return False

        # ── Cleanup ──────────────────────────────────────────────────────
        print("\n[CLEANUP] Removing test conversations")
        try:
            await conn.execute("DELETE FROM conversations WHERE id = $1", legacy_conv_id)
            await conn.execute("DELETE FROM conversations WHERE id = $1", layered_conv_id)
            print("  Done")
        except Exception as e:
            print(f"  Warning: Cleanup failed: {e}")

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        return True


if __name__ == "__main__":
    success = asyncio.run(test_chat_endpoints())
    sys.exit(0 if success else 1)
