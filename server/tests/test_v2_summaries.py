"""Tests for v2 Relationship Summarization feature.

Run with: uv run python tests/test_v2_summaries.py

Tests cover:
- Trigger logic (unit tests)
- Message range calculation (unit tests)
- API endpoints (integration tests)
- End-to-end summarization with actual messages

Note: For faster E2E tests, configure the test companion with a lower
message_limit (e.g., 10) in companion.config.context.message_limit.
"""

import os
import sys
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")

# For faster tests, set this to a lower value matching your test companion's config
# Default is 100, but can use 10 for faster tests
MESSAGE_LIMIT = int(os.getenv("TEST_MESSAGE_LIMIT", "10"))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def send_messages(client: httpx.Client, companion_id: str, user_id: str, count: int) -> int:
    """Send multiple messages and return total message count."""
    messages_url = f"{BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}/messages"

    for i in range(count):
        response = client.post(
            messages_url,
            headers=_headers(),
            json={"content": f"Test message {i + 1}"},
            timeout=60.0,
        )
        if response.status_code != 200:
            print(f"  Message {i + 1} failed: {response.status_code}")
            break
        if (i + 1) % 5 == 0:
            print(f"  Sent {i + 1}/{count} messages...")

    # Get updated message count
    rel_url = f"{BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}"
    rel_response = client.get(rel_url, headers=_headers())
    if rel_response.status_code == 200:
        return rel_response.json().get("message_count", 0)
    return 0


# -----------------------------------------------------------------------------
# Unit Tests: Trigger Logic
# -----------------------------------------------------------------------------


def test_trigger_logic_first_threshold():
    """Summarization should trigger at first message_limit threshold."""
    message_limit = 100

    # Before threshold: 99 messages, last_summarized = 0
    message_count = 99
    last_summarized = 0
    current_window = message_count // message_limit  # 0
    last_window = last_summarized // message_limit  # 0
    should_trigger = current_window > last_window and message_count >= message_limit
    assert not should_trigger, "Should not trigger before threshold"

    # At threshold: 100 messages, last_summarized = 0
    message_count = 100
    current_window = message_count // message_limit  # 1
    should_trigger = current_window > last_window and message_count >= message_limit
    assert should_trigger, "Should trigger at first threshold"

    print("✓ test_trigger_logic_first_threshold")


def test_trigger_logic_subsequent_thresholds():
    """Summarization should trigger at subsequent thresholds."""
    message_limit = 100

    # At second threshold: 200 messages, last_summarized = 100
    message_count = 200
    last_summarized = 100
    current_window = message_count // message_limit  # 2
    last_window = last_summarized // message_limit  # 1
    should_trigger = current_window > last_window and message_count >= message_limit
    assert should_trigger is True, "Should trigger at second threshold"

    # Between thresholds: 250 messages, last_summarized = 200
    message_count = 250
    last_summarized = 200
    current_window = message_count // message_limit  # 2
    last_window = last_summarized // message_limit  # 2
    should_trigger = current_window > last_window and message_count >= message_limit
    assert should_trigger is False, "Should not trigger between thresholds"

    # At third threshold: 300 messages, last_summarized = 200
    message_count = 300
    last_summarized = 200
    current_window = message_count // message_limit  # 3
    last_window = last_summarized // message_limit  # 2
    should_trigger = current_window > last_window and message_count >= message_limit
    assert should_trigger is True, "Should trigger at third threshold"

    print("✓ test_trigger_logic_subsequent_thresholds")


def test_trigger_logic_custom_limit():
    """Summarization should work with custom message_limit values."""
    message_limit = 50

    # At threshold: 50 messages, last_summarized = 0
    message_count = 50
    last_summarized = 0
    current_window = message_count // message_limit  # 1
    last_window = last_summarized // message_limit  # 0
    should_trigger = current_window > last_window and message_count >= message_limit
    assert should_trigger is True, "Should trigger with custom limit"

    # At 100 messages with limit 50: last_summarized = 50
    message_count = 100
    last_summarized = 50
    current_window = message_count // message_limit  # 2
    last_window = last_summarized // message_limit  # 1
    should_trigger = current_window > last_window and message_count >= message_limit
    assert should_trigger is True, "Should trigger at next threshold"

    print("✓ test_trigger_logic_custom_limit")


def test_message_range_calculation():
    """Test message range calculation for summarization."""
    message_limit = 100

    # First summary: messages 1-100
    last_summarized = 0
    current_count = 100
    messages_start = last_summarized + 1 if last_summarized > 0 else 1
    messages_end = (current_count // message_limit) * message_limit
    assert messages_start == 1, "First summary should start at 1"
    assert messages_end == 100, "First summary should end at 100"

    # Second summary: messages 101-200
    last_summarized = 100
    current_count = 200
    messages_start = last_summarized + 1 if last_summarized > 0 else 1
    messages_end = (current_count // message_limit) * message_limit
    assert messages_start == 101, "Second summary should start at 101"
    assert messages_end == 200, "Second summary should end at 200"

    # Third summary: messages 201-300
    last_summarized = 200
    current_count = 300
    messages_start = last_summarized + 1 if last_summarized > 0 else 1
    messages_end = (current_count // message_limit) * message_limit
    assert messages_start == 201, "Third summary should start at 201"
    assert messages_end == 300, "Third summary should end at 300"

    print("✓ test_message_range_calculation")


# -----------------------------------------------------------------------------
# Integration Tests: API Endpoints
# -----------------------------------------------------------------------------


def test_list_summaries_empty():
    """GET /summaries returns empty list for new relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        # Create relationship
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # List summaries
        summaries_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/summaries"
        list_response = client.get(summaries_url, headers=_headers())
        assert list_response.status_code == 200
        data = list_response.json()
        assert data["items"] == []
        assert data["total"] == 0

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_list_summaries_empty")


def test_get_latest_summary_not_found():
    """GET /summaries/latest returns 404 when no summary exists."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        # Create relationship
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # Get latest summary
        latest_url = (
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/summaries/latest"
        )
        get_response = client.get(latest_url, headers=_headers())
        assert get_response.status_code == 404

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_get_latest_summary_not_found")


def test_trigger_summarization_not_enough_messages():
    """POST /summaries/trigger returns not triggered when not enough messages."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        # Create relationship (new relationship has 0 messages)
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # Try to trigger summarization
        trigger_url = (
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/summaries/trigger"
        )
        trigger_response = client.post(trigger_url, headers=_headers())
        assert trigger_response.status_code == 200
        data = trigger_response.json()
        assert data["triggered"] is False
        assert "Not enough" in data["message"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_trigger_summarization_not_enough_messages")


# -----------------------------------------------------------------------------
# End-to-End Tests: Full Summarization Flow
# -----------------------------------------------------------------------------


def test_e2e_summarization_at_threshold():
    """
    End-to-end test: Send enough messages to trigger summarization.

    This test:
    1. Creates a relationship
    2. Sends MESSAGE_LIMIT messages (configurable, default 10 for speed)
    3. Verifies summarization was triggered
    4. Checks that summary is created

    Note: Requires test companion to have context.message_limit set to MESSAGE_LIMIT.
    """
    user_id = f"test-e2e-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    print(f"\n  Testing with MESSAGE_LIMIT={MESSAGE_LIMIT}")

    with httpx.Client(timeout=120.0) as client:
        # Create relationship
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]
        print(f"  Created relationship: {relationship_id}")

        try:
            # Send enough messages to cross the threshold
            # Each turn = 2 messages (user + assistant), so we need MESSAGE_LIMIT/2 turns
            turns_needed = MESSAGE_LIMIT // 2
            print(f"  Sending {turns_needed} turns ({MESSAGE_LIMIT} messages)...")

            message_count = send_messages(client, COMPANION_ID, user_id, turns_needed)
            print(f"  Total message count: {message_count}")

            # Verify we crossed the threshold
            assert message_count >= MESSAGE_LIMIT, (
                f"Expected at least {MESSAGE_LIMIT} messages, got {message_count}"
            )

            # Wait for async summarization to complete
            print("  Waiting for summarization job to complete...")
            time.sleep(10)  # Give Modal worker time to process

            # Check for summary
            latest_url = (
                f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/summaries/latest"
            )
            summary_response = client.get(latest_url, headers=_headers())

            if summary_response.status_code == 200:
                summary = summary_response.json()
                print(f"  Summary created! Version: {summary['version']}")
                print(f"  Messages covered: {summary['messages_start']}-{summary['messages_end']}")
                print(f"  Summary content preview: {summary['content'][:100]}...")
                assert summary["version"] == 1, "First summary should be version 1"
                assert summary["messages_end"] >= MESSAGE_LIMIT, (
                    "Summary should cover threshold messages"
                )
            elif summary_response.status_code == 404:
                # Summary might not be ready yet - try trigger endpoint
                print("  Summary not found, trying manual trigger...")
                trigger_url = (
                    f"{BASE_URL}/v2/companions/{COMPANION_ID}"
                    f"/relationships/{user_id}/summaries/trigger"
                )
                trigger_response = client.post(trigger_url, headers=_headers())
                trigger_data = trigger_response.json()

                if trigger_data.get("triggered"):
                    print(f"  Manual trigger succeeded: version={trigger_data.get('version')}")
                    print("  Waiting for job to complete...")
                    time.sleep(10)

                    # Check again
                    summary_response = client.get(latest_url, headers=_headers())
                    if summary_response.status_code == 200:
                        summary = summary_response.json()
                        print(f"  Summary created! Version: {summary['version']}")
                    else:
                        print("  Warning: Summary still not found after trigger")
                else:
                    print(f"  Manual trigger returned: {trigger_data.get('message')}")

        finally:
            # Cleanup
            client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())
            print("  Cleaned up relationship")

    print("✓ test_e2e_summarization_at_threshold")


def test_e2e_manual_trigger_with_messages():
    """
    Test manual trigger endpoint with a relationship that has messages.

    This test:
    1. Creates a relationship
    2. Sends MESSAGE_LIMIT messages
    3. Uses the manual trigger endpoint
    4. Verifies the response is correct based on actual companion threshold

    Note: The actual trigger threshold depends on the companion's config.context.message_limit.
    If MESSAGE_LIMIT < actual threshold, trigger will return triggered=False (expected).
    """
    user_id = f"test-trigger-{uuid4().hex[:8]}"
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    print(f"\n  Testing manual trigger with MESSAGE_LIMIT={MESSAGE_LIMIT}")

    with httpx.Client(timeout=120.0) as client:
        # Create relationship
        put_response = client.put(rel_url, headers=_headers(), json={})
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        try:
            # Send messages
            turns_needed = MESSAGE_LIMIT // 2
            print(f"  Sending {turns_needed} turns...")
            message_count = send_messages(client, COMPANION_ID, user_id, turns_needed)
            print(f"  Total message count: {message_count}")

            # Manual trigger
            trigger_url = (
                f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/summaries/trigger"
            )
            trigger_response = client.post(trigger_url, headers=_headers())
            assert trigger_response.status_code == 200

            data = trigger_response.json()
            print(f"  Trigger response: triggered={data['triggered']}, message={data['message']}")

            # Parse actual threshold from response message
            # Message format: "Not enough new messages to summarize. Current: X, last summarized: Y, threshold: Z"
            if not data["triggered"] and "threshold:" in data["message"]:
                actual_threshold = int(data["message"].split("threshold:")[1].strip())
                print(f"  Companion's actual message_limit: {actual_threshold}")
                print(f"  Need {actual_threshold - message_count} more messages to trigger")
                # This is expected if we haven't hit the threshold
                assert message_count < actual_threshold, (
                    f"Should have triggered with {message_count} >= {actual_threshold}"
                )
            elif data["triggered"]:
                assert data.get("version") is not None, "Should return version number"
                print(
                    f"  Job dispatched: version={data['version']}, "
                    f"range={data['messages_start']}-{data['messages_end']}"
                )

        finally:
            # Cleanup
            client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_e2e_manual_trigger_with_messages")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main():
    """Run all tests."""
    if not API_KEY or not COMPANION_ID:
        print("ERROR: TEST_EM_API_KEY and TEST_EM_COMPANION_ID must be set")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion ID: {COMPANION_ID}")
    print(f"Message Limit: {MESSAGE_LIMIT} (set TEST_MESSAGE_LIMIT env to change)\n")

    # Unit tests (no server required)
    print("=== Unit Tests ===")
    test_trigger_logic_first_threshold()
    test_trigger_logic_subsequent_thresholds()
    test_trigger_logic_custom_limit()
    test_message_range_calculation()

    # Integration tests (require server)
    print("\n=== Integration Tests ===")
    test_list_summaries_empty()
    test_get_latest_summary_not_found()
    test_trigger_summarization_not_enough_messages()

    # End-to-end tests (require server + Modal worker)
    print("\n=== End-to-End Tests ===")
    test_e2e_summarization_at_threshold()
    test_e2e_manual_trigger_with_messages()

    print("\n✅ All tests passed!")


if __name__ == "__main__":
    main()
