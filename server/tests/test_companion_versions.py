import json
from datetime import UTC, datetime, timezone
from http import HTTPStatus
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.db import get_db
from app.models.user import User
from app.routers import api


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
    def __init__(
        self,
        *,
        owner_id: UUID,
        versions: List[Dict[str, Any]] | None = None,
        version_row: Dict[str, Any] | None = None,
    ) -> None:
        self._owner_id = owner_id
        self._versions = versions or []
        self._version_row = version_row

    async def fetch(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        if "FROM companion_versions" in query and "ORDER BY" in query:
            if len(args) >= 2 and args[1] == self._owner_id:
                return self._versions
            return []
        raise AssertionError(f"Unexpected fetch query: {query}")

    async def fetchrow(self, query: str, *args: Any) -> Dict[str, Any] | None:
        if "SELECT 1 FROM companions" in query:
            return {} if len(args) >= 2 and args[1] == self._owner_id else None
        if "FROM companion_versions" in query:
            if len(args) >= 3 and args[2] == self._owner_id:
                return self._version_row
            return None
        raise AssertionError(f"Unexpected fetchrow query: {query}")


def _build_app(conn: _StubConnection, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)

    app.dependency_overrides[get_current_user] = lambda: user

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db

    return TestClient(app)


def test_companion_versions_list_returns_summaries() -> None:
    owner_id = uuid4()
    companion_id = uuid4()
    version_latest_id = uuid4()
    version_previous_id = uuid4()

    versions = [
        {
            "id": version_latest_id,
            "version_number": 4,
            "config": None,  # Use system_prompt fallback for legacy test data
            "system_prompt": json.dumps(
                {
                    "system_prompt": {
                        "full_system_prompt": "# IDENTITY\n\nGuardian",
                        "identity": "Guardian",
                        "personality": "Calm",
                        "style": "Direct",
                        "backstory": "Protective AI",
                        "additional_instructions": "",
                    },
                    "memory": {
                        "enabled": True,
                        "core_memories": ["Remember safety protocols"],
                        "memory_evaluation_prompt": "Rate importance",
                        "min_saliency": 5,
                        "recency": 0.995,
                        "top_k": 50,
                    },
                    "voice": {
                        "popular_options": ["OpenAI - speech-to-speech"],
                        "build_your_own": {"STT": [], "TTS": [], "LLM": []},
                        "voice": ["Alloy"],
                        "temperature": 0.25,
                    },
                }
            ),
            "created_at": datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
        },
        {
            "id": version_previous_id,
            "version_number": 3,
            "config": None,  # Use system_prompt fallback for legacy test data
            "system_prompt": json.dumps(
                {
                    "system_prompt": {
                        "full_system_prompt": "# IDENTITY\n\nHelper",
                        "identity": "Helper",
                        "personality": "Warm",
                        "style": "Conversational",
                        "backstory": "Friendly AI",
                        "additional_instructions": "",
                    },
                    "memory": {
                        "enabled": False,
                        "core_memories": [],
                        "memory_evaluation_prompt": "",
                        "min_saliency": 0.2,
                        "recency": 0.995,
                        "top_k": 50,
                    },
                    "voice": {
                        "popular_options": ["OpenAI - speech-to-speech"],
                        "build_your_own": {"STT": [], "TTS": [], "LLM": []},
                        "voice": ["Ash"],
                        "temperature": 0.25,
                    },
                }
            ),
            "created_at": datetime(2024, 1, 1, 9, 0, tzinfo=UTC),
        },
    ]

    conn = _StubConnection(owner_id=owner_id, versions=versions)
    client = _build_app(conn, _make_user(owner_id))

    response = client.get(f"/api/companions/{companion_id}/versions")
    assert response.status_code == HTTPStatus.OK

    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["id"] == str(version_latest_id)
    assert payload[0]["config"]["memory"]["min_saliency"] == 0.5  # sanitized from 5 -> 0.5
    assert payload[0]["config"]["system_prompt"]["identity"] == "Guardian"
    assert payload[1]["id"] == str(version_previous_id)
    assert payload[1]["config"]["memory"]["enabled"] is False


def test_companion_versions_list_unauthorized_returns_404() -> None:
    owner_id = uuid4()
    other_user = uuid4()
    companion_id = uuid4()

    conn = _StubConnection(owner_id=owner_id, versions=[])
    client = _build_app(conn, _make_user(other_user))

    response = client.get(f"/api/companions/{companion_id}/versions")
    assert response.status_code == HTTPStatus.NOT_FOUND


def test_companion_version_detail_handles_double_encoded_payload() -> None:
    owner_id = uuid4()
    companion_id = uuid4()
    version_id = uuid4()

    legacy_payload = {
        "system_prompt": {
            "full_system_prompt": "# IDENTITY\n\nArchivist",
            "identity": "Archivist",
            "personality": "Methodical",
            "style": "Formal",
            "backstory": "Preserves memories",
            "additional_instructions": "Archive key events",
        },
        "memory": {
            "enabled": True,
            "core_memories": ["Catalogue every outcome"],
            "memory_evaluation_prompt": "Rank relevance",
            "min_saliency": 12,
            "recency": 0.995,
            "top_k": 50,
        },
        "voice": {
            "popular_options": ["OpenAI - speech-to-speech"],
            "build_your_own": {"STT": [], "TTS": [], "LLM": []},
            "voice": ["Ballad"],
            "temperature": 0.25,
        },
    }

    double_encoded = json.dumps(json.dumps(legacy_payload))

    conn = _StubConnection(
        owner_id=owner_id,
        version_row={
            "config": None,  # Use system_prompt fallback for legacy test data
            "system_prompt": double_encoded,
        },
    )
    client = _build_app(conn, _make_user(owner_id))

    response = client.get(f"/api/companions/{companion_id}/versions/{version_id}")
    assert response.status_code == HTTPStatus.OK

    config = response.json()
    assert config["system_prompt"]["identity"] == "Archivist"
    assert config["memory"]["min_saliency"] == 0.2  # Min saliency capped for legacy rows
    assert config["memory"]["enabled"] is True
    assert config["voice"]["voice_name"] == "Ballad"
