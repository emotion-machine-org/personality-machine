"""Tests for v2 Relationships API.

Run with: uv run python tests/test_v2_relationships.py
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


def test_ensure_relationship_creates_new():
    """PUT creates a new relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        response = client.put(url, headers=_headers(), json={})
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()

        assert data["user_id"] == user_id
        assert data["companion_id"] == COMPANION_ID
        assert "id" in data
        assert data["version"] == 0

        relationship_id = data["id"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_ensure_relationship_creates_new")


def test_ensure_relationship_is_idempotent():
    """PUT is idempotent."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        response1 = client.put(url, headers=_headers(), json={})
        assert response1.status_code == 200
        data1 = response1.json()

        response2 = client.put(url, headers=_headers(), json={})
        assert response2.status_code == 200
        data2 = response2.json()

        assert data1["id"] == data2["id"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{data1['id']}", headers=_headers())

    print("✓ test_ensure_relationship_is_idempotent")


def test_get_relationship_by_user():
    """GET by user retrieves a relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(url, headers=_headers(), json={})
        relationship_id = put_response.json()["id"]

        get_response = client.get(url, headers=_headers())
        assert get_response.status_code == 200
        assert get_response.json()["id"] == relationship_id

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_get_relationship_by_user")


def test_get_relationship_by_id():
    """GET by ID retrieves a relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(put_url, headers=_headers(), json={})
        relationship_id = put_response.json()["id"]

        get_url = f"{BASE_URL}/v2/relationships/{relationship_id}"
        get_response = client.get(get_url, headers=_headers())
        assert get_response.status_code == 200
        assert get_response.json()["id"] == relationship_id

        # Cleanup
        client.delete(get_url, headers=_headers())

    print("✓ test_get_relationship_by_id")


def test_delete_relationship():
    """DELETE removes a relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(put_url, headers=_headers(), json={})
        relationship_id = put_response.json()["id"]

        delete_url = f"{BASE_URL}/v2/relationships/{relationship_id}"
        delete_response = client.delete(delete_url, headers=_headers())
        assert delete_response.status_code == 204

        get_response = client.get(delete_url, headers=_headers())
        assert get_response.status_code == 404

    print("✓ test_delete_relationship")


def test_list_relationships():
    """GET list returns relationships."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"
    list_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(put_url, headers=_headers(), json={})
        relationship_id = put_response.json()["id"]

        list_response = client.get(list_url, headers=_headers())
        assert list_response.status_code == 200
        data = list_response.json()
        assert "items" in data
        assert "total" in data

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_list_relationships")


def test_patch_user_state():
    """PATCH state updates user state."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(put_url, headers=_headers(), json={})
        relationship_id = put_response.json()["id"]

        state_url = f"{BASE_URL}/v2/relationships/{relationship_id}/state"
        patch_response = client.patch(
            state_url,
            headers=_headers(),
            json={"scope": "user", "changes": {"name": "Test User", "age": 25}},
        )
        assert patch_response.status_code == 200
        data = patch_response.json()
        assert data["user"]["name"] == "Test User"
        assert data["user"]["age"] == 25

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_patch_user_state")


def test_app_state_crud():
    """App state GET/PUT/PATCH/DELETE operations."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(
            put_url,
            headers=_headers(),
            json={"app_state": {"initial": "value"}},
        )
        relationship_id = put_response.json()["id"]
        app_state_url = f"{BASE_URL}/v2/relationships/{relationship_id}/app-state"

        # GET
        get_response = client.get(app_state_url, headers=_headers())
        assert get_response.status_code == 200
        assert get_response.json()["app_state"]["initial"] == "value"

        # PUT (replace)
        put_state_response = client.put(
            app_state_url,
            headers=_headers(),
            json={"replaced": True},
        )
        assert put_state_response.status_code == 200
        assert put_state_response.json()["app_state"]["replaced"]
        assert "initial" not in put_state_response.json()["app_state"]

        # PATCH (merge)
        patch_response = client.patch(
            app_state_url,
            headers=_headers(),
            json={"added": "key"},
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["app_state"]["replaced"]
        assert patch_response.json()["app_state"]["added"] == "key"

        # DELETE (clear)
        delete_response = client.delete(app_state_url, headers=_headers())
        assert delete_response.status_code == 200
        assert delete_response.json()["app_state"] == {}

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_app_state_crud")


def test_config_crud():
    """Config GET/PATCH operations."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        put_response = client.put(
            put_url,
            headers=_headers(),
            json={"config": {"model": "gpt-4o"}},
        )
        relationship_id = put_response.json()["id"]
        config_url = f"{BASE_URL}/v2/relationships/{relationship_id}/config"

        # GET
        get_response = client.get(config_url, headers=_headers())
        assert get_response.status_code == 200
        assert get_response.json()["config"]["model"] == "gpt-4o"

        # PATCH
        patch_response = client.patch(
            config_url,
            headers=_headers(),
            json={"temperature": 0.9},
        )
        assert patch_response.status_code == 200
        assert patch_response.json()["config"]["model"] == "gpt-4o"
        assert patch_response.json()["config"]["temperature"] == 0.9

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_config_crud")


def test_config_resolved():
    """GET /config/resolved returns merged companion + relationship config."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    put_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    with httpx.Client(timeout=20.0) as client:
        # Create relationship with config override
        put_response = client.put(
            put_url,
            headers=_headers(),
            json={"config": {"include_profile_in_prompt": True}},
        )
        assert put_response.status_code == 200
        relationship_id = put_response.json()["id"]

        # GET /config/resolved
        resolved_url = f"{BASE_URL}/v2/relationships/{relationship_id}/config/resolved"
        resolved_response = client.get(resolved_url, headers=_headers())
        assert resolved_response.status_code == 200

        data = resolved_response.json()

        # Should have all three fields
        assert "config" in data
        assert "companion_config" in data
        assert "relationship_overrides" in data

        # Relationship override should be reflected
        assert data["relationship_overrides"].get("include_profile_in_prompt") is True

        # Merged config should include the override
        assert data["config"].get("include_profile_in_prompt") is True

        # Companion config should have system_prompt (from companion)
        assert "system_prompt" in data["companion_config"]

        # Cleanup
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())

    print("✓ test_config_resolved")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}\n")

    test_ensure_relationship_creates_new()
    test_ensure_relationship_is_idempotent()
    test_get_relationship_by_user()
    test_get_relationship_by_id()
    test_delete_relationship()
    test_list_relationships()
    test_patch_user_state()
    test_app_state_crud()
    test_config_crud()
    test_config_resolved()

    print("\n✅ All tests passed!")
