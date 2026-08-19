"""Integration tests for websocket intro-message behavior.

Run with:
    uv run python tests/test_v2_ws_intro_message.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from uuid import UUID, uuid4

import asyncpg
import httpx
import websockets
from dotenv import load_dotenv

load_dotenv()

BASE_URL = (
    os.getenv("EM_BASE_URL") or os.getenv("EM_API_BASE_URL") or "http://localhost:8100"
).rstrip("/")
WS_BASE_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
API_KEY = os.getenv("TEST_EM_API_KEY")
DATABASE_DSN = os.getenv("DATABASE_DSN")
INTRO_METADATA_KEY = "intro_message_sent_at"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


async def _count_intro_messages(relationship_id: UUID, intro_text: str) -> int:
    conn = await asyncpg.connect(DATABASE_DSN)
    try:
        count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM messages
            WHERE relationship_id = $1
              AND role = 'assistant'
              AND content = $2
            """,
            relationship_id,
            intro_text,
        )
        return int(count or 0)
    finally:
        await conn.close()


async def _get_intro_marker(relationship_id: UUID) -> str | None:
    conn = await asyncpg.connect(DATABASE_DSN)
    try:
        return await conn.fetchval(
            """
            SELECT metadata ->> $2
            FROM relationships
            WHERE id = $1
            """,
            relationship_id,
            INTRO_METADATA_KEY,
        )
    finally:
        await conn.close()


async def test_ws_intro_message_sent_once() -> None:
    companion_id: str | None = None
    relationship_id: str | None = None
    intro_text = f"Hi from intro test {uuid4().hex[:8]}"
    user_id = f"intro-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            create_resp = await client.post(
                f"{BASE_URL}/v1/companions",
                headers=_headers(),
                json={
                    "name": f"intro-test-{uuid4().hex[:8]}",
                    "config": {
                        "intro_message": {
                            "enabled": True,
                            "text": intro_text,
                            "send_once_per_relationship": True,
                        }
                    },
                },
            )
            assert create_resp.status_code == 201, (
                f"Failed to create companion: {create_resp.status_code} {create_resp.text}"
            )
            companion_id = create_resp.json()["id"]

            token_resp_1 = await client.post(
                f"{BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}/ws-token",
                headers=_headers(),
            )
            assert token_resp_1.status_code == 200, token_resp_1.text
            token_1 = token_resp_1.json()["token"]
            relationship_id = token_resp_1.json()["relationship_id"]

            ws_url_1 = (
                f"{WS_BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}/connect"
                f"?token={token_1}"
            )
            async with websockets.connect(ws_url_1, close_timeout=5) as ws:
                connected_event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert connected_event["type"] == "connected"
                assert connected_event["data"]["relationship_id"] == relationship_id

                intro_event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert intro_event["type"] == "message", intro_event
                assert intro_event["data"]["role"] == "assistant"
                assert intro_event["data"]["content"] == intro_text
                assert intro_event["seq"] is not None

            # Reconnect to same relationship: intro should not be sent again
            token_resp_2 = await client.post(
                f"{BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}/ws-token",
                headers=_headers(),
            )
            assert token_resp_2.status_code == 200, token_resp_2.text
            token_2 = token_resp_2.json()["token"]
            assert token_resp_2.json()["relationship_id"] == relationship_id

            ws_url_2 = (
                f"{WS_BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}/connect"
                f"?token={token_2}"
            )
            async with websockets.connect(ws_url_2, close_timeout=5) as ws:
                connected_again = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                assert connected_again["type"] == "connected"

                try:
                    extra = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.5))
                    assert extra["type"] != "message", (
                        "Expected no second intro message, but received a message event"
                    )
                except TimeoutError:
                    # Expected: no intro message after reconnect
                    pass

            intro_count = await _count_intro_messages(UUID(relationship_id), intro_text)
            assert intro_count == 1, f"Expected exactly 1 intro message, got {intro_count}"

            marker = await _get_intro_marker(UUID(relationship_id))
            assert marker is not None, "Expected intro metadata marker to be set"
        finally:
            if relationship_id:
                await client.delete(
                    f"{BASE_URL}/v2/relationships/{relationship_id}",
                    headers=_headers(),
                )
            if companion_id:
                await client.delete(
                    f"{BASE_URL}/v1/companions/{companion_id}",
                    headers=_headers(),
                )

    print("✓ test_ws_intro_message_sent_once")


def run_async_test(test_func) -> None:
    asyncio.run(test_func())


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not DATABASE_DSN:
        print("ERROR: Set DATABASE_DSN environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}\n")
    run_async_test(test_ws_intro_message_sent_once)
    print("\n✅ websocket intro-message tests passed!")
