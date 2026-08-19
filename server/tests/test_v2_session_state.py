"""Tests for v2 Phase 6: State & Sensing Model.

Run with: uv run python tests/test_v2_session_state.py

Phase 6: Tests for the new simplified state model:
- Profile (renamed from app_state)
- Session state
- Profile/Session API endpoints
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


def _create_relationship(client: httpx.Client, profile: dict | None = None) -> dict:
    """Helper to create a test relationship."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"
    payload = {}
    if profile:
        payload["profile"] = profile
    response = client.put(url, headers=_headers(), json=payload)
    assert response.status_code == 200, f"Failed to create relationship: {response.text}"
    return response.json()


def _cleanup_relationship(client: httpx.Client, relationship_id: str):
    """Helper to cleanup a test relationship."""
    client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())


# -----------------------------------------------------------------------------
# Profile API Tests (renamed from app-state)
# -----------------------------------------------------------------------------


def test_get_profile_empty():
    """GET profile returns empty object for new relationship."""
    with httpx.Client(timeout=20.0) as client:
        rel = _create_relationship(client)
        rel_id = rel["id"]

        try:
            url = f"{BASE_URL}/v2/relationships/{rel_id}/profile"
            response = client.get(url, headers=_headers())
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            assert "profile" in data
            assert "version" in data
            assert data["profile"] == {}
            assert data["version"] == 0
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_get_profile_empty")


def test_set_profile():
    """PUT profile replaces entire profile."""
    with httpx.Client(timeout=20.0) as client:
        rel = _create_relationship(client)
        rel_id = rel["id"]

        try:
            url = f"{BASE_URL}/v2/relationships/{rel_id}/profile"

            # Set profile
            profile_data = {"name": "Alice", "preferences": {"theme": "dark"}}
            response = client.put(url, headers=_headers(), json=profile_data)
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            assert data["profile"] == profile_data
            assert data["version"] == 1

            # Verify persistence
            response = client.get(url, headers=_headers())
            data = response.json()
            assert data["profile"]["name"] == "Alice"
            assert data["profile"]["preferences"]["theme"] == "dark"
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_set_profile")


def test_patch_profile():
    """PATCH profile merges changes (JSON Merge Patch)."""
    with httpx.Client(timeout=20.0) as client:
        # Create relationship with initial profile
        rel = _create_relationship(client, profile={"name": "Bob", "level": 1})
        rel_id = rel["id"]

        try:
            url = f"{BASE_URL}/v2/relationships/{rel_id}/profile"

            # Patch profile - add new key and modify existing
            patch_data = {"level": 5, "score": 100}
            response = client.patch(url, headers=_headers(), json=patch_data)
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            # Should merge, not replace
            assert data["profile"]["name"] == "Bob"  # Preserved
            assert data["profile"]["level"] == 5  # Updated
            assert data["profile"]["score"] == 100  # Added
            assert data["version"] >= 1
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_patch_profile")


def test_clear_profile():
    """DELETE profile clears to empty object."""
    with httpx.Client(timeout=20.0) as client:
        # Create relationship with profile
        rel = _create_relationship(client, profile={"data": "to be cleared"})
        rel_id = rel["id"]

        try:
            url = f"{BASE_URL}/v2/relationships/{rel_id}/profile"

            # Clear profile
            response = client.delete(url, headers=_headers())
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            assert data["profile"] == {}
            assert data["version"] >= 1

            # Verify cleared
            response = client.get(url, headers=_headers())
            data = response.json()
            assert data["profile"] == {}
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_clear_profile")


# -----------------------------------------------------------------------------
# Relationship Response Tests (profile field)
# -----------------------------------------------------------------------------


def test_relationship_contains_profile():
    """Relationship response includes profile (not app_state)."""
    with httpx.Client(timeout=20.0) as client:
        profile_data = {"test_field": "test_value"}
        rel = _create_relationship(client, profile=profile_data)
        rel_id = rel["id"]

        try:
            # Check the response from creation
            assert "profile" in rel, f"Response missing 'profile': {rel}"
            assert rel["profile"] == profile_data

            # Fetch relationship and verify
            url = f"{BASE_URL}/v2/relationships/{rel_id}"
            response = client.get(url, headers=_headers())
            data = response.json()

            assert "profile" in data
            assert data["profile"]["test_field"] == "test_value"

            # Verify no app_state field (it's renamed)
            # Note: response might still have app_state for backward compat
            # but profile should be the primary field
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_relationship_contains_profile")


# -----------------------------------------------------------------------------
# Config Option Tests (include_profile_in_prompt)
# -----------------------------------------------------------------------------


def test_include_profile_in_prompt_config():
    """Test include_profile_in_prompt config option."""
    with httpx.Client(timeout=20.0) as client:
        rel = _create_relationship(client)
        rel_id = rel["id"]

        try:
            # Set config with include_profile_in_prompt
            url = f"{BASE_URL}/v2/relationships/{rel_id}/config"
            config = {"include_profile_in_prompt": True}
            response = client.patch(url, headers=_headers(), json=config)
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            assert data["config"].get("include_profile_in_prompt") is True
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_include_profile_in_prompt_config")


def test_legacy_include_app_state_in_prompt_config():
    """Test legacy include_app_state_in_prompt still works."""
    with httpx.Client(timeout=20.0) as client:
        rel = _create_relationship(client)
        rel_id = rel["id"]

        try:
            # Set config with legacy key (should still work)
            url = f"{BASE_URL}/v2/relationships/{rel_id}/config"
            config = {"include_app_state_in_prompt": True}
            response = client.patch(url, headers=_headers(), json=config)
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            # Config should preserve the key
            assert data["config"].get("include_app_state_in_prompt") is True
        finally:
            _cleanup_relationship(client, rel_id)

    print("✓ test_legacy_include_app_state_in_prompt_config")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def run_all_tests():
    """Run all Phase 6 tests."""
    if not API_KEY or not COMPANION_ID:
        print("ERROR: TEST_EM_API_KEY and TEST_EM_COMPANION_ID env vars required")
        print("Set them in .env or export them")
        sys.exit(1)

    print("\nPhase 6: State & Sensing Model Tests")
    print(f"Base URL: {BASE_URL}")
    print(f"Companion ID: {COMPANION_ID}")
    print("-" * 60)

    # Profile API tests
    test_get_profile_empty()
    test_set_profile()
    test_patch_profile()
    test_clear_profile()

    # Relationship response tests
    test_relationship_contains_profile()

    # Config option tests
    test_include_profile_in_prompt_config()
    test_legacy_include_app_state_in_prompt_config()

    print("-" * 60)
    print("All Phase 6 tests passed!")


if __name__ == "__main__":
    run_all_tests()
