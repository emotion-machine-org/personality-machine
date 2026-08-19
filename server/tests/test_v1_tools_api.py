"""
Tests for /v1 tools and secrets endpoints.

Usage:
    cd server
    uv run pytest tests/test_v1_tools_api.py -v

Or run individual tests:
    uv run pytest tests/test_v1_tools_api.py::test_secrets_crud_live -v

Environment variables required:
    TEST_EM_API_KEY: Project API key for authentication
    EM_BASE_URL: Base URL (defaults to http://localhost:8100)

The tests use the OpenAPI spec from tests/data/em_v1_openapi.json.
"""

import json
import os
import time
import uuid
from http import HTTPStatus
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")

# Load test OpenAPI spec
SPEC_PATH = Path(__file__).parent / "data" / "em_v1_openapi.json"

# Simple test OpenAPI spec for httpbin.org (echo API for testing)
SIMPLE_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "servers": [{"url": "https://httpbin.org"}],
    "paths": {
        "/get": {
            "get": {
                "operationId": "test_get",
                "summary": "Test GET endpoint that echoes headers",
                "description": "Returns request headers and query params",
                "parameters": [
                    {
                        "name": "test_param",
                        "in": "query",
                        "schema": {"type": "string"},
                        "description": "A test query parameter",
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    }
                },
            }
        },
        "/post": {
            "post": {
                "operationId": "test_post",
                "summary": "Test POST endpoint",
                "description": "Echoes back the posted JSON body",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            }
                        }
                    }
                },
                "responses": {"200": {"description": "Success"}},
            }
        },
    },
}


pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="Set TEST_EM_API_KEY to run v1 tools integration tests.",
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _get_or_create_companion(client: httpx.Client) -> str:
    """Get an existing companion or create one for testing."""
    # Try to list companions first
    list_url = f"{BASE_URL}/v1/companions"
    response = client.get(list_url, headers=_headers())

    if response.status_code == HTTPStatus.OK:
        companions = response.json()
        if companions:
            return str(companions[0]["id"])

    # Create a new companion for testing
    create_url = f"{BASE_URL}/v1/companions"
    payload = {
        "name": f"Test Companion {uuid.uuid4().hex[:8]}",
        "description": "Created for tools API testing",
    }
    response = client.post(create_url, headers=_headers(), json=payload)
    assert response.status_code == HTTPStatus.CREATED, (
        f"Failed to create companion: {response.text}"
    )
    return str(response.json()["id"])


# ──────────────────────────────────────────────────────────────────────────────
# Secrets endpoint tests
# ──────────────────────────────────────────────────────────────────────────────


class TestSecretsEndpoints:
    """Tests for /v1/secrets endpoints."""

    def test_secrets_crud_live(self):
        """Test full CRUD lifecycle for secrets."""
        secret_name = f"test_secret_{uuid.uuid4().hex[:8]}"

        with httpx.Client(timeout=30.0) as client:
            # 1. Create secret
            create_url = f"{BASE_URL}/v1/secrets"
            create_payload = {
                "secret_name": secret_name,
                "secret_value": "test-api-key-value-12345",
                "description": "Test secret for API testing",
            }
            create_response = client.post(create_url, headers=_headers(), json=create_payload)
            assert create_response.status_code == HTTPStatus.CREATED, (
                f"Create failed: {create_response.text}"
            )

            created = create_response.json()
            assert created["secret_name"] == secret_name
            assert created["description"] == "Test secret for API testing"
            assert "id" in created
            assert "created_at" in created
            # Secret value should NOT be returned
            assert "secret_value" not in created

            # 2. List secrets
            list_url = f"{BASE_URL}/v1/secrets"
            list_response = client.get(list_url, headers=_headers())
            assert list_response.status_code == HTTPStatus.OK

            secrets = list_response.json()
            assert isinstance(secrets, list)
            secret_names = [s["secret_name"] for s in secrets]
            assert secret_name in secret_names

            # 3. Update secret (create with same name)
            update_payload = {
                "secret_name": secret_name,
                "secret_value": "updated-api-key-value-67890",
                "description": "Updated test secret",
            }
            update_response = client.post(create_url, headers=_headers(), json=update_payload)
            assert update_response.status_code == HTTPStatus.CREATED

            updated = update_response.json()
            assert updated["secret_name"] == secret_name
            assert updated["description"] == "Updated test secret"

            # 4. Delete secret
            delete_url = f"{BASE_URL}/v1/secrets/{secret_name}"
            delete_response = client.delete(delete_url, headers=_headers())
            assert delete_response.status_code == HTTPStatus.NO_CONTENT

            # 5. Verify deletion
            list_response = client.get(list_url, headers=_headers())
            secrets = list_response.json()
            secret_names = [s["secret_name"] for s in secrets]
            assert secret_name not in secret_names

    def test_delete_nonexistent_secret(self):
        """Test that deleting a non-existent secret returns 404."""
        with httpx.Client(timeout=10.0) as client:
            delete_url = f"{BASE_URL}/v1/secrets/nonexistent_secret_{uuid.uuid4().hex}"
            response = client.delete(delete_url, headers=_headers())
            assert response.status_code == HTTPStatus.NOT_FOUND

    def test_create_secret_validation(self):
        """Test validation for secret creation."""
        with httpx.Client(timeout=10.0) as client:
            create_url = f"{BASE_URL}/v1/secrets"

            # Missing secret_name
            response = client.post(
                create_url, headers=_headers(), json={"secret_value": "some-value"}
            )
            assert response.status_code == 422

            # Missing secret_value
            response = client.post(
                create_url, headers=_headers(), json={"secret_name": "test_secret"}
            )
            assert response.status_code == 422

            # Empty secret_name
            response = client.post(
                create_url,
                headers=_headers(),
                json={"secret_name": "", "secret_value": "some-value"},
            )
            assert response.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# Tools endpoint tests
# ──────────────────────────────────────────────────────────────────────────────


class TestToolsEndpoints:
    """Tests for /v1/companions/{id}/tools endpoints."""

    def test_tools_crud_live(self):
        """Test full CRUD lifecycle for tool specs."""
        with httpx.Client(timeout=60.0) as client:
            companion_id = _get_or_create_companion(client)
            spec_name = f"test_spec_{uuid.uuid4().hex[:8]}"

            # 1. Index (create) tool spec
            index_url = f"{BASE_URL}/v1/companions/{companion_id}/tools"
            index_payload = {
                "spec_name": spec_name,
                "openapi_spec": SIMPLE_OPENAPI_SPEC,
                "secrets_config": {"Authorization": "test_api_key"},
            }
            index_response = client.post(index_url, headers=_headers(), json=index_payload)
            assert index_response.status_code == HTTPStatus.ACCEPTED, (
                f"Index failed: {index_response.text}"
            )

            indexed = index_response.json()
            assert "spec_id" in indexed
            assert "dispatched" in indexed
            assert "request_id" in indexed
            spec_id = indexed["spec_id"]

            # Give Modal a moment to process (indexing is async)
            time.sleep(2)

            # 2. List tool specs
            list_url = f"{BASE_URL}/v1/companions/{companion_id}/tools"
            list_response = client.get(list_url, headers=_headers())
            assert list_response.status_code == HTTPStatus.OK

            specs = list_response.json()
            assert isinstance(specs, list)
            spec_ids = [s["id"] for s in specs]
            assert spec_id in spec_ids

            # 3. Get tool spec details
            get_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            get_response = client.get(get_url, headers=_headers())
            assert get_response.status_code == HTTPStatus.OK

            spec_detail = get_response.json()
            assert spec_detail["id"] == spec_id
            assert spec_detail["spec_name"] == spec_name
            assert spec_detail["secrets_config"] == {"Authorization": "test_api_key"}
            assert spec_detail["json_content"] is not None

            # 4. Update secrets_config
            update_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            update_payload = {
                "secrets_config": {
                    "Authorization": "updated_api_key",
                    "X-Custom-Header": "custom_key",
                }
            }
            update_response = client.patch(update_url, headers=_headers(), json=update_payload)
            assert update_response.status_code == HTTPStatus.OK

            updated = update_response.json()
            assert updated["status"] == "updated"
            assert updated["secrets_config"]["Authorization"] == "updated_api_key"
            assert updated["secrets_config"]["X-Custom-Header"] == "custom_key"

            # Verify update persisted
            get_response = client.get(get_url, headers=_headers())
            assert get_response.json()["secrets_config"]["X-Custom-Header"] == "custom_key"

            # 5. Delete tool spec
            delete_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            delete_response = client.delete(delete_url, headers=_headers())
            assert delete_response.status_code == HTTPStatus.NO_CONTENT

            # 6. Verify deletion
            get_response = client.get(get_url, headers=_headers())
            assert get_response.status_code == HTTPStatus.NOT_FOUND

    def test_get_nonexistent_tool_spec(self):
        """Test that getting a non-existent tool spec returns 404."""
        with httpx.Client(timeout=10.0) as client:
            companion_id = _get_or_create_companion(client)
            fake_spec_id = str(uuid.uuid4())

            get_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{fake_spec_id}"
            response = client.get(get_url, headers=_headers())
            assert response.status_code == HTTPStatus.NOT_FOUND

    def test_index_tool_spec_validation(self):
        """Test validation for tool spec indexing."""
        with httpx.Client(timeout=10.0) as client:
            companion_id = _get_or_create_companion(client)
            index_url = f"{BASE_URL}/v1/companions/{companion_id}/tools"

            # Missing openapi_spec
            response = client.post(index_url, headers=_headers(), json={"spec_name": "test"})
            assert response.status_code == 422

            # Invalid openapi_spec (not a dict)
            response = client.post(
                index_url, headers=_headers(), json={"openapi_spec": "not a dict"}
            )
            assert response.status_code == 422

    def test_tool_spec_wrong_companion(self):
        """Test that tool specs from other companions are not accessible."""
        with httpx.Client(timeout=30.0) as client:
            # Create two companions
            companion1_id = _get_or_create_companion(client)

            # Create a second companion
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Test Companion 2 {uuid.uuid4().hex[:8]}",
                "description": "Second companion for isolation testing",
            }
            response = client.post(create_url, headers=_headers(), json=payload)
            if response.status_code != 201:
                pytest.skip("Could not create second companion")
            companion2_id = str(response.json()["id"])

            # Create tool spec for companion1
            index_url = f"{BASE_URL}/v1/companions/{companion1_id}/tools"
            index_payload = {
                "spec_name": "isolation_test_spec",
                "openapi_spec": SIMPLE_OPENAPI_SPEC,
            }
            index_response = client.post(index_url, headers=_headers(), json=index_payload)
            assert index_response.status_code == HTTPStatus.ACCEPTED
            spec_id = index_response.json()["spec_id"]

            # Try to access spec from companion2 - should fail
            get_url = f"{BASE_URL}/v1/companions/{companion2_id}/tools/{spec_id}"
            get_response = client.get(get_url, headers=_headers())
            assert get_response.status_code == HTTPStatus.NOT_FOUND

            # Cleanup
            delete_url = f"{BASE_URL}/v1/companions/{companion1_id}/tools/{spec_id}"
            client.delete(delete_url, headers=_headers())


# ──────────────────────────────────────────────────────────────────────────────
# Integration workflow tests
# ──────────────────────────────────────────────────────────────────────────────


class TestToolsWorkflow:
    """Integration tests for the full tools workflow."""

    def test_full_tools_workflow_live(self):
        """
        Test the complete workflow:
        1. Create a secret for API authentication
        2. Index an OpenAPI spec with secrets_config
        3. Verify the tool spec is stored correctly
        4. Clean up
        """
        secret_name = f"workflow_test_key_{uuid.uuid4().hex[:8]}"

        with httpx.Client(timeout=60.0) as client:
            companion_id = _get_or_create_companion(client)

            # Step 1: Create secret
            secret_url = f"{BASE_URL}/v1/secrets"
            secret_payload = {
                "secret_name": secret_name,
                "secret_value": "Bearer sk-test-workflow-key-12345",
                "description": "Test key for workflow testing",
            }
            secret_response = client.post(secret_url, headers=_headers(), json=secret_payload)
            assert secret_response.status_code == HTTPStatus.CREATED, (
                f"Secret creation failed: {secret_response.text}"
            )

            # Step 2: Index OpenAPI spec with secrets_config
            tools_url = f"{BASE_URL}/v1/companions/{companion_id}/tools"
            tools_payload = {
                "spec_name": "workflow_test_api",
                "openapi_spec": SIMPLE_OPENAPI_SPEC,
                "secrets_config": {"Authorization": secret_name},
            }
            tools_response = client.post(tools_url, headers=_headers(), json=tools_payload)
            assert tools_response.status_code == HTTPStatus.ACCEPTED, (
                f"Tool indexing failed: {tools_response.text}"
            )
            spec_id = tools_response.json()["spec_id"]

            # Wait for indexing
            time.sleep(3)

            # Step 3: Verify tool spec
            get_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            get_response = client.get(get_url, headers=_headers())
            assert get_response.status_code == HTTPStatus.OK

            spec_detail = get_response.json()
            assert spec_detail["secrets_config"]["Authorization"] == secret_name
            assert "paths" in spec_detail["json_content"]

            # Step 4: Cleanup
            delete_tool_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            client.delete(delete_tool_url, headers=_headers())

            delete_secret_url = f"{BASE_URL}/v1/secrets/{secret_name}"
            client.delete(delete_secret_url, headers=_headers())

    def test_index_large_openapi_spec_live(self):
        """Test indexing a larger OpenAPI spec (the actual EM v1 spec)."""
        if not SPEC_PATH.exists():
            pytest.skip(f"OpenAPI spec not found at {SPEC_PATH}")

        with open(SPEC_PATH) as f:
            large_spec = json.load(f)

        with httpx.Client(timeout=120.0) as client:
            companion_id = _get_or_create_companion(client)

            # Index the large spec
            tools_url = f"{BASE_URL}/v1/companions/{companion_id}/tools"
            tools_payload = {
                "spec_name": "em_v1_full_spec",
                "openapi_spec": large_spec,
            }
            response = client.post(tools_url, headers=_headers(), json=tools_payload)
            assert response.status_code == HTTPStatus.ACCEPTED, (
                f"Large spec indexing failed: {response.text}"
            )

            spec_id = response.json()["spec_id"]
            dispatched = response.json()["dispatched"]

            # Verify response structure
            assert spec_id is not None
            assert isinstance(dispatched, bool)

            # Wait for indexing (large spec takes longer)
            time.sleep(5)

            # Verify spec was stored
            get_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            get_response = client.get(get_url, headers=_headers())
            assert get_response.status_code == HTTPStatus.OK

            spec_detail = get_response.json()
            assert spec_detail["spec_name"] == "em_v1_full_spec"
            assert spec_detail["json_content"]["openapi"] == large_spec["openapi"]

            # Cleanup
            delete_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            client.delete(delete_url, headers=_headers())


# ──────────────────────────────────────────────────────────────────────────────
# Secret cleanup on delete tests
# ──────────────────────────────────────────────────────────────────────────────


class TestSecretCleanup:
    """Tests for secret reference cleanup when secrets are deleted."""

    def test_secret_deletion_updates_tool_specs(self):
        """Test that deleting a secret removes it from tool specs' secrets_config."""
        secret_name = f"cleanup_test_key_{uuid.uuid4().hex[:8]}"

        with httpx.Client(timeout=60.0) as client:
            companion_id = _get_or_create_companion(client)

            # Create secret
            secret_url = f"{BASE_URL}/v1/secrets"
            secret_payload = {
                "secret_name": secret_name,
                "secret_value": "test-value",
            }
            client.post(secret_url, headers=_headers(), json=secret_payload)

            # Create tool spec referencing the secret
            tools_url = f"{BASE_URL}/v1/companions/{companion_id}/tools"
            tools_payload = {
                "spec_name": "cleanup_test_spec",
                "openapi_spec": SIMPLE_OPENAPI_SPEC,
                "secrets_config": {
                    "Authorization": secret_name,
                    "X-Other-Header": "other_secret",  # This should remain
                },
            }
            tools_response = client.post(tools_url, headers=_headers(), json=tools_payload)
            spec_id = tools_response.json()["spec_id"]

            time.sleep(1)

            # Delete the secret
            delete_secret_url = f"{BASE_URL}/v1/secrets/{secret_name}"
            client.delete(delete_secret_url, headers=_headers())

            # Verify secrets_config was updated (Authorization should be removed)
            get_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            get_response = client.get(get_url, headers=_headers())

            if get_response.status_code == HTTPStatus.OK:
                spec_detail = get_response.json()
                secrets_config = spec_detail.get("secrets_config") or {}
                # The Authorization key should be removed since its secret was deleted
                assert secret_name not in secrets_config.values(), (
                    f"Secret {secret_name} should have been removed from secrets_config"
                )

            # Cleanup
            delete_tool_url = f"{BASE_URL}/v1/companions/{companion_id}/tools/{spec_id}"
            client.delete(delete_tool_url, headers=_headers())


# ──────────────────────────────────────────────────────────────────────────────
# Run as script
# ──────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
