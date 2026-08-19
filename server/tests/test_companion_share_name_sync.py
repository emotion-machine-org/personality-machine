import json
from datetime import UTC, datetime, timezone
from http import HTTPStatus
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

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
        companion_row: Dict[str, Any],
        version_row: Dict[str, Any],
        share_row: Dict[str, Any] | None,
    ) -> None:
        self._companion_row = companion_row
        self._version_row = version_row
        self._share_row = share_row
        self.companion_updates: list[str] = []
        self.share_updates: list[str] = []

    class _Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    def transaction(self) -> "_StubConnection._Transaction":
        return self._Transaction()

    async def fetchrow(self, query: str, *args: Any) -> Dict[str, Any] | None:
        if "FROM companions" in query and "owner_id" in query:
            # Ownership is verified in Python by the repository; the query selects by id only.
            if args and args[0] == self._companion_row["id"]:
                return dict(self._companion_row)
            return None
        if "FROM companion_versions" in query and "ORDER BY" in query:
            if args and args[0] == self._version_row["companion_id"]:
                return dict(self._version_row)
            return None
        if "FROM companion_shares" in query and "companion_id" in query:
            if self._share_row and args and args[0] == self._share_row["companion_id"]:
                return dict(self._share_row)
            return None
        if "UPDATE companion_shares" in query:
            if not self._share_row or not args:
                return None
            share_id = args[0]
            if share_id != self._share_row["id"]:
                return None
            # args[1] corresponds to the new display_name when the repository sets it.
            new_display_name = args[1]
            self._share_row = {
                **self._share_row,
                "display_name": new_display_name,
                "updated_at": datetime.now(UTC),
            }
            self.share_updates.append(new_display_name)
            return dict(self._share_row)
        raise AssertionError(f"Unexpected fetchrow query: {query}")

    async def fetch(self, query: str, *args: Any) -> List[Dict[str, Any]]:
        return []

    async def execute(self, query: str, *args: Any) -> str:
        if "UPDATE companions" in query:
            new_name = args[0]
            companion_id = args[1]
            owner_id = args[2]
            if (
                companion_id == self._companion_row["id"]
                and owner_id == self._companion_row["owner_id"]
            ):
                self._companion_row = {
                    **self._companion_row,
                    "name": new_name,
                    "updated_at": datetime.now(UTC),
                }
                self.companion_updates.append(new_name)
                return "UPDATE 1"
            return "UPDATE 0"
        return "OK"


def _build_app(conn: _StubConnection, user: User) -> TestClient:
    app = FastAPI()
    app.include_router(api.router)

    app.dependency_overrides[get_current_user] = lambda: user

    async def override_db():
        yield conn

    app.dependency_overrides[get_db] = override_db

    return TestClient(app)


def test_companion_name_update_syncs_share_display_name() -> None:
    owner_id = uuid4()
    companion_id = uuid4()
    share_id = uuid4()
    now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    companion_row = {
        "id": companion_id,
        "owner_id": owner_id,
        "project_id": uuid4(),
        "name": "Original Name",
        "description": "A friendly AI",
        "metadata": None,
        "created_at": now,
        "updated_at": now,
    }

    version_row = {
        "id": uuid4(),
        "companion_id": companion_id,
        "version_number": 1,
        "config": None,
        "system_prompt": json.dumps(
            {
                "system_prompt": {
                    "full_system_prompt": "# IDENTITY\n\nOriginal Name",
                    "identity": "Original Name",
                }
            }
        ),
        "voice_id": None,
        "memory_enabled": False,
        "status": "DEPLOYED",
        "created_at": now,
    }

    share_row = {
        "id": share_id,
        "companion_id": companion_id,
        "owner_id": owner_id,
        "version_id": version_row["id"],
        "slug": "demo-share",
        "status": "active",
        "allow_text": True,
        "allow_voice": True,
        "require_auth": False,
        "expose_status_events": False,
        "config_snapshot": None,
        "display_name": "Original Name",
        "description": None,
        "created_at": now,
        "updated_at": now,
        "activated_at": now,
        "disabled_at": None,
        "total_sessions": 0,
        "total_messages": 0,
        "total_voice_sessions": 0,
        "last_activity_at": None,
    }

    conn = _StubConnection(
        companion_row=companion_row,
        version_row=version_row,
        share_row=share_row,
    )
    client = _build_app(conn, _make_user(owner_id))

    response = client.put(
        f"/api/companions/{companion_id}",
        json={"name": "Renamed Companion"},
    )

    assert response.status_code == HTTPStatus.OK
    assert conn.companion_updates == ["Renamed Companion"]
    assert conn.share_updates == ["Renamed Companion"]
    assert conn._share_row["display_name"] == "Renamed Companion"


def test_manual_share_name_is_preserved() -> None:
    owner_id = uuid4()
    companion_id = uuid4()
    share_id = uuid4()
    now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    companion_row = {
        "id": companion_id,
        "owner_id": owner_id,
        "project_id": uuid4(),
        "name": "Original Name",
        "description": "A friendly AI",
        "metadata": None,
        "created_at": now,
        "updated_at": now,
    }

    version_row = {
        "id": uuid4(),
        "companion_id": companion_id,
        "version_number": 1,
        "config": None,
        "system_prompt": json.dumps({"system_prompt": {"full_system_prompt": "#"}}),
        "voice_id": None,
        "memory_enabled": False,
        "status": "DEPLOYED",
        "created_at": now,
    }

    share_row = {
        "id": share_id,
        "companion_id": companion_id,
        "owner_id": owner_id,
        "version_id": version_row["id"],
        "slug": "demo-share",
        "status": "active",
        "allow_text": True,
        "allow_voice": True,
        "require_auth": False,
        "expose_status_events": False,
        "config_snapshot": None,
        "display_name": "Custom Landing Page Name",
        "description": None,
        "created_at": now,
        "updated_at": now,
        "activated_at": now,
        "disabled_at": None,
        "total_sessions": 0,
        "total_messages": 0,
        "total_voice_sessions": 0,
        "last_activity_at": None,
    }

    conn = _StubConnection(
        companion_row=companion_row,
        version_row=version_row,
        share_row=share_row,
    )
    client = _build_app(conn, _make_user(owner_id))

    response = client.put(
        f"/api/companions/{companion_id}",
        json={"name": "Renamed Companion"},
    )

    assert response.status_code == HTTPStatus.OK
    assert conn.companion_updates == ["Renamed Companion"]
    assert conn.share_updates == []
    assert conn._share_row["display_name"] == "Custom Landing Page Name"
