# server/tests/test_voice_workspace.py
"""Tests for S3-backed voice workspace.

Tests both the VoiceWorkspace/HotContextS3 classes and the API endpoints.
Uses moto for S3 mocking in unit tests.
"""

import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

# Set env vars before importing modules
os.environ["KNOWLEDGE_S3_BUCKET"] = "test-bucket"
os.environ["KNOWLEDGE_S3_REGION"] = "us-east-1"


@pytest.fixture
def aws_credentials():
    """Mock AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def s3_bucket(aws_credentials):
    """Create mock S3 bucket."""
    with mock_aws():
        conn = boto3.client("s3", region_name="us-east-1")
        conn.create_bucket(Bucket="test-bucket")
        yield conn


@pytest.fixture
def reset_s3_client():
    """Reset the global S3 client and pin the bucket name before each test.

    The module may have been imported by another test file before this one set
    KNOWLEDGE_S3_BUCKET, so override the captured module-level values directly.
    """
    from app.routers.voice import voice_workspace

    prev_bucket = voice_workspace._S3_BUCKET
    prev_region = voice_workspace._S3_REGION
    voice_workspace._s3_client = None
    voice_workspace._S3_BUCKET = "test-bucket"
    voice_workspace._S3_REGION = "us-east-1"
    yield
    voice_workspace._s3_client = None
    voice_workspace._S3_BUCKET = prev_bucket
    voice_workspace._S3_REGION = prev_region


# ─────────────────────────────────────────────────────────────────────────────
# VoiceWorkspace Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestVoiceWorkspace:
    """Tests for VoiceWorkspace S3 operations."""

    def test_write_and_read(self, s3_bucket, reset_s3_client):
        """Test basic write and read."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id = str(uuid4())
        ws = VoiceWorkspace(rel_id)

        # Write
        ws.write("test.md", "Hello, World!")

        # Read
        content = ws.read("test.md")
        assert content == "Hello, World!"

    def test_read_nonexistent(self, s3_bucket, reset_s3_client):
        """Test reading a file that doesn't exist returns None."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id = str(uuid4())
        ws = VoiceWorkspace(rel_id)

        content = ws.read("nonexistent.md")
        assert content is None

    def test_append(self, s3_bucket, reset_s3_client):
        """Test appending to a file."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id = str(uuid4())
        ws = VoiceWorkspace(rel_id)

        # First append creates file
        ws.append("log.md", "Line 1\n")
        assert ws.read("log.md") == "Line 1\n"

        # Second append adds to it
        ws.append("log.md", "Line 2\n")
        assert ws.read("log.md") == "Line 1\nLine 2\n"

    def test_delete(self, s3_bucket, reset_s3_client):
        """Test deleting a file."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id = str(uuid4())
        ws = VoiceWorkspace(rel_id)

        ws.write("temp.md", "temporary")
        assert ws.read("temp.md") == "temporary"

        ws.delete("temp.md")
        assert ws.read("temp.md") is None

    def test_exists(self, s3_bucket, reset_s3_client):
        """Test checking if file exists."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id = str(uuid4())
        ws = VoiceWorkspace(rel_id)

        assert ws.exists("missing.md") is False

        ws.write("exists.md", "I exist")
        assert ws.exists("exists.md") is True

    def test_list_files(self, s3_bucket, reset_s3_client):
        """Test listing files."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id = str(uuid4())
        ws = VoiceWorkspace(rel_id)

        ws.write("a.md", "a")
        ws.write("b.md", "b")
        ws.write("subdir/c.md", "c")

        files = ws.list_files()
        assert "a.md" in files
        assert "b.md" in files
        assert "subdir/c.md" in files

    def test_relationship_isolation(self, s3_bucket, reset_s3_client):
        """Test that different relationships have isolated workspaces."""
        from app.routers.voice.voice_workspace import VoiceWorkspace

        rel_id_1 = str(uuid4())
        rel_id_2 = str(uuid4())

        ws1 = VoiceWorkspace(rel_id_1)
        ws2 = VoiceWorkspace(rel_id_2)

        # Write to ws1
        ws1.write("shared_name.md", "Content from rel 1")

        # Write different content to ws2 with same filename
        ws2.write("shared_name.md", "Content from rel 2")

        # Each should see their own content
        assert ws1.read("shared_name.md") == "Content from rel 1"
        assert ws2.read("shared_name.md") == "Content from rel 2"

        # Deleting from one doesn't affect other
        ws1.delete("shared_name.md")
        assert ws1.read("shared_name.md") is None
        assert ws2.read("shared_name.md") == "Content from rel 2"


# ─────────────────────────────────────────────────────────────────────────────
# HotContextS3 Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestHotContextS3:
    """Tests for HotContextS3 task state tracking."""

    def test_log_start_and_get_status(self, s3_bucket, reset_s3_client):
        """Test logging task start and getting status."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id = str(uuid4())
        ctx = HotContextS3(rel_id)
        task_id = str(uuid4())

        ctx.log_start(task_id, "Send email to creator")

        result = ctx.get_task_result(task_id)
        assert result is not None
        status, data = result
        assert status == "started"
        assert "Send email" in data

    def test_log_ack(self, s3_bucket, reset_s3_client):
        """Test logging task acknowledgment."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id = str(uuid4())
        ctx = HotContextS3(rel_id)
        task_id = str(uuid4())

        ctx.log_start(task_id, "Task")
        ctx.log_ack(task_id, "OpenClaw accepted")

        result = ctx.get_task_result(task_id)
        status, _data = result
        assert status == "acked"

    def test_log_done(self, s3_bucket, reset_s3_client):
        """Test logging task completion."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id = str(uuid4())
        ctx = HotContextS3(rel_id)
        task_id = str(uuid4())

        ctx.log_start(task_id, "Task")
        ctx.log_done(task_id, "Email sent successfully!")

        result = ctx.get_task_result(task_id)
        status, data = result
        assert status == "done"
        assert "Email sent successfully" in data

    def test_log_fail(self, s3_bucket, reset_s3_client):
        """Test logging task failure."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id = str(uuid4())
        ctx = HotContextS3(rel_id)
        task_id = str(uuid4())

        ctx.log_start(task_id, "Task")
        ctx.log_fail(task_id, "Connection timeout")

        result = ctx.get_task_result(task_id)
        status, data = result
        assert status == "failed"
        assert "timeout" in data

    def test_multiple_tasks(self, s3_bucket, reset_s3_client):
        """Test tracking multiple tasks."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id = str(uuid4())
        ctx = HotContextS3(rel_id)

        task1 = str(uuid4())
        task2 = str(uuid4())

        ctx.log_start(task1, "Task 1")
        ctx.log_start(task2, "Task 2")

        ctx.log_done(task1, "Result 1")
        ctx.log_fail(task2, "Error 2")

        result1 = ctx.get_task_result(task1)
        result2 = ctx.get_task_result(task2)

        assert result1[0] == "done"
        assert result2[0] == "failed"

    def test_task_not_found(self, s3_bucket, reset_s3_client):
        """Test getting status of nonexistent task."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id = str(uuid4())
        ctx = HotContextS3(rel_id)

        result = ctx.get_task_result("nonexistent-task-id")
        assert result is None

    def test_relationship_isolation_tasks(self, s3_bucket, reset_s3_client):
        """Test that tasks are isolated per relationship."""
        from app.routers.voice.voice_workspace import HotContextS3

        rel_id_1 = str(uuid4())
        rel_id_2 = str(uuid4())

        ctx1 = HotContextS3(rel_id_1)
        ctx2 = HotContextS3(rel_id_2)

        task_id = "shared-task-id"  # Same task ID

        ctx1.log_start(task_id, "Task in rel 1")
        ctx1.log_done(task_id, "Result 1")

        ctx2.log_start(task_id, "Task in rel 2")
        ctx2.log_fail(task_id, "Error 2")

        # Each relationship sees their own task state
        assert ctx1.get_task_result(task_id)[0] == "done"
        assert ctx2.get_task_result(task_id)[0] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoint Tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("RUN_API_TESTS"), reason="Set RUN_API_TESTS=1 to run API tests (requires DB)"
)
class TestWorkspaceAPI:
    """Tests for workspace API endpoints (OpenClaw callback interface).

    These tests require a database connection. Run with:
        RUN_API_TESTS=1 pytest tests/test_voice_workspace.py -v
    """

    @pytest.fixture
    def client(self, s3_bucket, reset_s3_client):
        """Create test client with mocked S3."""
        from app.main import app

        return TestClient(app)

    @pytest.fixture
    def auth_header(self):
        """Auth header for API calls."""
        # No token configured = all allowed
        return {}

    def test_write_and_read_file(self, client, auth_header):
        """Test writing and reading a file via API."""
        rel_id = str(uuid4())

        # Write
        resp = client.put(
            f"/voice/workspace/{rel_id}/files/notes.md",
            json={"content": "Hello from API"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Read
        resp = client.get(
            f"/voice/workspace/{rel_id}/files/notes.md",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        assert data["content"] == "Hello from API"

    def test_read_nonexistent_file(self, client, auth_header):
        """Test reading nonexistent file returns exists=False."""
        rel_id = str(uuid4())

        resp = client.get(
            f"/voice/workspace/{rel_id}/files/missing.md",
            headers=auth_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False
        assert data["content"] is None

    def test_append_file(self, client, auth_header):
        """Test appending to a file via API."""
        rel_id = str(uuid4())

        # First append
        resp = client.post(
            f"/voice/workspace/{rel_id}/files/log.md/append",
            json={"content": "Line 1\n"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Second append
        resp = client.post(
            f"/voice/workspace/{rel_id}/files/log.md/append",
            json={"content": "Line 2\n"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Read back
        resp = client.get(
            f"/voice/workspace/{rel_id}/files/log.md",
            headers=auth_header,
        )
        assert resp.json()["content"] == "Line 1\nLine 2\n"

    def test_delete_file(self, client, auth_header):
        """Test deleting a file via API."""
        rel_id = str(uuid4())

        # Create file
        client.put(
            f"/voice/workspace/{rel_id}/files/temp.md",
            json={"content": "temp"},
            headers=auth_header,
        )

        # Delete
        resp = client.delete(
            f"/voice/workspace/{rel_id}/files/temp.md",
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Verify deleted
        resp = client.get(
            f"/voice/workspace/{rel_id}/files/temp.md",
            headers=auth_header,
        )
        assert resp.json()["exists"] is False

    def test_task_done_callback(self, client, auth_header):
        """Test task done callback (simulates OpenClaw calling back)."""
        rel_id = str(uuid4())
        task_id = str(uuid4())

        # Start task
        client.post(
            f"/voice/workspace/{rel_id}/tasks/{task_id}/start",
            json={"result": "Send email"},
            headers=auth_header,
        )

        # Mark done (OpenClaw callback)
        resp = client.post(
            f"/voice/workspace/{rel_id}/tasks/{task_id}/done",
            json={"result": "Email sent to creator@example.com"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Check status
        resp = client.get(
            f"/voice/workspace/{rel_id}/tasks/{task_id}",
            headers=auth_header,
        )
        data = resp.json()
        assert data["found"] is True
        assert data["status"] == "done"
        assert "Email sent" in data["data"]

    def test_task_fail_callback(self, client, auth_header):
        """Test task fail callback."""
        rel_id = str(uuid4())
        task_id = str(uuid4())

        # Start task
        client.post(
            f"/voice/workspace/{rel_id}/tasks/{task_id}/start",
            json={"result": "Send email"},
            headers=auth_header,
        )

        # Mark failed (OpenClaw callback)
        resp = client.post(
            f"/voice/workspace/{rel_id}/tasks/{task_id}/fail",
            json={"error": "SMTP connection refused"},
            headers=auth_header,
        )
        assert resp.status_code == 200

        # Check status
        resp = client.get(
            f"/voice/workspace/{rel_id}/tasks/{task_id}",
            headers=auth_header,
        )
        data = resp.json()
        assert data["found"] is True
        assert data["status"] == "failed"

    def test_task_not_found(self, client, auth_header):
        """Test getting status of nonexistent task."""
        rel_id = str(uuid4())

        resp = client.get(
            f"/voice/workspace/{rel_id}/tasks/nonexistent-task",
            headers=auth_header,
        )
        data = resp.json()
        assert data["found"] is False

    def test_api_relationship_isolation(self, client, auth_header):
        """Test that API respects relationship isolation."""
        rel_id_1 = str(uuid4())
        rel_id_2 = str(uuid4())
        task_id = "shared-task"

        # Task in rel_1
        client.post(
            f"/voice/workspace/{rel_id_1}/tasks/{task_id}/start",
            json={"result": "Task 1"},
            headers=auth_header,
        )
        client.post(
            f"/voice/workspace/{rel_id_1}/tasks/{task_id}/done",
            json={"result": "Result 1"},
            headers=auth_header,
        )

        # Task in rel_2
        client.post(
            f"/voice/workspace/{rel_id_2}/tasks/{task_id}/start",
            json={"result": "Task 2"},
            headers=auth_header,
        )
        client.post(
            f"/voice/workspace/{rel_id_2}/tasks/{task_id}/fail",
            json={"error": "Error 2"},
            headers=auth_header,
        )

        # Check isolation
        resp1 = client.get(f"/voice/workspace/{rel_id_1}/tasks/{task_id}", headers=auth_header)
        resp2 = client.get(f"/voice/workspace/{rel_id_2}/tasks/{task_id}", headers=auth_header)

        assert resp1.json()["status"] == "done"
        assert resp2.json()["status"] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Integration Test (requires real S3)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("RUN_S3_INTEGRATION_TESTS"),
    reason="Set RUN_S3_INTEGRATION_TESTS=1 to run real S3 tests",
)
class TestS3Integration:
    """Integration tests with real S3 (skipped by default)."""

    def test_real_s3_roundtrip(self):
        """Test with real S3 credentials."""
        from app.routers.voice.voice_workspace import HotContextS3, VoiceWorkspace

        rel_id = f"test-{uuid4()}"

        # Workspace test
        ws = VoiceWorkspace(rel_id)
        ws.write("integration-test.md", "Hello from integration test!")
        content = ws.read("integration-test.md")
        assert content == "Hello from integration test!"
        ws.delete("integration-test.md")

        # Hot context test
        ctx = HotContextS3(rel_id)
        task_id = str(uuid4())
        ctx.log_start(task_id, "Integration test task")
        ctx.log_done(task_id, "Integration test passed!")

        result = ctx.get_task_result(task_id)
        assert result[0] == "done"

        # Cleanup
        ws.delete("hot_context.md")

        print(f"✓ Integration test passed for relationship {rel_id}")
