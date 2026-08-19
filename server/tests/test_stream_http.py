"""Test HTTP streaming endpoint with Server-Sent Events (SSE).

Requires the server to be running. Tests both legacy and layered engines via headers.

Run server first:  uv run uvicorn app.main:app --reload
Then run test:     uv run python tests/test_stream_http.py

Requires httpx-sse: uv add httpx-sse (or just use httpx with manual SSE parsing)
"""

import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

import asyncio
import json
import os
from uuid import uuid4

import httpx

# Config
BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")  # Your project API key


async def get_test_companion_id():
    """Get a companion ID from the database directly."""
    from app.db import init_db

    pool = await init_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, project_id FROM companions LIMIT 1")
        if row:
            # Also get API key for project
            key_row = await conn.fetchrow(
                """
                SELECT prefix FROM project_api_keys
                WHERE project_id = $1 AND status = 'active'
                LIMIT 1
            """,
                row["project_id"],
            )
            return row["id"], row["project_id"], key_row
    return None, None, None


async def parse_sse_stream(response):
    """Parse Server-Sent Events from httpx response."""
    events = []
    current_event = {"event": None, "data": None, "id": None}

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line:
            # Empty line = end of event
            if current_event["event"] or current_event["data"]:
                events.append(current_event.copy())
                current_event = {"event": None, "data": None, "id": None}
            continue

        if line.startswith("event:"):
            current_event["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current_event["data"] = line[5:].strip()
        elif line.startswith("id:"):
            current_event["id"] = line[3:].strip()

    # Don't forget last event if no trailing newline
    if current_event["event"] or current_event["data"]:
        events.append(current_event)

    return events


async def test_stream_legacy(client, companion_id, api_key):
    """Test streaming with legacy engine."""
    print("\n[TEST] Stream - Legacy Engine")
    print("-" * 40)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "external_user_id": f"test-stream-{uuid4().hex[:8]}",
        "message": "Hello, this is a streaming test.",
        "model": "openai-gpt4o-mini",
        "temperature": 0.7,
    }

    try:
        async with client.stream(
            "POST",
            f"{BASE_URL}/v1/companions/{companion_id}/chat/stream",
            headers=headers,
            json=payload,
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                print(f"  FAIL: HTTP {response.status_code}")
                return False

            events = await parse_sse_stream(response)

            print(f"  Events received: {len(events)}")
            event_types = [e["event"] for e in events if e["event"]]
            print(f"  Event types: {list(set(event_types))}")

            # Check for expected events
            has_ack = any(e["event"] == "ack" for e in events)
            has_done = any(e["event"] == "done" for e in events)
            has_delta = any(e["event"] == "delta" for e in events)

            print(f"  Has ack: {has_ack}")
            print(f"  Has delta: {has_delta}")
            print(f"  Has done: {has_done}")

            # Print error if present
            error_event = next((e for e in events if e["event"] == "error"), None)
            if error_event and error_event["data"]:
                try:
                    error_data = json.loads(error_event["data"])
                    print(f"  ERROR: {error_data}")
                except json.JSONDecodeError:
                    print(f"  ERROR (raw): {error_event['data']}")

            # Get final content from message event (not done - done just has IDs)
            message_event = next((e for e in events if e["event"] == "message"), None)
            if message_event and message_event["data"]:
                try:
                    message_data = json.loads(message_event["data"])
                    content = (
                        message_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    )
                    print(
                        f"  Response preview: {content[:180]}{'...' if len(content) > 180 else ''}"
                    )
                except json.JSONDecodeError:
                    pass

            if has_ack and has_done:
                print("  PASS")
                return True
            else:
                print("  FAIL: Missing expected events")
                return False

    except httpx.TimeoutException:
        print("  FAIL: Request timeout")
        return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


async def test_stream_layered(client, companion_id, api_key):
    """Test streaming with layered engine."""
    print("\n[TEST] Stream - Layered Engine")
    print("-" * 40)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Context-Engine": "layered",  # <-- Enable layered engine
    }

    payload = {
        "external_user_id": f"test-stream-{uuid4().hex[:8]}",
        "message": "Tell me something interesting.",
        "model": "openai-gpt4o-mini",
        "temperature": 0.7,
    }

    try:
        async with client.stream(
            "POST",
            f"{BASE_URL}/v1/companions/{companion_id}/chat/stream",
            headers=headers,
            json=payload,
            timeout=30.0,
        ) as response:
            if response.status_code != 200:
                print(f"  FAIL: HTTP {response.status_code}")
                return False

            events = await parse_sse_stream(response)

            print(f"  Events received: {len(events)}")

            # Group by event type
            event_counts = {}
            for e in events:
                t = e["event"] or "unknown"
                event_counts[t] = event_counts.get(t, 0) + 1
            print(f"  Event counts: {json.dumps(event_counts)}")

            # Look for layer events (memory:start, knowledge:start, etc.)
            layer_events = [e for e in events if e["event"] and ":" in e["event"]]
            print(f"  Layer events: {len(layer_events)}")
            if layer_events:
                print(f"  Layer event types: {list({e['event'] for e in layer_events})[:10]}")

            # Check for expected events
            has_ack = any(e["event"] == "ack" for e in events)
            has_done = any(e["event"] == "done" for e in events)

            # Print error if present
            error_event = next((e for e in events if e["event"] == "error"), None)
            if error_event and error_event["data"]:
                try:
                    error_data = json.loads(error_event["data"])
                    print(f"  ERROR: {error_data}")
                except json.JSONDecodeError:
                    print(f"  ERROR (raw): {error_event['data']}")

            # Get metadata from message event (not done - done just has IDs)
            message_event = next((e for e in events if e["event"] == "message"), None)
            if message_event and message_event["data"]:
                try:
                    message_data = json.loads(message_event["data"])
                    meta = (
                        message_data.get("choices", [{}])[0]
                        .get("emotion_machine", {})
                        .get("metadata", {})
                    )
                    print(f"  Context engine: {meta.get('context_engine')}")
                    print(f"  Build ms: {meta.get('build_ms')}")
                    content = (
                        message_data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    )
                    print(
                        f"  Response preview: {content[:180]}{'...' if len(content) > 180 else ''}"
                    )
                except json.JSONDecodeError:
                    pass

            if has_ack and has_done:
                print("  PASS")
                return True
            else:
                print("  FAIL: Missing expected events")
                return False

    except httpx.TimeoutException:
        print("  FAIL: Request timeout")
        return False
    except Exception as e:
        print(f"  FAIL: {e}")
        return False


async def main():
    print("=" * 60)
    print("HTTP STREAMING ENDPOINT TESTS")
    print("=" * 60)

    # Get companion and check for API key
    companion_id, project_id, key_info = await get_test_companion_id()

    if not companion_id:
        print("ERROR: No companions in DB")
        return False

    print(f"\nCompanion ID: {companion_id}")
    print(f"Project ID: {project_id}")

    # Use provided API key or warn
    api_key = API_KEY
    if not api_key:
        print("\nWARNING: No TEST_API_KEY set in environment.")
        print("Set it via: export TEST_API_KEY=your-api-key")
        if key_info:
            print(f"A key exists with prefix: {key_info['prefix']}...")
        print("\nYou can also test with curl:")
        print(f"""
curl -X POST "{BASE_URL}/v1/companions/{companion_id}/chat/stream" \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -H "X-Context-Engine: layered" \\
  -d '{{"external_user_id": "test", "message": "Hello"}}'
""")
        return False

    print(f"Base URL: {BASE_URL}")

    async with httpx.AsyncClient() as client:
        # Test basic connectivity
        try:
            health = await client.get(f"{BASE_URL}/healthz", timeout=5.0)
            if health.status_code != 200:
                print(f"\nERROR: Server not healthy at {BASE_URL}")
                return False
            print("Server health: OK")
        except Exception:
            print(f"\nERROR: Cannot connect to server at {BASE_URL}")
            print("Make sure server is running: uv run uvicorn app.main:app --reload")
            return False

        results = []
        results.append(("Legacy Stream", await test_stream_legacy(client, companion_id, api_key)))
        results.append(("Layered Stream", await test_stream_layered(client, companion_id, api_key)))

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        for name, passed in results:
            status = "PASS" if passed else "FAIL"
            print(f"  {name}: {status}")

        return all(r[1] for r in results)


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
