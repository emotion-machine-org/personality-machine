"""Tests for v2 WebSockets API.

Run with: uv run python tests/test_v2_websockets.py
"""

import asyncio
import json
import os
import sys
from uuid import uuid4

import httpx
import websockets
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
WS_BASE_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def test_ws_token_endpoint():
    """POST ws-token creates a token for WebSocket auth."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, headers=_headers())
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

        data = response.json()
        assert "token" in data
        assert "relationship_id" in data
        assert "expires_in" in data
        assert data["expires_in"] == 3600  # 1 hour

        relationship_id = data["relationship_id"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_ws_token_endpoint")


def test_ws_token_creates_relationship():
    """POST ws-token creates relationship if it doesn't exist."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=30.0) as client:
        # First, verify relationship doesn't exist
        check = client.get(rel_url, headers=_headers())
        assert check.status_code == 404

        # Get token (should create relationship)
        response = client.post(token_url, headers=_headers())
        assert response.status_code == 200

        data = response.json()
        relationship_id = data["relationship_id"]

        # Now relationship should exist
        check2 = client.get(rel_url, headers=_headers())
        assert check2.status_code == 200
        assert check2.json()["id"] == relationship_id

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_ws_token_creates_relationship")


async def test_ws_connect_and_receive_connected_event():
    """WebSocket connects and receives connected event."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect WebSocket
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                # Should receive connected event
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
                assert event["data"]["relationship_id"] == relationship_id
                assert event["data"]["companion_id"] == COMPANION_ID
                assert event["data"]["user_id"] == user_id
        finally:
            # Cleanup
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_connect_and_receive_connected_event")


async def test_ws_ping_pong():
    """WebSocket responds to ping with pong."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send ping
                await ws.send(json.dumps({"type": "ping"}))

                # Should receive pong
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "pong"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_ping_pong")


async def test_ws_send_message():
    """WebSocket send message receives ack, delta, and message events."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=60) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send a message
                client_message_id = f"msg-{uuid4().hex[:8]}"
                await ws.send(
                    json.dumps(
                        {
                            "type": "user_message",
                            "client_message_id": client_message_id,
                            "content": "Hello! Say 'pong' back.",
                        }
                    )
                )

                # Collect events until we get the final message
                events = []
                event_types = set()
                timeout_at = asyncio.get_event_loop().time() + 30

                while "message" not in event_types:
                    if asyncio.get_event_loop().time() > timeout_at:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        event = json.loads(raw)
                        events.append(event)
                        event_types.add(event["type"])
                    except TimeoutError:
                        break

                # Verify we got the expected events
                assert "ack" in event_types, f"Expected ack event, got: {event_types}"
                assert "message" in event_types, f"Expected message event, got: {event_types}"

                # Verify ack event
                ack_event = next(e for e in events if e["type"] == "ack")
                assert ack_event["data"]["client_message_id"] == client_message_id
                assert "turn_id" in ack_event["data"]
                assert "message_id" in ack_event["data"]

                # Verify message event
                msg_event = next(e for e in events if e["type"] == "message")
                assert msg_event["data"]["role"] == "assistant"
                assert len(msg_event["data"]["content"]) > 0
                assert msg_event["seq"] is not None

        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_send_message")


async def test_ws_invalid_token():
    """WebSocket rejects invalid token."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # First create relationship so we can test token validation
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        relationship_id = data["relationship_id"]

        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token=invalid-token"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                # Should be closed immediately
                await asyncio.wait_for(ws.recv(), timeout=5)
                raise AssertionError("Expected connection to be closed")
        except websockets.exceptions.ConnectionClosed as e:
            # Should close with 4001 (invalid token)
            assert e.code == 4001, f"Expected close code 4001, got {e.code}"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_invalid_token")


async def test_ws_unknown_message_type():
    """WebSocket returns error for unknown message types."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send unknown type
                await ws.send(json.dumps({"type": "unknown_type"}))

                # Should receive error
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "error"
                assert event["data"]["code"] == "unknown_message_type"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_unknown_message_type")


async def test_ws_direct_endpoint():
    """WebSocket direct endpoint works with relationship_id."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token first
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect via direct endpoint
        ws_url = f"{WS_BASE_URL}/v2/relationships/{relationship_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                # Should receive connected event
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
                assert event["data"]["relationship_id"] == relationship_id
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_direct_endpoint")


async def test_ws_message_flow_complete():
    """Complete WebSocket message flow with all event types."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=60) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send a message
                client_message_id = f"msg-{uuid4().hex[:8]}"
                await ws.send(
                    json.dumps(
                        {
                            "type": "user_message",
                            "client_message_id": client_message_id,
                            "content": "Tell me a very short joke.",
                        }
                    )
                )

                # Collect all events
                events = []
                timeout_at = asyncio.get_event_loop().time() + 45

                while True:
                    if asyncio.get_event_loop().time() > timeout_at:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2)
                        event = json.loads(raw)
                        events.append(event)

                        # Stop when we get the final message
                        if event["type"] == "message":
                            break
                    except TimeoutError:
                        break

                # Verify event flow
                event_types = [e["type"] for e in events]

                # Must have: ack, (status events), delta (streaming), message
                assert "ack" in event_types, f"Missing ack: {event_types}"
                assert "message" in event_types, f"Missing message: {event_types}"

                # Verify all durable events have seq
                ack_event = next(e for e in events if e["type"] == "ack")
                msg_event = next(e for e in events if e["type"] == "message")

                assert ack_event["seq"] is not None, "ack should have seq"
                assert msg_event["seq"] is not None, "message should have seq"

                # Delta events should not have seq (ephemeral)
                delta_events = [e for e in events if e["type"] == "delta"]
                for d in delta_events:
                    assert d["seq"] is None, "delta should not have seq"

        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_message_flow_complete")


# -----------------------------------------------------------------------------
# Header-Based Authentication Tests
# -----------------------------------------------------------------------------


async def test_ws_auth_via_header():
    """WebSocket connects with token in Authorization header (mobile client flow)."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect WebSocket with token in header (NO query param)
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": f"Bearer {token}"}
            ) as ws:
                # Should receive connected event
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
                assert event["data"]["relationship_id"] == relationship_id
                assert event["data"]["companion_id"] == COMPANION_ID
                assert event["data"]["user_id"] == user_id
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_via_header")


async def test_ws_auth_via_header_direct_endpoint():
    """WebSocket direct endpoint accepts token in Authorization header."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect via direct endpoint with header auth
        ws_url = f"{WS_BASE_URL}/v2/relationships/{relationship_id}/connect"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": f"Bearer {token}"}
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
                assert event["data"]["relationship_id"] == relationship_id
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_via_header_direct_endpoint")


async def test_ws_auth_header_takes_precedence():
    """When both header and query param provided, header takes precedence."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        valid_token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect with valid header and INVALID query param
        # If header takes precedence, connection should succeed
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token=invalid-query-token"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": f"Bearer {valid_token}"}
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                # Should succeed because header (valid) takes precedence
                assert event["type"] == "connected"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_header_takes_precedence")


async def test_ws_auth_missing_token():
    """WebSocket rejects connection when no token provided at all."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create relationship first
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        relationship_id = response.json()["relationship_id"]

        # Connect WITHOUT any token (no header, no query param)
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                raise AssertionError("Expected connection to be closed")
        except websockets.exceptions.ConnectionClosed as e:
            # Should close with 4001 (invalid/missing token)
            assert e.code == 4001, f"Expected close code 4001, got {e.code}"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_missing_token")


async def test_ws_auth_invalid_header_format():
    """WebSocket rejects non-Bearer authorization header."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get valid token for setup
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect with "Basic" auth instead of "Bearer" (and no query param)
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": f"Basic {token}"}
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                raise AssertionError("Expected connection to be closed")
        except websockets.exceptions.ConnectionClosed as e:
            # Should close with 4001 (missing token - Basic is not recognized)
            assert e.code == 4001, f"Expected close code 4001, got {e.code}"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_invalid_header_format")


async def test_ws_auth_header_case_insensitive():
    """Authorization header with 'bearer' (lowercase) should work."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect with lowercase "bearer" (should still work)
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": f"bearer {token}"}
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_header_case_insensitive")


async def test_ws_auth_header_with_extra_whitespace():
    """Authorization header with extra whitespace should work."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect with extra whitespace around token
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": f"Bearer   {token}  "}
            ) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_header_with_extra_whitespace")


async def test_ws_auth_header_empty_bearer():
    """Authorization header with empty Bearer token should fail."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create relationship first
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        relationship_id = response.json()["relationship_id"]

        # Connect with empty Bearer token
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect"

        try:
            async with websockets.connect(
                ws_url, close_timeout=5, extra_headers={"Authorization": "Bearer "}
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=5)
                raise AssertionError("Expected connection to be closed")
        except websockets.exceptions.ConnectionClosed as e:
            # Should close with 4001 (empty token treated as missing)
            assert e.code == 4001, f"Expected close code 4001, got {e.code}"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_header_empty_bearer")


async def test_ws_auth_query_param_backward_compat():
    """Query param auth still works (backward compatibility for browser clients)."""
    user_id = f"test-user-{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get token
        token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
        response = await client.post(token_url, headers=_headers())
        assert response.status_code == 200
        data = response.json()
        token = data["token"]
        relationship_id = data["relationship_id"]

        # Connect with query param only (no header)
        ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

        try:
            async with websockets.connect(ws_url, close_timeout=5) as ws:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(raw)

                assert event["type"] == "connected"
        finally:
            await client.delete(
                f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers()
            )

    print("✓ test_ws_auth_query_param_backward_compat")


def run_async_test(test_func):
    """Helper to run async tests."""
    asyncio.run(test_func())


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}\n")

    # Sync tests
    test_ws_token_endpoint()
    test_ws_token_creates_relationship()

    # Async tests - Core functionality
    run_async_test(test_ws_connect_and_receive_connected_event)
    run_async_test(test_ws_ping_pong)
    run_async_test(test_ws_invalid_token)
    run_async_test(test_ws_unknown_message_type)
    run_async_test(test_ws_direct_endpoint)
    run_async_test(test_ws_send_message)
    run_async_test(test_ws_message_flow_complete)

    # Header-based authentication tests
    print("\n--- Header-Based Auth Tests ---")
    run_async_test(test_ws_auth_via_header)
    run_async_test(test_ws_auth_via_header_direct_endpoint)
    run_async_test(test_ws_auth_header_takes_precedence)
    run_async_test(test_ws_auth_missing_token)
    run_async_test(test_ws_auth_invalid_header_format)
    run_async_test(test_ws_auth_header_case_insensitive)
    run_async_test(test_ws_auth_header_with_extra_whitespace)
    run_async_test(test_ws_auth_header_empty_bearer)
    run_async_test(test_ws_auth_query_param_backward_compat)

    print("\n✅ All v2 WebSocket tests passed!")
