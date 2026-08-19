"""Tests for v2 Behaviors API.

Run with: uv run python tests/test_v2_behaviors.py

Phase 5: Tests for the Behaviors system which enables developer-defined logic
that runs during conversations.
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


def _create_relationship(client: httpx.Client) -> dict:
    """Helper to create a test relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"
    response = client.put(url, headers=_headers(), json={})
    assert response.status_code == 200, f"Failed to create relationship: {response.text}"
    return response.json()


def _cleanup_relationship(client: httpx.Client, relationship_id: str):
    """Helper to cleanup a test relationship."""
    client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())


# -----------------------------------------------------------------------------
# Companion-Level Behavior Tests
# -----------------------------------------------------------------------------


def test_list_companion_behaviors_empty():
    """GET behaviors returns empty list for companion with no behaviors."""
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        response = client.get(url, headers=_headers())
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert "behaviors" in data
        assert "total" in data
        assert data["companion_id"] == COMPANION_ID

    print("✓ test_list_companion_behaviors_empty")


def test_create_companion_behavior():
    """POST creates a new behavior link."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        payload = {
            "behavior_key": behavior_key,
            "triggers": ["always"],
            "priority": True,
            "enabled": True,
            "classifier_hint": "Test behavior for greeting",
        }
        response = client.post(url, headers=_headers(), json=payload)
        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert data["behavior_key"] == behavior_key
        assert data["priority"]
        assert data["enabled"]
        assert "always" in data["triggers"]
        assert data["companion_id"] == COMPANION_ID

        # Cleanup
        client.delete(f"{url}/{behavior_key}", headers=_headers())

    print("✓ test_create_companion_behavior")


def test_get_companion_behavior():
    """GET retrieves a specific behavior link."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    base_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Create
        payload = {"behavior_key": behavior_key, "triggers": ["every:5"]}
        create_response = client.post(base_url, headers=_headers(), json=payload)
        assert create_response.status_code == 201

        # Get
        get_url = f"{base_url}/{behavior_key}"
        get_response = client.get(get_url, headers=_headers())
        assert get_response.status_code == 200
        data = get_response.json()

        assert data["behavior_key"] == behavior_key
        assert "every:5" in data["triggers"]

        # Cleanup
        client.delete(get_url, headers=_headers())

    print("✓ test_get_companion_behavior")


def test_update_companion_behavior():
    """PATCH updates a behavior link."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    base_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Create
        payload = {"behavior_key": behavior_key, "triggers": ["always"], "priority": False}
        client.post(base_url, headers=_headers(), json=payload)

        # Update
        update_url = f"{base_url}/{behavior_key}"
        update_response = client.patch(
            update_url,
            headers=_headers(),
            json={"triggers": ["every:3", "keyword:hello,hi"], "priority": True},
        )
        assert update_response.status_code == 200
        data = update_response.json()

        assert data["priority"]
        assert "every:3" in data["triggers"]
        assert "keyword:hello,hi" in data["triggers"]

        # Cleanup
        client.delete(update_url, headers=_headers())

    print("✓ test_update_companion_behavior")


def test_delete_companion_behavior():
    """DELETE removes a behavior link."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    base_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Create
        payload = {"behavior_key": behavior_key, "triggers": ["always"]}
        client.post(base_url, headers=_headers(), json=payload)

        # Delete
        delete_url = f"{base_url}/{behavior_key}"
        delete_response = client.delete(delete_url, headers=_headers())
        assert delete_response.status_code == 204

        # Verify deletion
        get_response = client.get(delete_url, headers=_headers())
        assert get_response.status_code == 404

    print("✓ test_delete_companion_behavior")


def test_create_duplicate_behavior_returns_conflict():
    """POST with same behavior_key returns 409 Conflict."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        payload = {"behavior_key": behavior_key, "triggers": ["always"]}

        # First create succeeds
        response1 = client.post(url, headers=_headers(), json=payload)
        assert response1.status_code == 201

        # Second create should fail with conflict
        response2 = client.post(url, headers=_headers(), json=payload)
        assert response2.status_code == 409

        # Cleanup
        client.delete(f"{url}/{behavior_key}", headers=_headers())

    print("✓ test_create_duplicate_behavior_returns_conflict")


def test_trigger_shorthand_parsing():
    """Test various trigger shorthand formats are accepted."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        payload = {
            "behavior_key": behavior_key,
            "triggers": [
                "always",
                "every:10",
                "turn:1,5,10",
                "keyword:anxious,stressed,worried",
            ],
            "priority": True,
        }
        response = client.post(url, headers=_headers(), json=payload)
        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )
        data = response.json()

        # Verify triggers are stored and returned correctly
        triggers = data["triggers"]
        assert "always" in triggers
        assert "every:10" in triggers
        assert "turn:1,5,10" in triggers
        assert "keyword:anxious,stressed,worried" in triggers

        # Cleanup
        client.delete(f"{url}/{behavior_key}", headers=_headers())

    print("✓ test_trigger_shorthand_parsing")


# -----------------------------------------------------------------------------
# Relationship-Level Behavior Override Tests
# -----------------------------------------------------------------------------


def test_create_relationship_behavior_override():
    """POST creates a relationship-specific behavior override."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    companion_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # First create a relationship
        relationship = _create_relationship(client)
        relationship_id = relationship["id"]

        # Create companion-level behavior first
        client.post(
            companion_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"]},
        )

        # Now create relationship-level override
        rel_url = f"{BASE_URL}/v2/relationships/{relationship_id}/behaviors"
        override_payload = {
            "behavior_key": behavior_key,
            "triggers": ["every:3"],  # Different trigger
            "priority": True,
        }
        response = client.post(rel_url, headers=_headers(), json=override_payload)
        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert data["relationship_id"] == relationship_id
        assert "every:3" in data["triggers"]
        assert data["priority"]

        # Cleanup
        client.delete(f"{rel_url}/{behavior_key}", headers=_headers())
        client.delete(f"{companion_url}/{behavior_key}", headers=_headers())
        _cleanup_relationship(client, relationship_id)

    print("✓ test_create_relationship_behavior_override")


def test_list_relationship_behavior_overrides():
    """GET lists relationship-specific behavior overrides."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    companion_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Create relationship
        relationship = _create_relationship(client)
        relationship_id = relationship["id"]

        # Create behavior at companion level, then override at relationship level
        client.post(
            companion_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"]},
        )

        rel_url = f"{BASE_URL}/v2/relationships/{relationship_id}/behaviors"
        client.post(
            rel_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["keyword:test"]},
        )

        # List relationship overrides
        list_response = client.get(rel_url, headers=_headers())
        assert list_response.status_code == 200
        data = list_response.json()

        assert "behaviors" in data
        assert data["relationship_id"] == relationship_id
        assert len(data["behaviors"]) >= 1

        # Cleanup
        client.delete(f"{rel_url}/{behavior_key}", headers=_headers())
        client.delete(f"{companion_url}/{behavior_key}", headers=_headers())
        _cleanup_relationship(client, relationship_id)

    print("✓ test_list_relationship_behavior_overrides")


def test_get_relationship_behavior_override():
    """GET retrieves a specific relationship behavior override."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    companion_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Setup
        relationship = _create_relationship(client)
        relationship_id = relationship["id"]

        client.post(
            companion_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"]},
        )

        rel_url = f"{BASE_URL}/v2/relationships/{relationship_id}/behaviors"
        client.post(
            rel_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["turn:1,2,3"], "priority": True},
        )

        # Get the override
        get_response = client.get(f"{rel_url}/{behavior_key}", headers=_headers())
        assert get_response.status_code == 200
        data = get_response.json()

        assert data["behavior_key"] == behavior_key
        assert data["relationship_id"] == relationship_id
        assert "turn:1,2,3" in data["triggers"]

        # Cleanup
        client.delete(f"{rel_url}/{behavior_key}", headers=_headers())
        client.delete(f"{companion_url}/{behavior_key}", headers=_headers())
        _cleanup_relationship(client, relationship_id)

    print("✓ test_get_relationship_behavior_override")


def test_update_relationship_behavior_override():
    """PATCH updates a relationship behavior override."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    companion_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Setup
        relationship = _create_relationship(client)
        relationship_id = relationship["id"]

        client.post(
            companion_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"]},
        )

        rel_url = f"{BASE_URL}/v2/relationships/{relationship_id}/behaviors"
        client.post(
            rel_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"], "enabled": True},
        )

        # Update the override
        update_response = client.patch(
            f"{rel_url}/{behavior_key}",
            headers=_headers(),
            json={"enabled": False, "triggers": ["every:100"]},
        )
        assert update_response.status_code == 200
        data = update_response.json()

        assert not data["enabled"]
        assert "every:100" in data["triggers"]

        # Cleanup
        client.delete(f"{rel_url}/{behavior_key}", headers=_headers())
        client.delete(f"{companion_url}/{behavior_key}", headers=_headers())
        _cleanup_relationship(client, relationship_id)

    print("✓ test_update_relationship_behavior_override")


def test_delete_relationship_behavior_override():
    """DELETE removes a relationship behavior override."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    companion_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Setup
        relationship = _create_relationship(client)
        relationship_id = relationship["id"]

        client.post(
            companion_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"]},
        )

        rel_url = f"{BASE_URL}/v2/relationships/{relationship_id}/behaviors"
        client.post(
            rel_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["every:5"]},
        )

        # Delete the override
        delete_response = client.delete(f"{rel_url}/{behavior_key}", headers=_headers())
        assert delete_response.status_code == 204

        # Verify deletion
        get_response = client.get(f"{rel_url}/{behavior_key}", headers=_headers())
        assert get_response.status_code == 404

        # Companion-level behavior should still exist
        companion_get = client.get(f"{companion_url}/{behavior_key}", headers=_headers())
        assert companion_get.status_code == 200

        # Cleanup
        client.delete(f"{companion_url}/{behavior_key}", headers=_headers())
        _cleanup_relationship(client, relationship_id)

    print("✓ test_delete_relationship_behavior_override")


# -----------------------------------------------------------------------------
# Behavior Definition Update Tests
# -----------------------------------------------------------------------------


def test_update_behavior_definition():
    """PATCH definition updates the behavior source code."""
    behavior_key = f"test-behavior-{uuid4().hex[:8]}"
    base_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    with httpx.Client(timeout=20.0) as client:
        # Create behavior
        client.post(
            base_url,
            headers=_headers(),
            json={"behavior_key": behavior_key, "triggers": ["always"]},
        )

        # Update definition
        definition_url = f"{base_url}/{behavior_key}/definition"
        update_response = client.patch(
            definition_url,
            headers=_headers(),
            json={
                "name": "Updated Behavior Name",
                "description": "This behavior does something interesting",
                "source_code": "def run(ctx):\n    return {'prompt_block': 'Hello from behavior'}",
                "dependencies": ["requests"],
                "timeout_seconds": 30,
            },
        )
        assert update_response.status_code == 200, (
            f"Expected 200, got {update_response.status_code}: {update_response.text}"
        )
        data = update_response.json()

        assert data["name"] == "Updated Behavior Name"
        assert data["description"] == "This behavior does something interesting"
        assert "def run(ctx)" in data["source_code"]
        assert "requests" in data["dependencies"]
        assert data["timeout_seconds"] == 30

        # Cleanup
        client.delete(f"{base_url}/{behavior_key}", headers=_headers())

    print("✓ test_update_behavior_definition")


# -----------------------------------------------------------------------------
# Error Handling Tests
# -----------------------------------------------------------------------------


def test_get_nonexistent_behavior_returns_404():
    """GET for nonexistent behavior returns 404."""
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/nonexistent-behavior"

    with httpx.Client(timeout=20.0) as client:
        response = client.get(url, headers=_headers())
        assert response.status_code == 404

    print("✓ test_get_nonexistent_behavior_returns_404")


def test_delete_nonexistent_behavior_returns_404():
    """DELETE for nonexistent behavior returns 404."""
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/nonexistent-behavior"

    with httpx.Client(timeout=20.0) as client:
        response = client.delete(url, headers=_headers())
        assert response.status_code == 404

    print("✓ test_delete_nonexistent_behavior_returns_404")


def test_relationship_override_without_behavior_returns_404():
    """POST relationship override for nonexistent behavior returns 404."""
    with httpx.Client(timeout=20.0) as client:
        relationship = _create_relationship(client)
        relationship_id = relationship["id"]

        rel_url = f"{BASE_URL}/v2/relationships/{relationship_id}/behaviors"
        response = client.post(
            rel_url,
            headers=_headers(),
            json={"behavior_key": "nonexistent-behavior", "triggers": ["always"]},
        )
        assert response.status_code == 404

        _cleanup_relationship(client, relationship_id)

    print("✓ test_relationship_override_without_behavior_returns_404")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}\n")

    # Companion-level behavior tests
    test_list_companion_behaviors_empty()
    test_create_companion_behavior()
    test_get_companion_behavior()
    test_update_companion_behavior()
    test_delete_companion_behavior()
    test_create_duplicate_behavior_returns_conflict()
    test_trigger_shorthand_parsing()

    # Relationship-level behavior override tests
    test_create_relationship_behavior_override()
    test_list_relationship_behavior_overrides()
    test_get_relationship_behavior_override()
    test_update_relationship_behavior_override()
    test_delete_relationship_behavior_override()

    # Behavior definition tests
    test_update_behavior_definition()

    # Error handling tests
    test_get_nonexistent_behavior_returns_404()
    test_delete_nonexistent_behavior_returns_404()
    test_relationship_override_without_behavior_returns_404()

    print("\n✅ All behavior tests passed!")
