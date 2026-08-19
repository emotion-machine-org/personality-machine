"""API v2 WebSocket Router.

Implements WebSocket support for real-time bidirectional communication.
Phase 4 of the v2 API implementation.

Features:
- JWT token endpoint for WebSocket auth
- Connection manager for active connections
- Heartbeats and idle detection
- Message processing with unified event protocol
- Event replay via since_seq parameter
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from ...auth import ProjectApiKeySubject, get_project_api_subject
from ...context import ContextEvent
from ...db import get_db, get_db_connection
from ...models.v2.message import (
    TurnConfig,
    WsTokenResponse,
)
from ...models.v2.relationship import Relationship
from ...repositories.companion import CompanionRepository
from ...repositories.relationship_repository import RelationshipRepository
from ...services.message_processor import (
    EventEmitter,
    TurnInput,
    TurnProcessor,
)
from ...services.message_processor import (
    TurnConfig as ProcessorTurnConfig,
)

router = APIRouter(prefix="/v2", tags=["v2-websockets"])
logger = logging.getLogger(__name__)

# Configuration
WS_TOKEN_SECRET = os.environ.get("WS_TOKEN_SECRET", "dev-ws-secret-change-in-production")
WS_TOKEN_EXPIRY_SECONDS = 3600  # 1 hour
HEARTBEAT_INTERVAL_SECONDS = 30
IDLE_TIMEOUT_SECONDS = 300  # 5 minutes
MAX_CONNECTION_DURATION_SECONDS = 86400  # 24 hours
TOKEN_EXPIRING_WARNING_SECONDS = 300  # 5 min before expiry
INTRO_MESSAGE_SENT_AT_KEY = "intro_message_sent_at"
DEFAULT_INTRO_MESSAGE_TEXT = "Hi, how are you?"

# WebSocket close codes
WS_CLOSE_NORMAL = 1000
WS_CLOSE_GOING_AWAY = 1001
WS_CLOSE_PROTOCOL_ERROR = 1002
WS_CLOSE_INTERNAL_ERROR = 1011
WS_CLOSE_SERVICE_RESTART = 1012
WS_CLOSE_INVALID_TOKEN = 4001
WS_CLOSE_TOKEN_EXPIRED = 4002
WS_CLOSE_NOT_FOUND = 4004
WS_CLOSE_RATE_LIMITED = 4029


# -----------------------------------------------------------------------------
# Connection Manager
# -----------------------------------------------------------------------------


@dataclass
class ConnectionState:
    """State for an active WebSocket connection."""

    websocket: WebSocket
    relationship_id: UUID
    api_key_id: UUID
    connected_at: datetime
    last_activity_at: datetime
    token_expires_at: datetime
    token_expiring_warned: bool = False
    pending_turn: asyncio.Task | None = None
    debug_mode: bool = False  # When True, emit detailed trace events


class ConnectionManager:
    """Manages active WebSocket connections per relationship.

    This is an in-memory registry. On server restart, connections are dropped
    and clients auto-reconnect.
    """

    def __init__(self):
        # relationship_id -> list of connections (one relationship can have multiple connections)
        self._connections: dict[UUID, list[ConnectionState]] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        relationship_id: UUID,
        api_key_id: UUID,
        token_expires_at: datetime,
    ) -> ConnectionState:
        """Register a new connection."""
        now = datetime.now(UTC)
        state = ConnectionState(
            websocket=websocket,
            relationship_id=relationship_id,
            api_key_id=api_key_id,
            connected_at=now,
            last_activity_at=now,
            token_expires_at=token_expires_at,
        )

        async with self._lock:
            if relationship_id not in self._connections:
                self._connections[relationship_id] = []
            self._connections[relationship_id].append(state)

        logger.info(
            "WebSocket connected: relationship=%s, connections=%d",
            relationship_id,
            len(self._connections.get(relationship_id, [])),
        )
        return state

    async def disconnect(self, state: ConnectionState) -> None:
        """Unregister a connection."""
        async with self._lock:
            conns = self._connections.get(state.relationship_id, [])
            if state in conns:
                conns.remove(state)
            if not conns:
                self._connections.pop(state.relationship_id, None)

        logger.info(
            "WebSocket disconnected: relationship=%s, remaining=%d",
            state.relationship_id,
            len(self._connections.get(state.relationship_id, [])),
        )

    async def get_connections(self, relationship_id: UUID) -> list[ConnectionState]:
        """Get all active connections for a relationship."""
        async with self._lock:
            return list(self._connections.get(relationship_id, []))

    async def broadcast_to_relationship(
        self,
        relationship_id: UUID,
        event_type: str,
        data: dict[str, Any],
        *,
        seq: int | None = None,
        turn_id: str | None = None,
    ) -> int:
        """Broadcast an event to all connections for a relationship.

        Returns the number of connections that received the event.
        """
        connections = await self.get_connections(relationship_id)
        if not connections:
            return 0

        event = _format_ws_event(event_type, data, seq=seq, turn_id=turn_id)
        sent = 0

        for conn in connections:
            try:
                await conn.websocket.send_text(event)
                sent += 1
            except Exception as e:
                logger.warning("Failed to broadcast to connection: %s", e)

        return sent

    async def send_proactive_message(
        self,
        relationship_id: UUID,
        message_id: UUID,
        content: str,
        seq: int | None = None,
        source_behavior_key: str | None = None,
    ) -> int:
        """Send a proactive message to all connections for a relationship.

        Returns the number of connections that received the message.
        This is used by PostTurnExecutor to deliver proactive messages.
        """
        connections = await self.get_connections(relationship_id)
        if not connections:
            return 0

        event = _format_ws_event(
            "proactive",
            {
                "id": str(message_id),
                "content": content,
                "source_behavior_key": source_behavior_key,
            },
            seq=seq,
        )
        sent = 0

        for conn in connections:
            try:
                await conn.websocket.send_text(event)
                sent += 1
            except Exception as e:
                logger.warning("Failed to send proactive message to connection: %s", e)

        logger.info(
            "Proactive message sent: message_id=%s, relationship=%s, sent_to=%d connections",
            message_id,
            relationship_id,
            sent,
        )
        return sent

    async def graceful_shutdown(self) -> None:
        """Close all connections gracefully (for server shutdown)."""
        async with self._lock:
            all_connections = [conn for conns in self._connections.values() for conn in conns]

        for conn in all_connections:
            try:
                await conn.websocket.close(WS_CLOSE_SERVICE_RESTART, "Server restarting")
            except Exception as e:
                logger.warning("Error closing connection during shutdown: %s", e)

        async with self._lock:
            self._connections.clear()

        logger.info("All WebSocket connections closed for shutdown")


# Global connection manager instance
connection_manager = ConnectionManager()


# -----------------------------------------------------------------------------
# JWT Token Helpers
# -----------------------------------------------------------------------------


def _create_ws_token(
    relationship_id: UUID,
    api_key_id: UUID,
    expires_in: int = WS_TOKEN_EXPIRY_SECONDS,
) -> tuple[str, datetime]:
    """Create a short-lived JWT for WebSocket auth."""
    now = datetime.now(UTC)
    expires_at = datetime.fromtimestamp(now.timestamp() + expires_in, tz=UTC)

    payload = {
        "relationship_id": str(relationship_id),
        "api_key_id": str(api_key_id),
        "exp": int(expires_at.timestamp()),
        "iat": int(now.timestamp()),
    }

    token = jwt.encode(payload, WS_TOKEN_SECRET, algorithm="HS256")
    return token, expires_at


def _verify_ws_token(token: str, expected_relationship_id: UUID) -> tuple[UUID, UUID, datetime]:
    """Verify a WebSocket JWT and extract claims.

    Returns (relationship_id, api_key_id, expires_at)
    Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError on failure.
    """
    payload = jwt.decode(token, WS_TOKEN_SECRET, algorithms=["HS256"])

    relationship_id = UUID(payload["relationship_id"])
    api_key_id = UUID(payload["api_key_id"])
    exp_timestamp = payload["exp"]
    expires_at = datetime.fromtimestamp(exp_timestamp, tz=UTC)

    # Verify scope
    if relationship_id != expected_relationship_id:
        raise jwt.InvalidTokenError("Token scope mismatch")

    return relationship_id, api_key_id, expires_at


def _extract_ws_token(websocket: WebSocket, token_query: str | None) -> str | None:
    """Extract JWT token from Authorization header or query parameter.

    Priority:
    1. Authorization header (Bearer token) - preferred for native/mobile clients
    2. Query parameter - fallback for browser clients

    Returns the token string or None if not found.
    """
    # Check Authorization header first (preferred for mobile/native clients)
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        if token:
            return token

    # Fall back to query parameter (for browser clients)
    if token_query:
        return token_query

    return None


# -----------------------------------------------------------------------------
# Event Formatting
# -----------------------------------------------------------------------------


def _format_ws_event(
    event_type: str,
    data: dict[str, Any],
    *,
    seq: int | None = None,
    turn_id: str | None = None,
) -> str:
    """Format a WebSocket event with unified protocol."""
    event_payload = {
        "seq": seq,
        "timestamp": datetime.now(UTC).isoformat(),
        "turn_id": turn_id,
        "type": event_type,
        "data": data,
    }
    return json.dumps(event_payload)


# -----------------------------------------------------------------------------
# WebSocket Event Emitter
# -----------------------------------------------------------------------------


class WebSocketEventEmitter(EventEmitter):
    """EventEmitter implementation for WebSocket transport."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket

    async def emit_ack(
        self,
        turn_id: str,
        message_id: UUID,
        seq: int,
        client_message_id: str | None = None,
    ) -> None:
        data = {"message_id": str(message_id), "turn_id": turn_id}
        if client_message_id:
            data["client_message_id"] = client_message_id
        await self.websocket.send_text(_format_ws_event("ack", data, seq=seq, turn_id=turn_id))

    async def emit_status(
        self,
        turn_id: str,
        stage: str,
        phase: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        data: dict[str, Any] = {"stage": stage, "phase": phase}
        if meta:
            data["meta"] = meta
        await self.websocket.send_text(_format_ws_event("status", data, turn_id=turn_id))

    async def emit_delta(self, turn_id: str, content: str) -> None:
        await self.websocket.send_text(
            _format_ws_event(
                "delta",
                {"content": content, "role": "assistant"},
                turn_id=turn_id,
            )
        )

    async def emit_message(
        self,
        turn_id: str,
        message_id: UUID,
        relationship_id: UUID,
        content: str,
        seq: int,
        build_ms: int,
    ) -> None:
        await self.websocket.send_text(
            _format_ws_event(
                "message",
                {
                    "id": str(message_id),
                    "relationship_id": str(relationship_id),
                    "role": "assistant",
                    "content": content,
                    "seq": seq,
                    "build_ms": build_ms,
                },
                seq=seq,
                turn_id=turn_id,
            )
        )

    async def emit_error(self, turn_id: str, code: str, message: str) -> None:
        await self.websocket.send_text(
            _format_ws_event(
                "error",
                {"code": code, "message": message},
                turn_id=turn_id,
            )
        )

    async def emit_trace(self, turn_id: str, event: ContextEvent) -> None:
        await self.websocket.send_text(
            _format_ws_event(
                "trace",
                {
                    "name": event.name,
                    "phase": event.phase,
                    "ts_ms": event.ts_ms,
                    "meta": event.meta,
                },
                turn_id=turn_id,
            )
        )


async def _replay_events(
    conn: asyncpg.Connection,
    websocket: WebSocket,
    relationship_id: UUID,
    since_seq: int,
) -> int:
    """Replay durable events since a given sequence number.

    Returns the highest sequence number replayed.
    """
    # Query messages with seq > since_seq (durable events only)
    rows = await conn.fetch(
        """
        SELECT id, relationship_id, role, content, seq, session_id,
               is_proactive, delivery_status, created_at, build_ms
        FROM messages
        WHERE relationship_id = $1 AND seq > $2
        ORDER BY seq ASC
        """,
        relationship_id,
        since_seq,
    )

    max_seq = since_seq
    proactive_ids_to_update = []

    for row in rows:
        msg = dict(row)
        event_type = "proactive" if msg.get("is_proactive") else "message"
        event = _format_ws_event(
            event_type,
            {
                "id": str(msg["id"]),
                "relationship_id": str(msg["relationship_id"]),
                "role": msg["role"],
                "content": msg["content"],
                "seq": msg["seq"],
                "created_at": msg["created_at"].isoformat(),
            },
            seq=msg["seq"],
        )
        await websocket.send_text(event)
        max_seq = max(max_seq, msg["seq"] or 0)

        # Track proactive messages that need status update
        if msg.get("is_proactive") and msg.get("delivery_status") == "pending":
            proactive_ids_to_update.append(msg["id"])

    # Update delivery status for proactive messages sent via replay
    if proactive_ids_to_update:
        await conn.execute(
            """
            UPDATE messages
            SET delivery_status = 'delivered'
            WHERE id = ANY($1)
            """,
            proactive_ids_to_update,
        )
        logger.info(
            "Updated %d proactive messages to delivered via replay",
            len(proactive_ids_to_update),
        )

    logger.info(
        "Replayed %d events for relationship=%s since_seq=%d",
        len(rows),
        relationship_id,
        since_seq,
    )
    return max_seq


async def _maybe_send_intro_message(state: ConnectionState, companion: Any) -> bool:
    """Send companion intro message on connect when configured and eligible."""
    companion_config = getattr(companion, "config", None)
    intro_cfg = getattr(companion_config, "intro_message", None) if companion_config else None
    if not intro_cfg or not bool(getattr(intro_cfg, "enabled", False)):
        return False

    intro_text = str(getattr(intro_cfg, "text", "") or "").strip()
    if not intro_text:
        intro_text = DEFAULT_INTRO_MESSAGE_TEXT

    send_once = bool(getattr(intro_cfg, "send_once_per_relationship", True))

    message_row: asyncpg.Record | None = None
    seq: int | None = None

    async with get_db_connection() as conn, conn.transaction():
        if send_once:
            gate_passed = await conn.fetchval(
                """
                    UPDATE relationships r
                    SET metadata = jsonb_set(
                        COALESCE(r.metadata, '{}'::jsonb),
                        ARRAY[$2::text],
                        to_jsonb(now()),
                        true
                    )
                    WHERE r.id = $1
                      AND NOT (COALESCE(r.metadata, '{}'::jsonb) ? $2)
                      AND NOT EXISTS (
                          SELECT 1
                          FROM messages m
                          WHERE m.relationship_id = r.id
                      )
                    RETURNING 1
                    """,
                state.relationship_id,
                INTRO_MESSAGE_SENT_AT_KEY,
            )
            if not gate_passed:
                return False
        else:
            has_user_messages = await conn.fetchval(
                """
                    SELECT 1
                    FROM messages
                    WHERE relationship_id = $1
                    LIMIT 1
                    """,
                state.relationship_id,
            )
            if has_user_messages:
                return False

        seq_row = await conn.fetchrow(
            "SELECT next_relationship_message_seq($1) as seq",
            state.relationship_id,
        )
        seq = seq_row["seq"] if seq_row else None

        message_row = await conn.fetchrow(
            """
                INSERT INTO messages (
                    id,
                    relationship_id,
                    role,
                    content,
                    seq,
                    is_proactive,
                    input_modality
                )
                VALUES ($1, $2, 'assistant', $3, $4, FALSE, 'text')
                RETURNING id, created_at
                """,
            uuid4(),
            state.relationship_id,
            intro_text,
            seq,
        )

    if not message_row:
        return False

    await state.websocket.send_text(
        _format_ws_event(
            "message",
            {
                "id": str(message_row["id"]),
                "relationship_id": str(state.relationship_id),
                "role": "assistant",
                "content": intro_text,
                "seq": seq,
                "created_at": message_row["created_at"].isoformat(),
            },
            seq=seq,
        )
    )
    return True


async def _process_ws_message(
    state: ConnectionState,
    relationship: Relationship,
    companion: Any,
    client_message_id: str,
    content: str,
    session_id: UUID | None,
    config: TurnConfig | None,
) -> None:
    """Process a user message received via WebSocket.

    Uses the shared TurnProcessor for core message processing logic.
    """
    emitter = WebSocketEventEmitter(state.websocket)
    turn_id = ""  # Will be set by processor

    try:
        async with get_db_connection() as conn:
            # Convert TurnConfig to ProcessorTurnConfig
            processor_config = None
            if config:
                processor_config = ProcessorTurnConfig(
                    model=config.model,
                    temperature=config.temperature,
                )

            processor = TurnProcessor(
                conn=conn,
                companion=companion,
                relationship=relationship,
                emitter=emitter,
                debug_mode=state.debug_mode,
            )
            turn_id = processor.state.turn_id

            turn_input = TurnInput(
                content=content,
                session_id=session_id,
                config=processor_config,
                client_message_id=client_message_id,
            )

            await processor.process_turn_streaming(turn_input)

    except asyncio.CancelledError:
        await state.websocket.send_text(
            _format_ws_event("cancelled", {"turn_id": turn_id}, turn_id=turn_id)
        )
        raise
    except Exception as e:
        logger.exception("Error processing WebSocket message")
        await state.websocket.send_text(
            _format_ws_event(
                "error",
                {"code": "internal_error", "message": str(e)},
                turn_id=turn_id,
            )
        )


# -----------------------------------------------------------------------------
# Heartbeat and Timeout Tasks
# -----------------------------------------------------------------------------


async def _deliver_pending_proactive_messages(state: ConnectionState) -> int:
    """Check for and deliver pending proactive messages.

    This is called from the heartbeat loop to deliver messages created by async
    behaviors (Modal) that couldn't access the WebSocket connection directly.

    Returns the number of messages delivered.
    """
    ws = state.websocket
    delivered_count = 0

    try:
        async with get_db_connection() as conn:
            # Fetch pending proactive messages for this relationship
            rows = await conn.fetch(
                """
                SELECT id, content, seq, source_behavior_key, created_at
                FROM messages
                WHERE relationship_id = $1
                  AND is_proactive = TRUE
                  AND delivery_status = 'pending'
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY seq ASC
                LIMIT 10
                """,
                state.relationship_id,
            )

            for row in rows:
                message_id = row["id"]
                try:
                    # Update delivery status FIRST (before sending)
                    # This ensures we don't re-send if WS fails after DB update
                    update_result = await conn.execute(
                        """
                        UPDATE messages
                        SET delivery_status = 'delivered'
                        WHERE id = $1
                        """,
                        message_id,
                    )
                    logger.info(
                        "Updated proactive message status: id=%s, result=%s",
                        message_id,
                        update_result,
                    )

                    # Send proactive message via WebSocket
                    event = _format_ws_event(
                        "proactive",
                        {
                            "message_id": str(message_id),
                            "content": row["content"],
                            "source_behavior_key": row["source_behavior_key"],
                            "created_at": row["created_at"].isoformat()
                            if row["created_at"]
                            else None,
                        },
                        seq=row["seq"],
                    )
                    await ws.send_text(event)
                    delivered_count += 1
                    logger.info(
                        "Delivered pending proactive message: id=%s, relationship=%s",
                        message_id,
                        state.relationship_id,
                    )

                except Exception as e:
                    logger.exception(
                        "Failed to deliver proactive message %s: %s",
                        message_id,
                        e,
                    )
                    break  # Stop on first failure to maintain order

    except Exception as e:
        logger.warning("Error checking for pending proactive messages: %s", e)

    return delivered_count


async def _heartbeat_loop(state: ConnectionState) -> None:
    """Send heartbeats, check for idle/token expiry, and deliver pending messages."""
    ws = state.websocket

    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

        now = datetime.now(UTC)

        # Check idle timeout
        idle_seconds = (now - state.last_activity_at).total_seconds()
        if idle_seconds > IDLE_TIMEOUT_SECONDS:
            logger.info(
                "Closing idle connection: relationship=%s, idle=%.0fs",
                state.relationship_id,
                idle_seconds,
            )
            await ws.close(WS_CLOSE_NORMAL, "Idle timeout")
            return

        # Check max connection duration
        duration = (now - state.connected_at).total_seconds()
        if duration > MAX_CONNECTION_DURATION_SECONDS:
            logger.info(
                "Closing long-lived connection: relationship=%s, duration=%.0fs",
                state.relationship_id,
                duration,
            )
            await ws.close(WS_CLOSE_NORMAL, "Max connection duration exceeded")
            return

        # Check token expiry warning
        time_until_expiry = (state.token_expires_at - now).total_seconds()
        if time_until_expiry <= TOKEN_EXPIRING_WARNING_SECONDS and not state.token_expiring_warned:
            try:
                await ws.send_text(
                    _format_ws_event(
                        "token_expiring",
                        {"expires_in_seconds": int(time_until_expiry)},
                    )
                )
                state.token_expiring_warned = True
            except Exception:
                pass

        # Check token expired
        if time_until_expiry <= 0:
            logger.info(
                "Closing connection with expired token: relationship=%s",
                state.relationship_id,
            )
            await ws.close(WS_CLOSE_TOKEN_EXPIRED, "Token expired")
            return

        # Deliver any pending proactive messages from async behaviors
        try:
            delivered = await _deliver_pending_proactive_messages(state)
            if delivered > 0:
                logger.debug(
                    "Delivered %d pending proactive messages: relationship=%s",
                    delivered,
                    state.relationship_id,
                )
        except Exception as e:
            logger.warning("Error delivering pending messages: %s", e)

        # Send heartbeat
        try:
            await ws.send_text(_format_ws_event("heartbeat", {}))
        except Exception as e:
            logger.warning("Failed to send heartbeat: %s", e)
            return


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------


@router.post(
    "/companions/{companion_id}/relationships/{user_id}/ws-token",
    response_model=WsTokenResponse,
    summary="Get WebSocket token",
    description="Exchange API key for a short-lived JWT for WebSocket connection.",
)
async def create_ws_token(
    companion_id: UUID,
    user_id: str,
    subject: ProjectApiKeySubject = Depends(get_project_api_subject),
    conn: asyncpg.Connection = Depends(get_db),
) -> WsTokenResponse:
    """Create a WebSocket authentication token."""
    # Verify companion access
    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
    if not companion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Companion {companion_id} not found",
        )
    if companion.project_id != subject.project.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Companion does not belong to this project",
        )

    # Ensure relationship exists
    relationship, created = await RelationshipRepository.ensure_exists(
        conn,
        companion_id=companion_id,
        user_id=user_id,
    )

    # Create token
    token, _expires_at = _create_ws_token(
        relationship_id=relationship.id,
        api_key_id=subject.api_key.id,
    )

    logger.info(
        "Created WS token: relationship=%s (%s)",
        relationship.id,
        "created" if created else "existing",
    )

    return WsTokenResponse(
        token=token,
        relationship_id=relationship.id,
        expires_in=WS_TOKEN_EXPIRY_SECONDS,
    )


@router.websocket("/companions/{companion_id}/relationships/{user_id}/connect")
async def websocket_connect(
    websocket: WebSocket,
    companion_id: UUID,
    user_id: str,
    token: str | None = Query(default=None),
    since_seq: int | None = Query(default=None),
    debug: bool = Query(default=False),
):
    """WebSocket endpoint for real-time communication.

    Authentication:
        Token can be provided via:
        1. Authorization header: "Bearer <token>" (preferred for mobile/native clients)
        2. Query parameter: ?token=<token> (fallback for browser clients)

        If both are provided, the header takes precedence.
    """
    # Accept the connection first (needed to send close codes)
    await websocket.accept()

    # Extract token from header or query param
    extracted_token = _extract_ws_token(websocket, token)
    if not extracted_token:
        await websocket.close(WS_CLOSE_INVALID_TOKEN, "Missing authentication token")
        return

    # Verify token
    try:
        async with get_db_connection() as conn:
            # First get the relationship to verify it exists
            relationship = await RelationshipRepository.get_by_companion_and_user(
                conn, companion_id=companion_id, user_id=user_id
            )
            if not relationship:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Relationship not found")
                return

            # Verify token
            try:
                _rel_id, api_key_id, token_expires_at = _verify_ws_token(
                    extracted_token, relationship.id
                )
            except jwt.ExpiredSignatureError:
                await websocket.close(WS_CLOSE_TOKEN_EXPIRED, "Token expired")
                return
            except (jwt.InvalidTokenError, ValueError) as e:
                logger.warning("Invalid WS token: %s", e)
                await websocket.close(WS_CLOSE_INVALID_TOKEN, "Invalid token")
                return

            # Get companion
            companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
            if not companion:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Companion not found")
                return

    except Exception:
        logger.exception("Error during WebSocket auth")
        await websocket.close(WS_CLOSE_INTERNAL_ERROR, "Internal error")
        return

    # Register connection
    state = await connection_manager.connect(
        websocket=websocket,
        relationship_id=relationship.id,
        api_key_id=api_key_id,
        token_expires_at=token_expires_at,
    )
    state.debug_mode = debug

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(_heartbeat_loop(state))

    try:
        # Send connected event
        await websocket.send_text(
            _format_ws_event(
                "connected",
                {
                    "relationship_id": str(relationship.id),
                    "companion_id": str(companion_id),
                    "user_id": user_id,
                },
            )
        )

        # Optionally send configured first-message intro
        try:
            intro_sent = await _maybe_send_intro_message(state, companion)
            if intro_sent:
                logger.info("Intro message sent on connect: relationship=%s", relationship.id)
        except Exception as e:
            logger.warning("Failed to send intro message on connect: %s", e)

        # Deliver any pending proactive messages immediately at connection
        try:
            delivered = await _deliver_pending_proactive_messages(state)
            if delivered > 0:
                logger.info(
                    "Delivered %d pending proactive messages at connection: relationship=%s",
                    delivered,
                    relationship.id,
                )
        except Exception as e:
            logger.warning("Error delivering pending messages at connection: %s", e)

        # Replay events if requested
        if since_seq is not None:
            try:
                async with get_db_connection() as conn:
                    # Verify seq exists
                    check = await conn.fetchval(
                        "SELECT 1 FROM messages WHERE relationship_id = $1 AND seq = $2",
                        relationship.id,
                        since_seq,
                    )
                    if since_seq > 0 and not check:
                        await websocket.send_text(
                            _format_ws_event(
                                "error",
                                {
                                    "code": "invalid_seq",
                                    "message": "Sequence not found. Reconnect without since_seq.",
                                },
                            )
                        )
                    else:
                        await _replay_events(conn, websocket, relationship.id, since_seq)
            except Exception as e:
                logger.warning("Event replay failed: %s", e)

        # Main message loop
        while True:
            try:
                raw_data = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            state.last_activity_at = datetime.now(UTC)

            # Parse message
            try:
                msg = json.loads(raw_data)
                msg_type = msg.get("type")
            except json.JSONDecodeError:
                await websocket.send_text(
                    _format_ws_event(
                        "error",
                        {"code": "invalid_json", "message": "Invalid JSON"},
                    )
                )
                continue

            # Handle message types
            if msg_type == "ping":
                await websocket.send_text(_format_ws_event("pong", {}))

            elif msg_type == "user_message":
                # Validate message
                try:
                    client_message_id = msg["client_message_id"]
                    content = msg["content"]
                except KeyError as e:
                    await websocket.send_text(
                        _format_ws_event(
                            "error",
                            {"code": "invalid_message", "message": f"Missing field: {e}"},
                        )
                    )
                    continue

                session_id = msg.get("session_id")
                if session_id:
                    session_id = UUID(session_id)

                config_dict = msg.get("config")
                config = TurnConfig(**config_dict) if config_dict else None

                # Cancel pending turn if any
                if state.pending_turn and not state.pending_turn.done():
                    state.pending_turn.cancel()
                    try:
                        await state.pending_turn
                    except asyncio.CancelledError:
                        pass

                # Reload relationship to get current state
                async with get_db_connection() as conn:
                    relationship = await RelationshipRepository.get_by_id(conn, relationship.id)
                    companion = await CompanionRepository.get_companion_by_id_no_auth(
                        conn, companion_id
                    )

                # Process message
                state.pending_turn = asyncio.create_task(
                    _process_ws_message(
                        state=state,
                        relationship=relationship,
                        companion=companion,
                        client_message_id=client_message_id,
                        content=content,
                        session_id=session_id,
                        config=config,
                    )
                )
                try:
                    await state.pending_turn
                except asyncio.CancelledError:
                    pass

            elif msg_type == "cancel":
                if state.pending_turn and not state.pending_turn.done():
                    state.pending_turn.cancel()

            elif msg_type == "refresh_token":
                # Handle token refresh
                new_token = msg.get("token")
                if not new_token:
                    await websocket.send_text(
                        _format_ws_event(
                            "error",
                            {"code": "invalid_message", "message": "Missing token field"},
                        )
                    )
                    continue

                try:
                    _, _, new_expires_at = _verify_ws_token(new_token, relationship.id)
                    state.token_expires_at = new_expires_at
                    state.token_expiring_warned = False
                    await websocket.send_text(
                        _format_ws_event(
                            "status",
                            {"stage": "token_refreshed", "expires_at": new_expires_at.isoformat()},
                        )
                    )
                except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError) as e:
                    await websocket.send_text(
                        _format_ws_event(
                            "error",
                            {"code": "invalid_token", "message": str(e)},
                        )
                    )

            else:
                await websocket.send_text(
                    _format_ws_event(
                        "error",
                        {"code": "unknown_message_type", "message": f"Unknown type: {msg_type}"},
                    )
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.close(WS_CLOSE_INTERNAL_ERROR, str(e))
        except Exception:
            pass
    finally:
        # Cleanup
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        if state.pending_turn and not state.pending_turn.done():
            state.pending_turn.cancel()
            try:
                await state.pending_turn
            except asyncio.CancelledError:
                pass

        await connection_manager.disconnect(state)


@router.websocket("/relationships/{relationship_id}/connect")
async def websocket_connect_direct(
    websocket: WebSocket,
    relationship_id: UUID,
    token: str | None = Query(default=None),
    since_seq: int | None = Query(default=None),
    debug: bool = Query(default=False),
):
    """WebSocket endpoint using relationship ID directly.

    Authentication:
        Token can be provided via:
        1. Authorization header: "Bearer <token>" (preferred for mobile/native clients)
        2. Query parameter: ?token=<token> (fallback for browser clients)

        If both are provided, the header takes precedence.
    """
    await websocket.accept()

    # Extract token from header or query param
    extracted_token = _extract_ws_token(websocket, token)
    if not extracted_token:
        await websocket.close(WS_CLOSE_INVALID_TOKEN, "Missing authentication token")
        return

    try:
        async with get_db_connection() as conn:
            relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
            if not relationship:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Relationship not found")
                return

            try:
                _rel_id, api_key_id, token_expires_at = _verify_ws_token(
                    extracted_token, relationship_id
                )
            except jwt.ExpiredSignatureError:
                await websocket.close(WS_CLOSE_TOKEN_EXPIRED, "Token expired")
                return
            except (jwt.InvalidTokenError, ValueError) as e:
                logger.warning("Invalid WS token: %s", e)
                await websocket.close(WS_CLOSE_INVALID_TOKEN, "Invalid token")
                return

            companion = await CompanionRepository.get_companion_by_id_no_auth(
                conn, relationship.companion_id
            )
            if not companion:
                await websocket.close(WS_CLOSE_NOT_FOUND, "Companion not found")
                return

    except Exception:
        logger.exception("Error during WebSocket auth")
        await websocket.close(WS_CLOSE_INTERNAL_ERROR, "Internal error")
        return

    # Register connection
    state = await connection_manager.connect(
        websocket=websocket,
        relationship_id=relationship_id,
        api_key_id=api_key_id,
        token_expires_at=token_expires_at,
    )
    state.debug_mode = debug

    heartbeat_task = asyncio.create_task(_heartbeat_loop(state))

    try:
        await websocket.send_text(
            _format_ws_event(
                "connected",
                {
                    "relationship_id": str(relationship_id),
                    "companion_id": str(relationship.companion_id),
                    "user_id": relationship.user_id,
                },
            )
        )

        # Optionally send configured first-message intro
        try:
            intro_sent = await _maybe_send_intro_message(state, companion)
            if intro_sent:
                logger.info("Intro message sent on connect: relationship=%s", relationship_id)
        except Exception as e:
            logger.warning("Failed to send intro message on connect: %s", e)

        # Deliver any pending proactive messages immediately at connection
        try:
            delivered = await _deliver_pending_proactive_messages(state)
            if delivered > 0:
                logger.info(
                    "Delivered %d pending proactive messages at connection: relationship=%s",
                    delivered,
                    relationship_id,
                )
        except Exception as e:
            logger.warning("Error delivering pending messages at connection: %s", e)

        if since_seq is not None:
            try:
                async with get_db_connection() as conn:
                    check = await conn.fetchval(
                        "SELECT 1 FROM messages WHERE relationship_id = $1 AND seq = $2",
                        relationship_id,
                        since_seq,
                    )
                    if since_seq > 0 and not check:
                        await websocket.send_text(
                            _format_ws_event(
                                "error",
                                {
                                    "code": "invalid_seq",
                                    "message": "Sequence not found. Reconnect without since_seq.",
                                },
                            )
                        )
                    else:
                        await _replay_events(conn, websocket, relationship_id, since_seq)
            except Exception as e:
                logger.warning("Event replay failed: %s", e)

        while True:
            try:
                raw_data = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            state.last_activity_at = datetime.now(UTC)

            try:
                msg = json.loads(raw_data)
                msg_type = msg.get("type")
            except json.JSONDecodeError:
                await websocket.send_text(
                    _format_ws_event(
                        "error",
                        {"code": "invalid_json", "message": "Invalid JSON"},
                    )
                )
                continue

            if msg_type == "ping":
                await websocket.send_text(_format_ws_event("pong", {}))

            elif msg_type == "user_message":
                try:
                    client_message_id = msg["client_message_id"]
                    content = msg["content"]
                except KeyError as e:
                    await websocket.send_text(
                        _format_ws_event(
                            "error",
                            {"code": "invalid_message", "message": f"Missing field: {e}"},
                        )
                    )
                    continue

                session_id = msg.get("session_id")
                if session_id:
                    session_id = UUID(session_id)

                config_dict = msg.get("config")
                config = TurnConfig(**config_dict) if config_dict else None

                if state.pending_turn and not state.pending_turn.done():
                    state.pending_turn.cancel()
                    try:
                        await state.pending_turn
                    except asyncio.CancelledError:
                        pass

                async with get_db_connection() as conn:
                    relationship = await RelationshipRepository.get_by_id(conn, relationship_id)
                    companion = await CompanionRepository.get_companion_by_id_no_auth(
                        conn, relationship.companion_id
                    )

                state.pending_turn = asyncio.create_task(
                    _process_ws_message(
                        state=state,
                        relationship=relationship,
                        companion=companion,
                        client_message_id=client_message_id,
                        content=content,
                        session_id=session_id,
                        config=config,
                    )
                )
                try:
                    await state.pending_turn
                except asyncio.CancelledError:
                    pass

            elif msg_type == "cancel":
                if state.pending_turn and not state.pending_turn.done():
                    state.pending_turn.cancel()

            elif msg_type == "refresh_token":
                new_token = msg.get("token")
                if not new_token:
                    await websocket.send_text(
                        _format_ws_event(
                            "error",
                            {"code": "invalid_message", "message": "Missing token field"},
                        )
                    )
                    continue

                try:
                    _, _, new_expires_at = _verify_ws_token(new_token, relationship_id)
                    state.token_expires_at = new_expires_at
                    state.token_expiring_warned = False
                    await websocket.send_text(
                        _format_ws_event(
                            "status",
                            {"stage": "token_refreshed", "expires_at": new_expires_at.isoformat()},
                        )
                    )
                except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError) as e:
                    await websocket.send_text(
                        _format_ws_event(
                            "error",
                            {"code": "invalid_token", "message": str(e)},
                        )
                    )

            else:
                await websocket.send_text(
                    _format_ws_event(
                        "error",
                        {"code": "unknown_message_type", "message": f"Unknown type: {msg_type}"},
                    )
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket error")
        try:
            await websocket.close(WS_CLOSE_INTERNAL_ERROR, str(e))
        except Exception:
            pass
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        if state.pending_turn and not state.pending_turn.done():
            state.pending_turn.cancel()
            try:
                await state.pending_turn
            except asyncio.CancelledError:
                pass

        await connection_manager.disconnect(state)
