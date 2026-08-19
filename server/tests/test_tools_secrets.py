"""
Test script for tools worker with project secrets.

Usage:
    cd server
    uv run python tests/test_tools_secrets.py

This script tests:
1. Creating a project secret (encrypted)
2. Creating a tool spec with secrets_config
3. Indexing a simple OpenAPI spec
4. Using a tool (which resolves secrets at runtime)
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()

# Modal endpoints
MODAL_BASE = f"https://{os.getenv('MODAL_WORKSPACE', 'my-workspace')}--em-tools-toolsworker"
INDEX_URL = f"{MODAL_BASE}-index-tools.modal.run"
RETRIEVE_URL = f"{MODAL_BASE}-retrieve-best-tool.modal.run"
USE_TOOL_URL = f"{MODAL_BASE}-use-api-tool.modal.run"

# Test OpenAPI spec (simple echo-like API for testing)
TEST_OPENAPI_SPEC = {
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


async def get_db_connection():
    """Get database connection."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_DSN")
    if not dsn:
        raise ValueError("DATABASE_URL or DATABASE_DSN not set")
    return await asyncpg.connect(dsn)


async def create_test_secret(conn, project_id: uuid.UUID, secret_name: str, secret_value: str):
    """Create an encrypted secret in the database."""
    from app.services.encryption import encrypt_secret

    encrypted = encrypt_secret(secret_value)

    await conn.execute(
        """
        INSERT INTO project_secrets (project_id, secret_name, encrypted_value, description)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (project_id, secret_name) DO UPDATE SET
            encrypted_value = EXCLUDED.encrypted_value,
            updated_at = now()
        """,
        project_id,
        secret_name,
        encrypted,
        f"Test secret: {secret_name}",
    )
    print(f"✓ Created secret: {secret_name}")


async def create_tool_spec(
    conn, project_id: uuid.UUID, spec_name: str, secrets_config: dict
) -> uuid.UUID:
    """Create a tool spec with secrets_config."""
    spec_id = uuid.uuid4()

    await conn.execute(
        """
        INSERT INTO tool_specs (id, project_id, spec_name, json_content, secrets_config)
        VALUES ($1, $2, $3, $4, $5)
        """,
        spec_id,
        project_id,
        spec_name,
        json.dumps(TEST_OPENAPI_SPEC),
        json.dumps(secrets_config),
    )
    print(f"✓ Created tool spec: {spec_name} (id: {spec_id})")
    return spec_id


async def index_tools(project_id: str, spec_id: str):
    """Call Modal worker to index tools."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            INDEX_URL,
            json={
                "request_id": str(uuid.uuid4()),
                "project_id": project_id,
                "spec_id": spec_id,
                "openapi_spec": TEST_OPENAPI_SPEC,
            },
        )
        result = response.json()
        print(f"✓ Indexed tools: {result}")
        return result


async def retrieve_best_tool(project_id: str, spec_id: str, query: str):
    """Call Modal worker to retrieve best tool for query."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            RETRIEVE_URL,
            json={
                "request_id": str(uuid.uuid4()),
                "project_id": project_id,
                "spec_id": spec_id,
                "query": query,
            },
        )
        result = response.json()
        print(f"✓ Best tool for '{query}': {result}")
        return result


async def use_tool(project_id: str, spec_id: str, tool_name: str, parameters: dict):
    """Call Modal worker to use a tool (resolves secrets automatically)."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            USE_TOOL_URL,
            json={
                "request_id": str(uuid.uuid4()),
                "project_id": project_id,
                "spec_id": spec_id,
                "base_url": "https://httpbin.org",
                "tool_name": tool_name,
                "parameters": parameters,
            },
        )
        result = response.json()
        print(f"✓ Tool response: {json.dumps(result, indent=2)}")
        return result


async def cleanup(conn, project_id: uuid.UUID, spec_id: uuid.UUID):
    """Clean up test data."""
    await conn.execute("DELETE FROM tool_operations WHERE spec_id = $1", spec_id)
    await conn.execute("DELETE FROM tool_specs WHERE id = $1", spec_id)
    await conn.execute("DELETE FROM project_secrets WHERE project_id = $1", project_id)
    print("✓ Cleaned up test data")


async def main():
    print("=" * 60)
    print("Tools Worker Secrets Test")
    print("=" * 60)

    conn = await get_db_connection()

    try:
        # Get a test project (use first available)
        project_row = await conn.fetchrow("SELECT id FROM projects LIMIT 1")
        if not project_row:
            print("✗ No projects found in database. Create a project first.")
            return

        project_id = project_row["id"]
        print(f"\nUsing project: {project_id}")

        # Step 1: Create test secrets
        print("\n--- Step 1: Creating test secrets ---")
        await create_test_secret(conn, project_id, "test_api_key", "test-secret-value-12345")
        await create_test_secret(conn, project_id, "test_custom_header", "custom-header-value")

        # Step 2: Create tool spec with secrets_config
        print("\n--- Step 2: Creating tool spec with secrets_config ---")
        secrets_config = {"Authorization": "test_api_key", "X-Custom-Header": "test_custom_header"}
        spec_id = await create_tool_spec(conn, project_id, "test-httpbin-api", secrets_config)

        # Step 3: Index tools
        print("\n--- Step 3: Indexing tools ---")
        index_result = await index_tools(str(project_id), str(spec_id))
        if index_result.get("status") != "success":
            print(f"✗ Indexing failed: {index_result}")
            return

        # Step 4: Retrieve best tool
        print("\n--- Step 4: Retrieving best tool ---")
        await retrieve_best_tool(str(project_id), str(spec_id), "I want to get some data")
        await retrieve_best_tool(str(project_id), str(spec_id), "I want to post a message")

        # Step 5: Use tool (this tests secret resolution)
        print("\n--- Step 5: Using tool (tests secret resolution) ---")
        result = await use_tool(str(project_id), str(spec_id), "test_get", {"test_param": "hello"})

        # Check if headers were passed correctly
        if result.get("status") == "success":
            api_response = result.get("api_response", {})
            headers = api_response.get("headers", {})

            print("\n--- Verification ---")
            auth_header = headers.get("Authorization", "")
            custom_header = headers.get("X-Custom-Header", "")

            if "test-secret-value-12345" in auth_header:
                print("✓ Authorization header correctly resolved from secret")
            else:
                print(f"✗ Authorization header not found or incorrect: {auth_header}")

            if custom_header == "custom-header-value":
                print("✓ X-Custom-Header correctly resolved from secret")
            else:
                print(f"✗ X-Custom-Header not found or incorrect: {custom_header}")
        else:
            print(f"✗ Tool call failed: {result}")

        # Cleanup
        print("\n--- Cleanup ---")
        await cleanup(conn, project_id, spec_id)

        print("\n" + "=" * 60)
        print("Test completed!")
        print("=" * 60)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
