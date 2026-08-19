"""Tests for v2 Sessions API.

Run with: uv run python tests/test_v2_sessions.py
"""

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


def _create_relationship(client: httpx.Client) -> tuple[str, str]:
    """Create a test relationship and return (relationship_id, user_id)."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"
    response = client.put(url, headers=_headers(), json={})
    assert response.status_code == 200
    return response.json()["id"], user_id


def _cleanup_relationship(client: httpx.Client, relationship_id: str) -> None:
    """Delete a test relationship."""
    client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())


def test_create_session():
    """POST creates a new session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            response = client.post(url, headers=_headers(), json={"type": "coaching"})

            assert response.status_code == 201, (
                f"Expected 201, got {response.status_code}: {response.text}"
            )
            data = response.json()

            assert data["relationship_id"] == relationship_id
            assert data["type"] == "coaching"
            assert data["status"] == "active"
            assert data["isolated"] is False
            assert data["state"] == {}
            assert "id" in data
            assert "created_at" in data

            print("  session_id:", data["id"])
        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_create_session")


def test_create_isolated_session():
    """POST creates an isolated session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            response = client.post(
                url, headers=_headers(), json={"type": "therapy", "isolated": True}
            )

            assert response.status_code == 201
            data = response.json()

            assert data["type"] == "therapy"
            assert data["isolated"] is True

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_create_isolated_session")


def test_duplicate_active_session_fails():
    """Cannot create a second active session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"

            # Create first session
            response1 = client.post(url, headers=_headers(), json={})
            assert response1.status_code == 201

            # Try to create second session
            response2 = client.post(url, headers=_headers(), json={})
            assert response2.status_code == 409, f"Expected 409, got {response2.status_code}"

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_duplicate_active_session_fails")


def test_list_sessions():
    """GET list returns sessions."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"

            # Create a session
            create_response = client.post(
                sessions_url, headers=_headers(), json={"type": "coaching"}
            )
            assert create_response.status_code == 201
            session_id = create_response.json()["id"]

            # End it so we can create another
            end_url = f"{BASE_URL}/v2/sessions/{session_id}/end"
            client.post(end_url, headers=_headers())

            # Create another
            create_response2 = client.post(
                sessions_url, headers=_headers(), json={"type": "therapy"}
            )
            assert create_response2.status_code == 201

            # List sessions
            list_response = client.get(sessions_url, headers=_headers())
            assert list_response.status_code == 200
            data = list_response.json()

            assert "sessions" in data
            assert "total" in data
            assert data["total"] >= 2

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_list_sessions")


def test_get_session_by_id():
    """GET by ID retrieves a session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(
                sessions_url, headers=_headers(), json={"type": "coaching"}
            )
            session_id = create_response.json()["id"]

            get_url = f"{BASE_URL}/v2/sessions/{session_id}"
            get_response = client.get(get_url, headers=_headers())

            assert get_response.status_code == 200
            assert get_response.json()["id"] == session_id

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_get_session_by_id")


def test_get_active_session():
    """GET active session returns current active session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(
                sessions_url, headers=_headers(), json={"type": "coaching"}
            )
            session_id = create_response.json()["id"]

            active_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions/active"
            active_response = client.get(active_url, headers=_headers())

            assert active_response.status_code == 200
            assert active_response.json()["id"] == session_id

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_get_active_session")


def test_get_active_session_none():
    """GET active session returns null when no active session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            active_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions/active"
            active_response = client.get(active_url, headers=_headers())

            assert active_response.status_code == 200
            assert active_response.json() is None

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_get_active_session_none")


def test_end_session():
    """POST end session ends and generates summary."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(
                sessions_url, headers=_headers(), json={"type": "coaching"}
            )
            session_id = create_response.json()["id"]

            end_url = f"{BASE_URL}/v2/sessions/{session_id}/end"
            end_response = client.post(end_url, headers=_headers())

            assert end_response.status_code == 200
            data = end_response.json()
            assert data["status"] == "ended"
            assert "ended_at" in data
            # Summary may be empty if no messages in session
            assert "summary" in data

            # Verify session is now ended
            get_response = client.get(f"{BASE_URL}/v2/sessions/{session_id}", headers=_headers())
            assert get_response.json()["status"] == "ended"

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_end_session")


def test_end_already_ended_session_fails():
    """Cannot end an already ended session."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(sessions_url, headers=_headers(), json={})
            session_id = create_response.json()["id"]

            end_url = f"{BASE_URL}/v2/sessions/{session_id}/end"
            client.post(end_url, headers=_headers())

            # Try to end again
            end_response2 = client.post(end_url, headers=_headers())
            assert end_response2.status_code == 400

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_end_already_ended_session_fails")


def test_patch_session_state():
    """PATCH state merges changes."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(sessions_url, headers=_headers(), json={})
            session_id = create_response.json()["id"]

            state_url = f"{BASE_URL}/v2/sessions/{session_id}/state"
            patch_response = client.patch(
                state_url, headers=_headers(), json={"changes": {"foo": "bar", "count": 1}}
            )

            assert patch_response.status_code == 200
            data = patch_response.json()
            assert data["state"]["foo"] == "bar"
            assert data["state"]["count"] == 1

            # Patch again to merge
            patch_response2 = client.patch(
                state_url, headers=_headers(), json={"changes": {"baz": "qux"}}
            )
            data2 = patch_response2.json()
            assert data2["state"]["foo"] == "bar"
            assert data2["state"]["baz"] == "qux"

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_patch_session_state")


def test_patch_isolated_session_state_fails():
    """Cannot patch state of isolated session."""
    with httpx.Client(timeout=20.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(sessions_url, headers=_headers(), json={"isolated": True})
            session_id = create_response.json()["id"]

            state_url = f"{BASE_URL}/v2/sessions/{session_id}/state"
            patch_response = client.patch(
                state_url, headers=_headers(), json={"changes": {"foo": "bar"}}
            )

            assert patch_response.status_code == 400

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_patch_isolated_session_state_fails")


def test_patch_ended_session_state_fails():
    """Cannot patch state of ended session."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(sessions_url, headers=_headers(), json={})
            session_id = create_response.json()["id"]

            # End the session
            end_url = f"{BASE_URL}/v2/sessions/{session_id}/end"
            client.post(end_url, headers=_headers())

            # Try to patch
            state_url = f"{BASE_URL}/v2/sessions/{session_id}/state"
            patch_response = client.patch(
                state_url, headers=_headers(), json={"changes": {"foo": "bar"}}
            )

            assert patch_response.status_code == 400

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_patch_ended_session_state_fails")


def test_message_with_session_id():
    """Messages can be sent with a session_id."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            # Create a session
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(
                sessions_url, headers=_headers(), json={"type": "coaching"}
            )
            session_id = create_response.json()["id"]

            # Send a message with session_id
            message_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            msg_response = client.post(
                message_url,
                headers=_headers(),
                json={"content": "Hello!", "session_id": session_id},
            )

            assert msg_response.status_code == 200
            data = msg_response.json()
            assert data["message"]["session_id"] == session_id

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_message_with_session_id")


def test_message_with_invalid_session_id_fails():
    """Messages with invalid session_id fail."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            message_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            msg_response = client.post(
                message_url,
                headers=_headers(),
                json={"content": "Hello!", "session_id": str(uuid4())},
            )

            assert msg_response.status_code == 404

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_message_with_invalid_session_id_fails")


def test_message_with_ended_session_fails():
    """Messages with ended session_id fail."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            # Create and end a session
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"
            create_response = client.post(sessions_url, headers=_headers(), json={})
            session_id = create_response.json()["id"]

            end_url = f"{BASE_URL}/v2/sessions/{session_id}/end"
            client.post(end_url, headers=_headers())

            # Try to send message with ended session
            message_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            msg_response = client.post(
                message_url,
                headers=_headers(),
                json={"content": "Hello!", "session_id": session_id},
            )

            assert msg_response.status_code == 400

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_message_with_ended_session_fails")


def test_session_pagination():
    """Session list supports cursor pagination."""
    with httpx.Client(timeout=30.0) as client:
        relationship_id, _user_id = _create_relationship(client)
        try:
            sessions_url = f"{BASE_URL}/v2/relationships/{relationship_id}/sessions"

            # Create 3 sessions
            for i in range(3):
                create_response = client.post(
                    sessions_url, headers=_headers(), json={"type": f"session-{i}"}
                )
                session_id = create_response.json()["id"]
                # End each to allow creating the next
                client.post(f"{BASE_URL}/v2/sessions/{session_id}/end", headers=_headers())

            # List with limit=2
            list_response = client.get(f"{sessions_url}?limit=2", headers=_headers())
            data = list_response.json()

            assert len(data["sessions"]) == 2
            assert data["total"] == 3
            assert data["next_cursor"] is not None

            # Get second page
            list_response2 = client.get(
                f"{sessions_url}?limit=2&cursor={data['next_cursor']}", headers=_headers()
            )
            data2 = list_response2.json()

            assert len(data2["sessions"]) == 1
            assert data2["next_cursor"] is None

        finally:
            _cleanup_relationship(client, relationship_id)

    print("  test_session_pagination")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}\n")

    test_create_session()
    test_create_isolated_session()
    test_duplicate_active_session_fails()
    test_list_sessions()
    test_get_session_by_id()
    test_get_active_session()
    test_get_active_session_none()
    test_end_session()
    test_end_already_ended_session_fails()
    test_patch_session_state()
    test_patch_isolated_session_state_fails()
    test_patch_ended_session_state_fails()
    test_message_with_session_id()
    test_message_with_invalid_session_id_fails()
    test_message_with_ended_session_fails()
    test_session_pagination()

    print("\n All tests passed!")
