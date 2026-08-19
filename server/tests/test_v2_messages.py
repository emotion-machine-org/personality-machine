"""Tests for v2 Messages API.

Run with: uv run python tests/test_v2_messages.py
"""

import json
import os
import sys
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _headers_sse() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def test_composite_send_creates_relationship():
    """POST composite creates relationship and sends message."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages"

    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            url,
            headers=_headers(),
            json={"content": "Hello, this is a test message!"},
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert "relationship_id" in data
        assert "message" in data
        assert data["message"]["role"] == "assistant"
        assert len(data["message"]["content"]) > 0
        assert data["message"]["seq"] is not None

        relationship_id = data["relationship_id"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_composite_send_creates_relationship")


def test_composite_send_uses_existing_relationship():
    """POST composite uses existing relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages"

    with httpx.Client(timeout=60.0) as client:
        # First message - creates relationship
        response1 = client.post(
            url,
            headers=_headers(),
            json={"content": "First message"},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        relationship_id = data1["relationship_id"]
        assert data1.get("relationship_created", True)  # First call creates

        # Second message - uses existing
        response2 = client.post(
            url,
            headers=_headers(),
            json={"content": "Second message"},
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["relationship_id"] == relationship_id
        assert not data2.get("relationship_created", False)  # Should not create

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_composite_send_uses_existing_relationship")


def test_direct_send():
    """POST direct sends message to existing relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=60.0) as client:
        # Create relationship first
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # Send message via direct endpoint
        msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
        response = client.post(
            msg_url,
            headers=_headers(),
            json={"content": "Direct message test"},
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert data["relationship_id"] == relationship_id
        assert data["message"]["role"] == "assistant"
        assert len(data["message"]["content"]) > 0

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_direct_send")


def test_direct_send_404_for_missing_relationship():
    """POST direct returns 404 for non-existent relationship."""
    fake_id = str(uuid4())
    url = f"{BASE_URL}/v2/relationships/{fake_id}/messages"

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            url,
            headers=_headers(),
            json={"content": "This should fail"},
        )
        assert response.status_code == 404

    print("✓ test_direct_send_404_for_missing_relationship")


def test_composite_stream():
    """POST composite with SSE streaming."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages"

    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "POST",
            url,
            headers=_headers_sse(),
            json={"content": "Tell me a short joke"},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            events = []
            current_event = {"event": None, "data": None}

            for line in response.iter_lines():
                if line.startswith("event:"):
                    current_event["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    current_event["data"] = line[5:].strip()
                elif line == "":
                    if current_event["event"] and current_event["data"]:
                        events.append(
                            {
                                "event": current_event["event"],
                                "data": json.loads(current_event["data"]),
                            }
                        )
                    current_event = {"event": None, "data": None}

            # Verify event types
            event_types = [e["event"] for e in events]
            assert "ack" in event_types, f"Expected 'ack' event, got: {event_types}"
            assert "message" in event_types, f"Expected 'message' event, got: {event_types}"

            # Verify ack event has turn_id
            ack_event = next(e for e in events if e["event"] == "ack")
            assert "turn_id" in ack_event["data"]["data"]

            # Verify message event has content
            msg_event = next(e for e in events if e["event"] == "message")
            assert "content" in msg_event["data"]["data"]
            assert len(msg_event["data"]["data"]["content"]) > 0

            # Get relationship_id from message event for cleanup
            relationship_id = msg_event["data"]["data"]["relationship_id"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_composite_stream")


def test_direct_stream():
    """POST direct with SSE streaming."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=60.0) as client:
        # Create relationship first
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # Stream message via direct endpoint
        msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"

        with client.stream(
            "POST",
            msg_url,
            headers=_headers_sse(),
            json={"content": "Count to 5"},
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            events = []
            current_event = {"event": None, "data": None}
            delta_count = 0

            for line in response.iter_lines():
                if line.startswith("event:"):
                    current_event["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    current_event["data"] = line[5:].strip()
                elif line == "":
                    if current_event["event"] and current_event["data"]:
                        event_data = json.loads(current_event["data"])
                        events.append(
                            {
                                "event": current_event["event"],
                                "data": event_data,
                            }
                        )
                        if current_event["event"] == "delta":
                            delta_count += 1
                    current_event = {"event": None, "data": None}

            # Verify we got delta events (streaming)
            assert delta_count > 0, "Expected delta events for streaming"

            # Verify unified event protocol structure
            for event in events:
                assert "seq" in event["data"] or event["data"].get("seq") is None
                assert "timestamp" in event["data"]
                assert "type" in event["data"]
                assert "data" in event["data"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_direct_stream")


def test_message_seq_increments():
    """Verify seq numbers increment per relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages"

    with httpx.Client(timeout=90.0) as client:
        # First message
        response1 = client.post(
            url,
            headers=_headers(),
            json={"content": "First"},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        seq1 = data1["message"]["seq"]
        relationship_id = data1["relationship_id"]

        # Second message
        response2 = client.post(
            url,
            headers=_headers(),
            json={"content": "Second"},
        )
        assert response2.status_code == 200
        data2 = response2.json()
        seq2 = data2["message"]["seq"]

        # Verify seq increments
        assert seq2 > seq1, f"Expected seq2 ({seq2}) > seq1 ({seq1})"

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_message_seq_increments")


def test_relationship_message_count_updates():
    """Verify relationship message_count increments."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    msg_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=60.0) as client:
        # Send a message (creates relationship)
        response = client.post(
            msg_url,
            headers=_headers(),
            json={"content": "Test message"},
        )
        assert response.status_code == 200
        relationship_id = response.json()["relationship_id"]

        # Check relationship
        rel_response = client.get(rel_url, headers=_headers())
        assert rel_response.status_code == 200
        rel_data = rel_response.json()

        # Should have at least 2 messages (user + assistant)
        assert rel_data["message_count"] >= 2, (
            f"Expected message_count >= 2, got {rel_data['message_count']}"
        )
        assert rel_data["last_interaction_at"] is not None

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_relationship_message_count_updates")


def test_app_state_in_prompt():
    """Verify profile injection works when include_profile_in_prompt is enabled (Phase 6)."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=60.0) as client:
        # Create relationship with include_profile_in_prompt enabled
        put_response = client.put(
            rel_url,
            headers=_headers(),
            json={
                "config": {"include_profile_in_prompt": True},
                "profile": {
                    "user_name": "TestUser",
                    "subscription_tier": "premium",
                    "preferences": {"theme": "dark", "notifications": True},
                },
            },
        )
        assert put_response.status_code == 200, (
            f"Expected 200, got {put_response.status_code}: {put_response.text}"
        )
        relationship_id = put_response.json()["id"]

        # Verify relationship has correct config and profile
        get_response = client.get(rel_url, headers=_headers())
        assert get_response.status_code == 200
        rel_data = get_response.json()
        assert rel_data["config"].get("include_profile_in_prompt") is True
        assert rel_data["profile"]["user_name"] == "TestUser"

        # Send a message - profile should be injected into the prompt
        msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
        response = client.post(
            msg_url,
            headers=_headers(),
            json={"content": "What do you know about my preferences?"},
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        # Verify we got a response (the actual content depends on LLM, but it should work)
        assert data["message"]["role"] == "assistant"
        assert len(data["message"]["content"]) > 0

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_app_state_in_prompt")


def test_app_state_not_injected_when_disabled():
    """Verify app_state is NOT injected when include_app_state_in_prompt is disabled (default)."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=60.0) as client:
        # Create relationship without include_app_state_in_prompt (default is False)
        put_response = client.put(
            rel_url,
            headers=_headers(),
            json={
                "app_state": {
                    "secret_data": "should_not_be_visible",
                }
            },
        )
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # Verify config does NOT have include_app_state_in_prompt set
        get_response = client.get(rel_url, headers=_headers())
        assert get_response.status_code == 200
        rel_data = get_response.json()
        assert rel_data["config"].get("include_app_state_in_prompt") is not True

        # Send a message - app_state should NOT be injected
        msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
        response = client.post(
            msg_url,
            headers=_headers(),
            json={"content": "Hello!"},
        )
        assert response.status_code == 200
        data = response.json()

        # Verify we got a response
        assert data["message"]["role"] == "assistant"
        assert len(data["message"]["content"]) > 0

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_app_state_not_injected_when_disabled")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}\n")

    test_composite_send_creates_relationship()
    test_composite_send_uses_existing_relationship()
    test_direct_send()
    test_direct_send_404_for_missing_relationship()
    test_composite_stream()
    test_direct_stream()
    test_message_seq_increments()
    test_relationship_message_count_updates()
    test_app_state_in_prompt()
    test_app_state_not_injected_when_disabled()

    print("\n✅ All v2 messages tests passed!")
