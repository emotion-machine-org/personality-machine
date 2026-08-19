import asyncio
import json
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Tuple

import asyncpg
from asyncpg import Pool
from dotenv import load_dotenv
from fastapi import HTTPException, status

from .request_context import try_get_request_context


async def _setup_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register JSONB codec to automatically encode/decode Python dicts.

    Without this, asyncpg returns JSONB columns as raw JSON strings,
    which causes issues throughout the codebase.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Database connection pool
_pool: Pool = None

# Simple instrumentation to help detect connection leaks/long holds
# Maps connection id -> (acquired_ts, task_name, stack)
_inflight: Dict[int, Tuple[float, str, str]] = {}


def _snapshot_inflight() -> List[Dict[str, Any]]:
    try:
        items = []
        now = time.time()
        for key, (acq, task, stack) in list(_inflight.items()):
            items.append(
                {
                    "conn_id": key,
                    "held_ms": int((now - acq) * 1000),
                    "task": task,
                    "where": stack,
                }
            )
        items.sort(key=lambda x: x["held_ms"], reverse=True)
        return items
    except Exception:
        return []


async def init_db() -> Pool:
    """Initialize database connection pool"""
    global _pool

    database_url = os.getenv("DATABASE_DSN")
    if not database_url:
        raise ValueError("DATABASE_DSN environment variable is required")

    pool_max_size = max(1, int(os.getenv("DB_POOL_MAX_SIZE", "12")))
    pool_min_size = min(max(1, int(os.getenv("DB_POOL_MIN_SIZE", "2"))), pool_max_size)

    try:
        _pool = await asyncpg.create_pool(
            database_url,
            min_size=pool_min_size,
            # Per-instance pool size. Default 12; tune via DB_POOL_MAX_SIZE.
            # Supabase max_connections is 60, so the safe ceiling depends on
            # how many app instances and other services share the database.
            max_size=pool_max_size,
            command_timeout=60,
            statement_cache_size=0,  # Disable prepared statements for pgbouncer compatibility
            server_settings={
                "jit": "off",  # Disable JIT for better connection startup
                "application_name": "emotion_machine_api",
            },
            # Safety valve to reap stale idles (helps restart scenarios)
            max_inactive_connection_lifetime=60.0,
            # Register JSONB codec for each new connection
            init=_setup_jsonb_codec,
        )
        logger.info(
            "Database connection pool initialized with JSONB codec (min_size=%s, max_size=%s)",
            pool_min_size,
            pool_max_size,
        )
        return _pool
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        raise


async def close_db():
    """Close database connection pool"""
    global _pool
    if _pool:
        try:
            # Best-effort graceful shutdown; avoid hanging the app if holders linger
            await asyncio.wait_for(_pool.close(), timeout=10)
            logger.info("Database connection pool closed")
        except Exception as e:
            # Print any connections still held at shutdown
            inflight = _snapshot_inflight()
            if inflight:
                logger.warning(
                    "DB pool close timeout; %d connection(s) still held: %s",
                    len(inflight),
                    inflight[:5],
                )
            logger.warning(f"Timed out or failed to close DB pool cleanly: {e}")
            # Force terminate to avoid zombie processes on reload/kill
            try:
                term = getattr(_pool, "terminate", None)
                if term:
                    term()  # asyncpg.Pool.terminate() is sync
                    logger.warning("DB pool forcibly terminated")
            except Exception as te:
                logger.warning(f"Failed to force-terminate DB pool: {te}")
        finally:
            _pool = None


def get_pool() -> Pool:
    """Get the database connection pool"""
    global _pool
    if not _pool:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database not available"
        )
    return _pool


@asynccontextmanager
async def get_db_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Get a database connection from the pool.

    Important: Do not swallow exceptions from upstream dependencies (like auth).
    Only provide the connection and ensure it is returned to the pool.
    """
    pool = get_pool()
    # Track acquisitions to debug leaks/long holds
    async with pool.acquire() as connection:
        try:
            task = asyncio.current_task()
            task_name = getattr(task, "get_name", lambda: "task")()
            _inflight[id(connection)] = (
                time.time(),
                str(task_name),
                "\n".join(traceback.format_stack(limit=8)),
            )
        except Exception:
            pass
        try:
            # Yield the connection without wrapping downstream exceptions.
            # This avoids converting 401 auth errors into 500 DB errors.
            yield connection
        finally:
            try:
                _inflight.pop(id(connection), None)
            except Exception:
                pass


# Dependency for FastAPI
async def get_db() -> AsyncGenerator[asyncpg.Connection, None]:
    """FastAPI dependency for database connection.

    HTTP requests already get a connection from DatabaseSessionMiddleware and
    store it in RequestContext. Reuse that connection so a request does not
    consume two pool slots.
    """
    ctx = None
    try:
        ctx = try_get_request_context()
    except Exception:
        logger.debug("Falling back to direct DB connection", exc_info=True)

    if ctx is not None and ctx.has_conn:
        yield ctx.conn
        return

    async with get_db_connection() as connection:
        yield connection


# ──────────────────────────────────────────────────────────────────────────────
# Conversation Persistence Functions
# Following the CONVERSATION_PERSISTENCE_PLAN for session-based conversation management
# ──────────────────────────────────────────────────────────────────────────────

from typing import Dict
from uuid import UUID

# In-memory session-to-conversation mapping for real-time tracking
# These are only needed during active WebSocket sessions
# Continuation uses conversation_id passed explicitly by client
_session_conversations: Dict[str, UUID] = {}
_conversation_states: Dict[UUID, str] = {}


async def persist_message(
    conversation_id: UUID,
    role: str,
    content: str,
    *,
    input_modality: str | None = None,
) -> UUID:
    """
    Non-blocking message persistence using FastAPI's async handling.
    Designed to not interfere with real-time voice sessions.

    Args:
        conversation_id: ID of the conversation
        role: Message role ('system', 'user', 'assistant')
        content: Message content
    """
    try:
        logger.info(
            f"[PERSIST_MESSAGE] Attempting to persist {role} message for conversation {conversation_id}: {content[:100]}..."
        )
        from .repositories.conversation import add_message

        async with get_db_connection() as conn:
            message_id = await add_message(
                conn,
                conversation_id,
                role,
                content,
                input_modality=input_modality,
            )
            logger.info(
                f"[PERSIST_MESSAGE] Successfully persisted {role} message {message_id} for conversation {conversation_id}"
            )
            return message_id
    except Exception as e:
        logger.error(
            f"[PERSIST_MESSAGE] Failed to persist {role} message for conversation {conversation_id}: {e}",
            exc_info=True,
        )
        # Don't re-raise - persistence failures shouldn't break voice sessions
        return UUID(int=0)


async def create_conversation_for_session(
    session_id: str,
    deployment_id: UUID,
    *,
    share_id: UUID | None = None,
    share_token_hash: bytes | None = None,
) -> UUID:
    """
    Always create new conversation for each session (testing workflow).
    Each "Press to Speak" creates a fresh conversation with no prior context.

    Args:
        session_id: WebSocket session identifier
        deployment_id: ID of the companion deployment being tested

    Returns:
        UUID of the created conversation
    """
    logger.info(
        f"[CREATE_CONVERSATION] Creating conversation for session {session_id} with deployment {deployment_id}"
    )
    try:
        from .repositories.conversation import create_conversation

        async with get_db_connection() as conn:
            logger.info(
                "[CREATE_CONVERSATION] Got database connection, calling create_conversation"
            )
            conversation_id = await create_conversation(
                conn,
                deployment_id,
                session_id,  # Use session_id as external_user_id
                share_id=share_id,
                share_token_hash=share_token_hash,
            )
            logger.info(
                f"[CREATE_CONVERSATION] Database create_conversation returned {conversation_id}"
            )

        _session_conversations[session_id] = conversation_id
        _conversation_states[conversation_id] = "voice_active"
        logger.info(
            f"[CREATE_CONVERSATION] Successfully created conversation {conversation_id} for session {session_id}, stored in memory"
        )
        logger.info(
            f"[CREATE_CONVERSATION] Session conversations mapping: {_session_conversations}"
        )
        return conversation_id
    except Exception as e:
        logger.error(
            f"[CREATE_CONVERSATION] Failed to create conversation for session {session_id}: {e}",
            exc_info=True,
        )
        raise


async def debug_database_connection() -> bool:
    """
    Debug helper to verify database connectivity and table existence.
    Returns True if everything is working, False otherwise.
    """
    try:
        logger.info("[DEBUG_DB] Testing database connection and table access...")
        async with get_db_connection() as conn:
            # Test basic connectivity
            result = await conn.fetchval("SELECT 1")
            logger.info(f"[DEBUG_DB] Basic connectivity test result: {result}")

            # Test conversations table
            conv_count = await conn.fetchval("SELECT COUNT(*) FROM conversations")
            logger.info(f"[DEBUG_DB] Conversations table count: {conv_count}")

            # Test messages table
            msg_count = await conn.fetchval("SELECT COUNT(*) FROM messages")
            logger.info(f"[DEBUG_DB] Messages table count: {msg_count}")

            # Smoke check that input_modality column exists
            modality_sample = await conn.fetchval("SELECT input_modality FROM messages LIMIT 1")
            logger.info(
                "[DEBUG_DB] messages.input_modality sample: %s",
                modality_sample,
            )

            logger.info("[DEBUG_DB] Database connectivity and tables verified successfully")
            return True
    except Exception as e:
        logger.error(f"[DEBUG_DB] Database connectivity test failed: {e}", exc_info=True)
        return False


def get_conversation_id(session_id: str) -> UUID | None:
    """
    Get conversation ID for a session.

    Args:
        session_id: WebSocket session identifier

    Returns:
        Conversation UUID or None if not found
    """
    return _session_conversations.get(session_id)


def register_session_conversation(session_id: str, conversation_id: UUID) -> None:
    """
    Record a mapping between a WebSocket session and a conversation.

    Args:
        session_id: WebSocket session identifier
        conversation_id: Conversation UUID
    """
    _session_conversations[session_id] = conversation_id
    _conversation_states.setdefault(conversation_id, "voice_active")


def set_conversation_state(conversation_id: UUID, state: str) -> None:
    """
    Track conversation state transitions.

    Args:
        conversation_id: ID of the conversation
        state: New state ('voice_active', 'voice_ended', 'text_active')
    """
    _conversation_states[conversation_id] = state
    logger.debug(f"Set conversation {conversation_id} state to {state}")


def get_conversation_state(conversation_id: UUID) -> str:
    """
    Get current conversation state.

    Args:
        conversation_id: ID of the conversation

    Returns:
        Current state or 'unknown' if not tracked
    """
    return _conversation_states.get(conversation_id, "unknown")


def cleanup_session_conversation(session_id: str) -> None:
    """
    Clean up bookkeeping when a WebSocket session ends.

    Removes the session mapping immediately since:
    - Each new WebSocket connection gets a new session_id
    - Conversation continuation passes conversation_id explicitly
    - The mapping is only useful during an active session

    Args:
        session_id: WebSocket session identifier
    """
    conversation_id = _session_conversations.pop(session_id, None)
    if conversation_id:
        # Clean up conversation state if no other session references it
        if conversation_id not in _session_conversations.values():
            _conversation_states.pop(conversation_id, None)
        logger.info(
            f"[DB] Cleaned up session {session_id} (conversation {conversation_id}). Active sessions: {len(_session_conversations)}"
        )
