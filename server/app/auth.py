import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Tuple
from uuid import UUID

import asyncpg
from clerk_backend_api import AuthenticateRequestOptions, Clerk
from fastapi import Depends, HTTPException, Request, status

from .db import get_db, get_db_connection
from .models.project import Project, ProjectApiKey
from .models.user import User, UserCreate
from .repositories.project import ProjectApiKeyRepository, ProjectRepository
from .repositories.user import UserRepository
from .request_context import try_get_request_context
from .services.api_keys import parse_api_key, verify_secret
from .services.cache_manager import cache, ttl_from_env

# Cache TTL for the API-key record lookup (5 minutes - API keys rarely change)
_API_KEY_CACHE_TTL_S = ttl_from_env("API_KEY_CACHE_TTL_S", 300.0)

# Cache TTL for the PBKDF2 verify result. A successful verify is remembered
# for this many seconds so subsequent requests with the same token skip the
# KDF. This bounds the revocation lag for an active key.
_API_KEY_VERIFY_TTL_S = ttl_from_env("API_KEY_VERIFY_TTL_S", 60.0)

# Updating last_used_at is operational metadata, not part of request success.
# Coalesce it so traffic bursts do not create one DB-writing background task per
# request and consume the asyncpg pool behind active handlers.
_API_KEY_MARK_USED_TTL_S = ttl_from_env("API_KEY_MARK_USED_TTL_S", 60.0)
_API_KEY_MARK_USED_TIMEOUT_S = ttl_from_env("API_KEY_MARK_USED_TIMEOUT_S", 2.0)
_api_key_mark_used_lock = asyncio.Lock()
_api_key_mark_used_inflight: set[UUID] = set()
_api_key_mark_used_last_scheduled: dict[UUID, float] = {}


def _verify_cache_key(prefix: str, secret: str) -> str:
    """Build cache key for a verified bearer token.

    Hashed so the secret never sits in the cache key in plaintext.
    """
    return hashlib.sha256(f"{prefix}.{secret}".encode()).hexdigest()


async def _mark_api_key_used_once(api_key_id: UUID) -> None:
    async with get_db_connection() as conn:
        await ProjectApiKeyRepository.mark_used(conn, api_key_id)


async def _background_mark_api_key_used(api_key_id: UUID) -> None:
    """Background task to update API key last_used_at timestamp.

    Uses its own connection to avoid blocking the request.
    This is fire-and-forget - failures are logged but don't affect the request.
    """
    try:
        if _API_KEY_MARK_USED_TIMEOUT_S > 0:
            await asyncio.wait_for(
                _mark_api_key_used_once(api_key_id),
                timeout=_API_KEY_MARK_USED_TIMEOUT_S,
            )
        else:
            await _mark_api_key_used_once(api_key_id)
    except TimeoutError:
        logger.warning(
            "Timed out updating last_used_at for API key %s after %.2fs",
            api_key_id,
            _API_KEY_MARK_USED_TIMEOUT_S,
        )
    except Exception:
        logger.warning(
            "Background task failed to update last_used_at for API key %s",
            api_key_id,
            exc_info=True,
        )
    finally:
        async with _api_key_mark_used_lock:
            _api_key_mark_used_inflight.discard(api_key_id)


async def _schedule_mark_api_key_used(api_key_id: UUID) -> bool:
    """Schedule a coalesced last_used_at update for an API key.

    Returns True when a background task was started. Calls inside the TTL or
    while an update is already in flight are intentionally skipped.
    """
    now = time.monotonic()
    async with _api_key_mark_used_lock:
        if api_key_id in _api_key_mark_used_inflight:
            return False

        last_scheduled = _api_key_mark_used_last_scheduled.get(api_key_id)
        if (
            _API_KEY_MARK_USED_TTL_S > 0
            and last_scheduled is not None
            and now - last_scheduled < _API_KEY_MARK_USED_TTL_S
        ):
            return False

        _api_key_mark_used_last_scheduled[api_key_id] = now
        _api_key_mark_used_inflight.add(api_key_id)

    asyncio.create_task(_background_mark_api_key_used(api_key_id))
    return True


# Initialize Clerk SDK instances
# Primary (emotionmachine)
clerk = Clerk(bearer_auth=os.getenv("CLERK_SECRET_KEY"))
CLERK_JWT_KEY = os.getenv("CLERK_JWT_KEY")

logger = logging.getLogger(__name__)


def _normalize_auth_provider(provider: str | None) -> str:
    """Normalize provider strings coming from Clerk claims or external accounts."""
    if not provider:
        return "email"

    normalized = provider.strip().lower()
    for prefix in ("oauth_", "oauth-", "oauth2_"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break

    if normalized.endswith("_oauth"):
        normalized = normalized[: -len("_oauth")]

    # Clerk sometimes reports strategies such as "email_link" or "password".
    if normalized in {"password", "email_password", "email_link"}:
        return "email"

    return normalized or "email"


def _extract_bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts[0].strip().lower(), parts[1].strip()
    if scheme != "bearer" or not token:
        return None
    return token


def _iter_external_accounts(clerk_user: Any) -> Iterable[Any]:
    """Safely iterate over external accounts regardless of SDK return shape."""
    if clerk_user is None:
        return []

    accounts = getattr(clerk_user, "external_accounts", None)
    if accounts is None:
        return []

    # The backend SDK may expose .data or behave like a list.
    if isinstance(accounts, dict):
        data = accounts.get("data") or accounts.get("items") or []
        return data

    return accounts  # type: ignore[return-value]


def _determine_auth_provider(token_data: Dict[str, Any], clerk_user: Any = None) -> str:
    """Extract the authentication provider (e.g. 'google') from Clerk payloads."""
    provider_claim_keys = (
        "provider",
        "auth_provider",
        "authentication_strategy",
        "strategy",
        "sign_in_strategy",
        "https://clerk.dev/auth_provider",
        "https://clerk.dev/strategy",
        "https://clerk.dev/external_account_strategy",
    )

    for key in provider_claim_keys:
        value = token_data.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_auth_provider(value)

    session_claims = token_data.get("session_claims")
    if isinstance(session_claims, dict):
        for key in ("strategy", "auth_type", "sign_in_strategy"):
            value = session_claims.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_auth_provider(value)

    for account in _iter_external_accounts(clerk_user):
        if isinstance(account, dict):
            candidate = account.get("provider") or account.get("strategy")
        else:
            candidate = getattr(account, "provider", None) or getattr(account, "strategy", None)
        if isinstance(candidate, str) and candidate.strip():
            return _normalize_auth_provider(candidate)

    return _normalize_auth_provider(None)


@dataclass
class ProjectApiKeySubject:
    project: Project
    api_key: ProjectApiKey
    token: str


def _api_key_cache_key(prefix: str) -> str:
    """Build cache key for API key validation."""
    return prefix


def _normalize_api_key_metadata(record_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize metadata field in API key record."""
    metadata = record_dict.get("metadata")
    if isinstance(metadata, str):
        try:
            record_dict["metadata"] = json.loads(metadata)
        except json.JSONDecodeError:
            record_dict["metadata"] = {}
    elif metadata is None:
        record_dict["metadata"] = {}
    return record_dict


def _build_api_key_model(record_dict: Dict[str, Any]) -> ProjectApiKey:
    """Build ProjectApiKey model from record dict."""
    return ProjectApiKey(
        id=record_dict["id"],
        project_id=record_dict["project_id"],
        created_by=record_dict["created_by"],
        name=record_dict["name"],
        prefix=record_dict["prefix"],
        status=record_dict["status"],
        scopes=list(record_dict["scopes"] or []),
        metadata=record_dict.get("metadata") or {},
        created_at=record_dict["created_at"],
        last_used_at=record_dict["last_used_at"],
        expires_at=record_dict["expires_at"],
    )


def invalidate_api_key_cache(prefix: str) -> None:
    """Invalidate cached API key data.

    Call this when an API key is revoked, updated, or deleted.
    """
    cache.delete("api_key_auth", _api_key_cache_key(prefix))


async def get_project_api_subject(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
) -> ProjectApiKeySubject:
    """Authenticate an API key supplied via Authorization: Bearer header.

    Uses caching to avoid repeated DB queries for the same API key.
    Cached data: (record_dict, project, secret_hash, salt)

    A second cache caches the result of PBKDF2 verification keyed by a
    SHA-256 of the bearer token so that hot tokens skip the ~200ms KDF
    cost on every request. The verify cache TTL bounds revocation lag.
    """

    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        parsed = parse_api_key(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check cache first
    cache_key = _api_key_cache_key(parsed.prefix)
    cached: Tuple[Dict[str, Any], Project, bytes, bytes] | None = cache.get(
        "api_key_auth", cache_key
    )

    if cached is not None:
        record_dict, project, secret_hash, salt = cached
    else:
        # Cache miss - fetch from database
        record = await ProjectApiKeyRepository.fetch_by_prefix(conn, parsed.prefix)
        if not record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        record_dict = _normalize_api_key_metadata(dict(record))
        secret_hash = bytes(record["secret_hash"])
        salt = bytes(record["salt"])

        project = await ProjectRepository.get_project_by_id(conn, record_dict["project_id"])
        if not project:
            logger.error(
                "API key %s references missing project %s", record["id"], record["project_id"]
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Cache the result (only cache active keys)
        if record_dict["status"].lower() == "active":
            cache.set(
                "api_key_auth",
                cache_key,
                (record_dict, project, secret_hash, salt),
                _API_KEY_CACHE_TTL_S,
            )

    # Verify-cache: if the same bearer token has verified recently, skip
    # the expensive PBKDF2 round. Revocation lag is bounded by TTL.
    verify_cache_key = _verify_cache_key(parsed.prefix, parsed.secret)
    if cache.get("api_key_verify", verify_cache_key) is None:
        ok = await asyncio.to_thread(verify_secret, parsed.secret, salt, secret_hash)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        cache.set("api_key_verify", verify_cache_key, True, _API_KEY_VERIFY_TTL_S)

    # Validate status and expiry
    status_value: str = record_dict["status"]
    expires_at: datetime | None = record_dict["expires_at"]

    if status_value.lower() != "active":
        # Invalidate cache for inactive keys
        cache.delete("api_key_auth", cache_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is not active",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if expires_at is not None and expires_at < datetime.now(UTC):
        # Invalidate cache for expired keys
        cache.delete("api_key_auth", cache_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    api_key = _build_api_key_model(record_dict)

    # Fire-and-forget background task to update last_used_at. It is coalesced
    # per API key so bursts do not turn operational metadata writes into pool
    # pressure on customer-facing requests.
    await _schedule_mark_api_key_used(api_key.id)

    # Populate RequestContext with authentication data
    # This eliminates redundant project_id queries downstream
    ctx = try_get_request_context()
    if ctx:
        ctx.project_id = project.id
        ctx.api_key_id = api_key.id

    return ProjectApiKeySubject(project=project, api_key=api_key, token=token)


def _build_authorized_parties(request: Request) -> list[str] | None:
    """Resolve authorized parties, supporting Vercel preview origins."""
    if os.getenv("ENV") in ["dev", "preview"]:
        origin = request.headers.get("origin")
        if not origin:
            host = request.headers.get("x-forwarded-host") or request.headers.get("host")
            proto = request.headers.get("x-forwarded-proto") or request.url.scheme
            if host and proto:
                origin = f"{proto}://{host}"

        env_parties = [
            item.strip()
            for item in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",")
            if item.strip()
        ]
        parties = [p for p in [origin] if p] + env_parties
        return parties or None

    parties = [
        item.strip()
        for item in os.getenv("CLERK_AUTHORIZED_PARTIES", "http://localhost:3000").split(",")
        if item.strip()
    ]
    return parties


def _is_mock_dev_token(auth_header: str | None) -> bool:
    """Check for mock dev token when auth bypass is enabled."""
    return bool(
        os.getenv("DISABLE_AUTH_FOR_TESTING") == "true"
        and auth_header
        and auth_header.strip().endswith("mock-dev-token")
    )


async def verify_clerk_token(request: Request) -> Dict[str, Any]:
    """Verify Clerk JWT token from request.

    - Uses networkless verification when `CLERK_JWT_KEY` is provided (intended for JWT templates).
    - Falls back to network-based verification if networkless check reports not signed in.
    - Honors development bypass via `DISABLE_AUTH_FOR_TESTING=true`.
    """
    if os.getenv("DISABLE_AUTH_FOR_TESTING") == "true":
        logger.warning("Authentication bypassed for development testing")
        return {"sub": "dev-user-clerk-id", "email": "dev@example.com", "username": "dev-user"}

    auth_header = request.headers.get("authorization")
    logger.info(f"Auth header present: {bool(auth_header)}")
    if not auth_header:
        logger.error("No authorization header found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing. Please provide Bearer token.",
        )

    authorized_parties = _build_authorized_parties(request)
    logger.info(f"Authorized parties: {authorized_parties}")

    if _is_mock_dev_token(auth_header):
        logger.warning("Development bypass active with mock-dev-token header")
        return {"sub": "dev-user-clerk-id", "email": "dev@example.com", "username": "dev-user"}

    try:
        options = AuthenticateRequestOptions(authorized_parties=authorized_parties)
        used_networkless = False
        if CLERK_JWT_KEY:
            options.jwt_key = CLERK_JWT_KEY
            used_networkless = True
            logger.info("Using networkless JWT verification")
        else:
            logger.info("Using network-based verification (no JWT key)")

        request_state = clerk.authenticate_request(request, options)

        if used_networkless and not request_state.is_signed_in:
            logger.warning(
                "Networkless verification reported unsigned; retrying with network-based verification with reason: %s",
                request_state.reason,
            )
            options_network = AuthenticateRequestOptions(authorized_parties=authorized_parties)
            request_state = clerk.authenticate_request(request, options_network)

        if not request_state.is_signed_in:
            logger.error(
                "Request state indicates user not signed in with reason: %s", request_state.reason
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        logger.info(
            f"Authentication successful for user: {request_state.payload.get('sub', 'unknown')}"
        )
        return request_state.payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication error: {e!s}",
        )


def _should_sync_user(user: User) -> bool:
    """Check if user data should be synced (older than 24 hours)"""
    if not user.updated_at:
        return True
    return datetime.now(UTC) - user.updated_at > timedelta(hours=24)


def _extract_primary_email(
    clerk_user: Any,
    token_data: Dict[str, Any],
    clerk_user_id: str,
    *,
    include_placeholder_fallback: bool = True,
) -> str | None:
    """Extract best-effort email from Clerk user payload, then token claims, then fallback."""
    email: str | None = None
    email_records = getattr(clerk_user, "email_addresses", None)
    if email_records:
        if isinstance(email_records, dict):
            candidates = email_records.get("data") or email_records.get("items") or []
        else:
            candidates = email_records

        primary_id = getattr(clerk_user, "primary_email_address_id", None)
        for record in candidates:
            record_email = (
                record.get("email_address")
                if isinstance(record, dict)
                else getattr(record, "email_address", None)
            )
            record_id = (
                record.get("id") if isinstance(record, dict) else getattr(record, "id", None)
            )
            if record_email:
                email = record_email
                if primary_id and record_id == primary_id:
                    break

    email = email or token_data.get("email") or token_data.get("email_address")
    if email:
        return email
    if include_placeholder_fallback:
        return f"{clerk_user_id}@users.clerk"
    return None


async def _reconcile_user_by_email(
    conn: asyncpg.Connection,
    *,
    active_user: User | None,
    clerk_user_id: str,
    email: str,
    username: str | None,
    display_name: str | None,
    avatar_url: str | None,
    auth_provider: str | None,
) -> User | None:
    """Relink the current Clerk identity to an existing user with the same email."""
    if not email or email.endswith("@users.clerk"):
        return None

    existing = await UserRepository.get_by_email(conn, email)
    if not existing:
        return None

    if active_user and existing.id == active_user.id:
        return None

    logger.warning(
        "Reconciling user identity by email | clerk_user_id=%s active_user_id=%s existing_user_id=%s email=%s",
        clerk_user_id,
        str(active_user.id) if active_user else None,
        str(existing.id),
        email,
    )

    async with conn.transaction():
        if active_user and active_user.id != existing.id:
            retired_clerk_id = f"merged:{active_user.id}"
            await conn.execute(
                """
                UPDATE users
                SET clerk_user_id = $1, updated_at = CURRENT_TIMESTAMP
                WHERE id = $2
                """,
                retired_clerk_id,
                active_user.id,
            )

        reassigned = await UserRepository.reassign_clerk_identity(
            conn,
            canonical_user_id=str(existing.id),
            new_clerk_user_id=clerk_user_id,
            email=email,
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
            auth_provider=auth_provider,
        )

    return reassigned


async def _create_user_from_token(
    token_data: Dict[str, Any], clerk_user_id: str, conn: asyncpg.Connection
) -> User:
    """Create user with minimal data from token, fallback if Clerk API fails"""
    clerk_user = None
    try:
        clerk_user = clerk.users.get(user_id=clerk_user_id)
    except Exception as e:
        logger.warning(f"Failed to fetch user from Clerk API: {e}")

    auth_provider = _determine_auth_provider(token_data, clerk_user)
    if clerk_user:
        email = _extract_primary_email(clerk_user, token_data, clerk_user_id)
        username = getattr(clerk_user, "username", None) or (email.split("@")[0] if email else None)
        full_name = getattr(clerk_user, "full_name", None)
        display_name = " ".join(
            part
            for part in (
                getattr(clerk_user, "first_name", None),
                getattr(clerk_user, "last_name", None),
            )
            if part
        ).strip()
        if not display_name:
            display_name = full_name or username or email.split("@")[0]
        avatar_url = clerk_user.profile_image_url
    else:
        email = (
            token_data.get("email")
            or token_data.get("email_address")
            or (f"{clerk_user_id}@users.clerk")
        )
        username = token_data.get("username") or (
            email.split("@")[0] if email else f"user_{clerk_user_id[:8]}"
        )
        display_name = token_data.get("display_name") or token_data.get("name") or username
        avatar_url = None

    reconciled = await _reconcile_user_by_email(
        conn,
        active_user=None,
        clerk_user_id=clerk_user_id,
        email=email,
        username=username,
        display_name=display_name,
        avatar_url=avatar_url,
        auth_provider=auth_provider,
    )
    if reconciled:
        return reconciled

    user_data = UserCreate(
        clerk_user_id=clerk_user_id,
        email=email,
        username=username,
        display_name=display_name,
        avatar_url=avatar_url,
        auth_provider=auth_provider,
    )
    try:
        user = await UserRepository.create(conn, user_data)
    except HTTPException as e:
        if e.status_code == status.HTTP_409_CONFLICT:
            reconciled = await _reconcile_user_by_email(
                conn,
                active_user=None,
                clerk_user_id=clerk_user_id,
                email=email,
                username=username,
                display_name=display_name,
                avatar_url=avatar_url,
                auth_provider=auth_provider,
            )
            if reconciled:
                return reconciled
        raise

    return user


async def _sync_user_data(clerk_user_id: str, user: User, conn: asyncpg.Connection) -> User:
    """Sync user data from Clerk API if stale"""
    try:
        clerk_user = clerk.users.get(user_id=clerk_user_id)

        updates = {}
        new_email = _extract_primary_email(
            clerk_user,
            {},
            clerk_user_id,
            include_placeholder_fallback=False,
        )
        new_username = clerk_user.username if clerk_user.username else user.username
        new_avatar_url = (
            clerk_user.profile_image_url if clerk_user.profile_image_url else user.avatar_url
        )
        new_display_name = f"{clerk_user.first_name or ''} {clerk_user.last_name or ''}".strip()
        if not new_display_name:
            new_display_name = user.display_name
        new_provider = _determine_auth_provider({}, clerk_user)

        if new_email and user.email != new_email:
            reconciled = await _reconcile_user_by_email(
                conn,
                active_user=user,
                clerk_user_id=clerk_user_id,
                email=new_email,
                username=new_username,
                display_name=new_display_name,
                avatar_url=new_avatar_url,
                auth_provider=new_provider,
            )
            if reconciled:
                return reconciled
            updates["email"] = new_email

        if new_username and user.username != new_username:
            updates["username"] = new_username

        if new_avatar_url and user.avatar_url != new_avatar_url:
            updates["avatar_url"] = new_avatar_url

        if new_display_name and user.display_name != new_display_name:
            updates["display_name"] = new_display_name

        if new_provider and new_provider != user.auth_provider:
            updates["auth_provider"] = new_provider

        if updates:
            from .models.user import UserUpdate

            updated_user = await UserRepository.update_by_clerk_id(
                conn, clerk_user_id, UserUpdate(**updates)
            )
            return updated_user or user
    except Exception as e:
        logger.warning(f"Failed to sync user data from Clerk: {e}")
        # If API call fails, continue with existing user data
        pass

    return user


async def get_or_create_user(token_data: Dict[str, Any], conn: asyncpg.Connection) -> User:
    """Get or create user with optimized JIT sync"""
    clerk_user_id = token_data.get("sub")
    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    # Check if user exists
    user = await UserRepository.get_by_clerk_id(conn, clerk_user_id)

    if not user:
        # Create new user - for dev bypass, create with minimal data
        if os.getenv("DISABLE_AUTH_FOR_TESTING") == "true":
            user_data = UserCreate(
                clerk_user_id=clerk_user_id,
                email=token_data.get("email", "dev@example.com"),
                username=token_data.get("username", "dev-user"),
                display_name="Development User",
                auth_provider="development",
            )
            user = await UserRepository.create(conn, user_data)
        else:
            user = await _create_user_from_token(token_data, clerk_user_id, conn)
    # Sync stale users (>24h) and placeholder-email users immediately.
    elif os.getenv("DISABLE_AUTH_FOR_TESTING") != "true" and (
        _should_sync_user(user) or user.email.endswith("@users.clerk")
    ):
        user = await _sync_user_data(clerk_user_id, user, conn)

    await ProjectRepository.ensure_default_project(
        conn,
        user.id,
        seed_source="auth-auto-default",
    )

    return user


async def get_current_user(request: Request, conn: asyncpg.Connection = Depends(get_db)) -> User:
    """Get current authenticated user, with debug timing recorded on request.state."""
    import time as _t

    t0 = _t.perf_counter()
    token_data = await verify_clerk_token(request)
    user = await get_or_create_user(token_data, conn)
    try:
        request.state.em_auth_ms = (_t.perf_counter() - t0) * 1000
    except Exception:
        pass

    # Populate RequestContext with Clerk user data
    ctx = try_get_request_context()
    if ctx:
        ctx.user_id = user.clerk_user_id

    return user


# Optional: Dependency for optional authentication
async def get_current_user_optional(
    request: Request, conn: asyncpg.Connection = Depends(get_db)
) -> User | None:
    """Get current user if authenticated, otherwise None"""
    try:
        token_data = await verify_clerk_token(request)
        return await get_or_create_user(token_data, conn)
    except Exception:
        return None
