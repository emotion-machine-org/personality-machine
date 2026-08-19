"""Tests for profile_schema field in CompanionConfig.

Phase 1 of Profile Tab implementation: verify profile_schema is properly
stored and retrieved from companion config.
"""

import json
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Dict, List
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.db import get_db
from app.models.companion import CompanionConfig, parse_companion_config_payload
from app.models.user import User
from app.repositories.relationship_repository import _deep_merge_with_schema
from app.routers import api

# =============================================================================
# Unit Tests for CompanionConfig model
# =============================================================================


def test_companion_config_profile_schema_default() -> None:
    """profile_schema should default to an empty dict."""
    config = CompanionConfig()
    assert config.profile_schema == {}


def test_companion_config_with_profile_schema() -> None:
    """profile_schema should accept arbitrary nested dicts."""
    schema = {
        "user": {
            "name": "",
            "age": None,
            "preferences": {"theme": "dark"},
        },
        "app": {
            "subscription": "free",
            "onboarding_complete": False,
        },
    }
    config = CompanionConfig(profile_schema=schema)
    assert config.profile_schema == schema
    assert config.profile_schema["user"]["name"] == ""
    assert config.profile_schema["app"]["subscription"] == "free"


def test_companion_config_profile_schema_serialization() -> None:
    """profile_schema should be included when serializing to dict."""
    schema = {"user": {"name": ""}}
    config = CompanionConfig(profile_schema=schema)
    config_dict = config.model_dump()

    assert "profile_schema" in config_dict
    assert config_dict["profile_schema"] == schema


def test_parse_companion_config_with_profile_schema() -> None:
    """parse_companion_config_payload should handle profile_schema correctly."""
    payload = {
        "system_prompt": {
            "full_system_prompt": "You are a helpful assistant.",
        },
        "memory": {"enabled": True},
        "profile_schema": {
            "user": {"name": "", "age": None},
            "app": {"tier": "premium"},
        },
    }
    config = parse_companion_config_payload(payload)

    assert config.profile_schema == payload["profile_schema"]
    assert config.profile_schema["app"]["tier"] == "premium"


def test_parse_companion_config_without_profile_schema() -> None:
    """parse_companion_config_payload should default profile_schema to empty dict."""
    payload = {
        "system_prompt": {
            "full_system_prompt": "You are a helpful assistant.",
        },
        "memory": {"enabled": False},
    }
    config = parse_companion_config_payload(payload)

    assert config.profile_schema == {}


def test_parse_companion_config_from_json_string() -> None:
    """parse_companion_config_payload should handle JSON string input."""
    payload = json.dumps(
        {
            "system_prompt": {"full_system_prompt": "Test"},
            "profile_schema": {"user": {"name": ""}},
        }
    )
    config = parse_companion_config_payload(payload)

    assert config.profile_schema == {"user": {"name": ""}}


# =============================================================================
# Integration Tests for API endpoints
# =============================================================================


def _make_user(user_id: UUID) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        clerk_user_id="clerk-user",
        email="test@example.com",
        username="tester",
        display_name="Tester",
        avatar_url=None,
        auth_provider="email",
        created_at=now,
        updated_at=now,
        onboarding_completed=True,
        onboarding_completed_at=now,
    )


class _StubConnection:
    """Minimal stub for testing companion version retrieval with profile_schema."""

    def __init__(
        self,
        *,
        owner_id: UUID,
        versions: List[Dict[str, Any]] | None = None,
    ) -> None:
        self._owner_id = owner_id
        self._versions = versions or []

    async def fetch(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        if "FROM companion_versions" in query and "ORDER BY" in query:
            if len(args) >= 2 and args[1] == self._owner_id:
                return self._versions
            return []
        raise AssertionError(f"Unexpected fetch query: {query}")

    async def fetchrow(self, query: str, *args: Any) -> Dict[str, Any] | None:
        if "SELECT 1 FROM companions" in query:
            return {} if len(args) >= 2 and args[1] == self._owner_id else None
        raise AssertionError(f"Unexpected fetchrow query: {query}")


def _build_app(conn: _StubConnection, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)

    app.dependency_overrides[get_current_user] = lambda: user

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db

    return TestClient(app)


def test_companion_versions_list_includes_profile_schema() -> None:
    """GET /api/companions/{id}/versions should include profile_schema in config."""
    owner_id = uuid4()
    companion_id = uuid4()
    version_id = uuid4()

    profile_schema = {
        "user": {"name": "", "age": None},
        "app": {"subscription": "free"},
    }

    versions = [
        {
            "id": version_id,
            "version_number": 1,
            "config": {
                "system_prompt": {
                    "full_system_prompt": "You are a helpful assistant.",
                    "identity": "Assistant",
                    "personality": "Friendly",
                    "style": "Conversational",
                    "backstory": "",
                    "additional_instructions": "",
                    "self_image": "",
                },
                "memory": {
                    "enabled": False,
                    "version": 1,
                    "core_memories": [],
                    "memory_evaluation_prompt": "",
                    "recency": 0.995,
                    "top_k": 50,
                    "min_saliency": 0.2,
                },
                "profile_schema": profile_schema,
            },
            "system_prompt": None,
            "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        },
    ]

    conn = _StubConnection(owner_id=owner_id, versions=versions)
    client = _build_app(conn, _make_user(owner_id))

    response = client.get(f"/api/companions/{companion_id}/versions")
    assert response.status_code == HTTPStatus.OK

    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["config"]["profile_schema"] == profile_schema


def test_companion_versions_list_defaults_missing_profile_schema() -> None:
    """GET /api/companions/{id}/versions should default profile_schema to {} if missing."""
    owner_id = uuid4()
    companion_id = uuid4()
    version_id = uuid4()

    # Config without profile_schema (legacy data)
    versions = [
        {
            "id": version_id,
            "version_number": 1,
            "config": {
                "system_prompt": {
                    "full_system_prompt": "Legacy prompt",
                    "identity": "",
                    "personality": "",
                    "style": "",
                    "backstory": "",
                    "additional_instructions": "",
                    "self_image": "",
                },
                "memory": {
                    "enabled": False,
                    "version": 1,
                    "core_memories": [],
                    "memory_evaluation_prompt": "",
                    "recency": 0.995,
                    "top_k": 50,
                    "min_saliency": 0.2,
                },
                # No profile_schema key
            },
            "system_prompt": None,
            "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        },
    ]

    conn = _StubConnection(owner_id=owner_id, versions=versions)
    client = _build_app(conn, _make_user(owner_id))

    response = client.get(f"/api/companions/{companion_id}/versions")
    assert response.status_code == HTTPStatus.OK

    payload = response.json()
    assert len(payload) == 1
    # Should default to empty dict
    assert payload[0]["config"]["profile_schema"] == {}


# =============================================================================
# Unit Tests for profile merge function (lazy migration)
# =============================================================================


def test_merge_empty_profile_with_schema() -> None:
    """Empty profile should get all schema defaults."""
    profile: Dict[str, Any] = {}
    schema = {
        "user": {"name": "", "age": None},
        "preferences": {"theme": "dark"},
    }

    result = _deep_merge_with_schema(profile, schema)

    assert result == schema
    assert result["user"]["name"] == ""
    assert result["preferences"]["theme"] == "dark"


def test_merge_preserves_profile_values() -> None:
    """Profile values should override schema defaults."""
    profile = {
        "user": {"name": "Alice", "age": 30},
        "preferences": {"theme": "light"},
    }
    schema = {
        "user": {"name": "", "age": None},
        "preferences": {"theme": "dark"},
    }

    result = _deep_merge_with_schema(profile, schema)

    assert result["user"]["name"] == "Alice"
    assert result["user"]["age"] == 30
    assert result["preferences"]["theme"] == "light"


def test_merge_adds_missing_fields() -> None:
    """Schema fields missing from profile should be added with defaults."""
    profile = {
        "user": {"name": "Bob"},
    }
    schema = {
        "user": {"name": "", "age": None, "email": ""},
        "settings": {"notifications": True},
    }

    result = _deep_merge_with_schema(profile, schema)

    assert result["user"]["name"] == "Bob"
    assert result["user"]["age"] is None
    assert result["user"]["email"] == ""
    assert result["settings"]["notifications"] is True


def test_merge_preserves_extra_profile_fields() -> None:
    """Profile fields not in schema should be preserved."""
    profile = {
        "user": {"name": "Charlie", "custom_field": "custom_value"},
        "legacy_data": {"old_key": "old_value"},
    }
    schema = {
        "user": {"name": "", "age": None},
    }

    result = _deep_merge_with_schema(profile, schema)

    assert result["user"]["name"] == "Charlie"
    assert result["user"]["age"] is None
    assert result["user"]["custom_field"] == "custom_value"
    assert result["legacy_data"]["old_key"] == "old_value"


def test_merge_handles_nested_structures() -> None:
    """Merge should work recursively for nested dicts."""
    profile = {
        "user": {
            "identity": {"name": "Dana"},
        },
    }
    schema = {
        "user": {
            "identity": {"name": "", "bio": ""},
            "preferences": {"theme": "system"},
        },
    }

    result = _deep_merge_with_schema(profile, schema)

    assert result["user"]["identity"]["name"] == "Dana"
    assert result["user"]["identity"]["bio"] == ""
    assert result["user"]["preferences"]["theme"] == "system"


def test_merge_with_empty_schema() -> None:
    """Empty schema should return profile unchanged."""
    profile = {"user": {"name": "Eve"}}
    schema: Dict[str, Any] = {}

    result = _deep_merge_with_schema(profile, schema)

    assert result == profile


def test_merge_handles_list_values() -> None:
    """List values should be handled correctly."""
    profile = {
        "tags": ["existing_tag"],
    }
    schema = {
        "tags": [],
        "categories": ["default"],
    }

    result = _deep_merge_with_schema(profile, schema)

    assert result["tags"] == ["existing_tag"]
    assert result["categories"] == ["default"]
