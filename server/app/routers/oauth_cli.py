from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..db import get_db
from ..models.user import User
from ..repositories.project import ProjectApiKeyRepository, ProjectRepository
from ..services.api_keys import generate_project_api_key

router = APIRouter(prefix="/api/oauth", tags=["oauth"])

OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


class OAuthAuthorizeRequest(BaseModel):
    response_type: Literal["code"] = "code"
    client_id: str
    redirect_uri: str
    scope: str | None = None
    code_challenge: str = Field(..., min_length=20)
    code_challenge_method: Literal["S256", "plain"] = "S256"
    state: str | None = None


class OAuthAuthorizeResponse(BaseModel):
    code: str
    redirect_uri: str
    state: str | None = None
    expires_at: datetime


class OAuthTokenRequest(BaseModel):
    grant_type: Literal["authorization_code"] = "authorization_code"
    client_id: str
    code: str
    redirect_uri: str
    code_verifier: str


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    scope: str | None = None
    api_key: str


class _StoredCode(BaseModel):
    id: UUID
    code_challenge: str
    code_challenge_method: str
    redirect_uri: str
    client_id: str
    scope: str | None
    user_id: UUID
    expires_at: datetime
    used_at: datetime | None


def _allowed_client_ids() -> set[str]:
    raw = os.getenv("CLI_OAUTH_CLIENT_IDS", "em-cli,openclaw")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _allow_oob_redirect() -> bool:
    return os.getenv("CLI_OAUTH_ALLOW_OOB", "true").lower() in {"1", "true", "yes"}


def _is_allowed_redirect_uri(redirect_uri: str) -> bool:
    if redirect_uri == OOB_REDIRECT_URI:
        return _allow_oob_redirect()

    parsed = urlparse(redirect_uri)
    if not parsed.scheme or not parsed.netloc:
        return False

    host = parsed.hostname or ""
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return True

    allowlist_raw = os.getenv("CLI_OAUTH_REDIRECT_ALLOWLIST", "")
    if allowlist_raw:
        allowlist = [item.strip() for item in allowlist_raw.split(",") if item.strip()]
        for entry in allowlist:
            if entry.endswith("/*") and redirect_uri.startswith(entry[:-1]):
                return True
            if redirect_uri == entry:
                return True
    return False


def _validate_client_id(client_id: str) -> None:
    allowed = _allowed_client_ids()
    if client_id not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid client_id")


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


async def _insert_code(
    conn: asyncpg.Connection,
    *,
    client_id: str,
    redirect_uri: str,
    scope: str | None,
    code_challenge: str,
    code_challenge_method: str,
    user_id: str,
    state: str | None,
    expires_at: datetime,
) -> str:
    for _ in range(5):
        code = secrets.token_urlsafe(32)
        try:
            await conn.execute(
                """
                INSERT INTO oauth_authorization_codes (
                    code,
                    client_id,
                    redirect_uri,
                    scope,
                    code_challenge,
                    code_challenge_method,
                    user_id,
                    state,
                    expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                code,
                client_id,
                redirect_uri,
                scope,
                code_challenge,
                code_challenge_method,
                user_id,
                state,
                expires_at,
            )
            return code
        except asyncpg.UniqueViolationError:
            continue
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to generate authorization code",
    )


async def _load_code(conn: asyncpg.Connection, code: str) -> _StoredCode | None:
    row = await conn.fetchrow(
        """
        SELECT id,
               code_challenge,
               code_challenge_method,
               redirect_uri,
               client_id,
               scope,
               user_id,
               expires_at,
               used_at
        FROM oauth_authorization_codes
        WHERE code = $1
        """,
        code,
    )
    if not row:
        return None
    return _StoredCode(**dict(row))


@router.post("/authorize", response_model=OAuthAuthorizeResponse)
async def oauth_authorize(
    payload: OAuthAuthorizeRequest,
    user: User = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db),
):
    if payload.response_type != "code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported response_type"
        )

    _validate_client_id(payload.client_id)

    if not _is_allowed_redirect_uri(payload.redirect_uri):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid redirect_uri")

    if payload.code_challenge_method not in {"S256", "plain"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code_challenge_method"
        )

    ttl_seconds = int(os.getenv("CLI_OAUTH_CODE_TTL_S", "300"))
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

    code = await _insert_code(
        conn,
        client_id=payload.client_id,
        redirect_uri=payload.redirect_uri,
        scope=payload.scope,
        code_challenge=payload.code_challenge,
        code_challenge_method=payload.code_challenge_method,
        user_id=str(user.id),
        state=payload.state,
        expires_at=expires_at,
    )

    return OAuthAuthorizeResponse(
        code=code,
        redirect_uri=payload.redirect_uri,
        state=payload.state,
        expires_at=expires_at,
    )


@router.post("/token", response_model=OAuthTokenResponse)
async def oauth_token(
    payload: OAuthTokenRequest,
    conn: asyncpg.Connection = Depends(get_db),
):
    if payload.grant_type != "authorization_code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported grant_type"
        )

    _validate_client_id(payload.client_id)

    stored = await _load_code(conn, payload.code)
    if not stored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code")

    if stored.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code already used")

    if stored.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code expired")

    if stored.client_id != payload.client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="client_id mismatch")

    if stored.redirect_uri != payload.redirect_uri:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="redirect_uri mismatch")

    if stored.code_challenge_method == "S256":
        expected = _pkce_challenge(payload.code_verifier)
    elif stored.code_challenge_method == "plain":
        expected = payload.code_verifier
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code_challenge_method"
        )

    if not secrets.compare_digest(expected, stored.code_challenge):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid code_verifier")

    res = await conn.execute(
        """
        UPDATE oauth_authorization_codes
        SET used_at = $2
        WHERE id = $1 AND used_at IS NULL
        """,
        stored.id,
        datetime.now(UTC),
    )
    if res != "UPDATE 1":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Code already used")

    project = await ProjectRepository.ensure_default_project(
        conn,
        owner_id=stored.user_id,
        seed_source="oauth-cli",
    )

    full_key, prefix, salt, secret_hash = generate_project_api_key()
    await ProjectApiKeyRepository.create_key(
        conn,
        project_id=project.id,
        created_by=stored.user_id,
        name="OpenClaw CLI",
        prefix=prefix,
        secret_hash=secret_hash,
        salt=salt,
        scopes=["read", "write"],
        metadata={
            "source": "oauth-cli",
            "client_id": payload.client_id,
            "scope": stored.scope,
        },
        expires_at=None,
    )

    return OAuthTokenResponse(
        access_token=full_key,
        token_type="bearer",
        scope=stored.scope,
        api_key=full_key,
    )
