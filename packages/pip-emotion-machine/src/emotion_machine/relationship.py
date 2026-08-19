"""Relationship handle for interacting with a user-companion pair."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from .streaming import parse_sse_async
from .websocket import VoiceConnection, WebSocketConnection

if TYPE_CHECKING:
    import httpx


class Session:
    """Handle for an active session within a relationship."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        session_id: str,
        relationship_id: str,
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url
        self._headers = headers
        self.id = session_id
        self.relationship_id = relationship_id

    async def send(
        self,
        message: str,
        *,
        config: dict[str, Any] | None = None,
        image_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a message within this session."""
        from .exceptions import APIError

        payload: dict[str, Any] = {"content": message, "session_id": self.id}
        if config:
            payload["config"] = config
        if image_ids:
            payload["image_ids"] = image_ids

        response = await self._http_client.post(
            f"{self._base_url}/v2/relationships/{self.relationship_id}/messages",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def stream(
        self,
        message: str,
        *,
        config: dict[str, Any] | None = None,
        image_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a response within this session."""
        from .exceptions import APIError

        payload: dict[str, Any] = {"content": message, "session_id": self.id}
        if config:
            payload["config"] = config
        if image_ids:
            payload["image_ids"] = image_ids

        headers = {**self._headers, "Accept": "text/event-stream"}

        async with self._http_client.stream(
            "POST",
            f"{self._base_url}/v2/relationships/{self.relationship_id}/messages",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise APIError(
                    body.decode(),
                    status_code=response.status_code,
                )

            async def line_iterator() -> AsyncIterator[str]:
                async for line in response.aiter_lines():
                    yield line

            async for event in parse_sse_async(line_iterator()):
                yield event

    async def end(self) -> dict[str, Any]:
        """End the session and get summary."""
        from .exceptions import APIError

        response = await self._http_client.post(
            f"{self._base_url}/v2/sessions/{self.id}/end",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    async def state_patch(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Merge changes into session state.

        Only works on active, non-isolated sessions.

        Args:
            changes: State changes to merge

        Returns:
            Updated session dict
        """
        from .exceptions import APIError

        response = await self._http_client.patch(
            f"{self._base_url}/v2/sessions/{self.id}/state",
            headers=self._headers,
            json={"changes": changes},
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    @staticmethod
    def _safe_json(response) -> dict[str, Any]:
        """Safely parse JSON from response."""
        try:
            return response.json() if response.text else {}
        except Exception:
            return {"raw": response.text}


class Relationship:
    """Handle for interacting with a user-companion relationship.

    This is a lightweight handle that doesn't make network calls on creation.
    All methods are async and make API calls when invoked.

    Usage:
        rel = em.relationship(companion_id, user_id)

        # Simple send
        response = await rel.send("Hello!")

        # Streaming
        async for chunk in rel.stream("Tell me a story"):
            print(chunk["data"]["content"], end="")

        # WebSocket
        async with rel.connect() as ws:
            await ws.send("Hello!")
            async for event in ws:
                handle(event)

        # Voice
        async with rel.voice() as voice:
            async for event in voice:
                handle(event)
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        companion_id: str,
        user_id: str,
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url
        self._headers = headers
        self.companion_id = companion_id
        self.user_id = user_id
        self._relationship_id: str | None = None

    def _composite_url(self) -> str:
        """URL for composite endpoints (creates relationship if needed)."""
        return f"{self._base_url}/v2/companions/{self.companion_id}/relationships/{self.user_id}"

    def _relationship_url(self, relationship_id: str) -> str:
        """URL for direct relationship endpoints."""
        return f"{self._base_url}/v2/relationships/{relationship_id}"

    async def ensure(self) -> str:
        """Ensure relationship exists and return its ID."""
        from .exceptions import APIError

        response = await self._http_client.put(
            f"{self._composite_url()}",
            headers=self._headers,
            json={},
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        data = response.json()
        self._relationship_id = data["id"]
        return self._relationship_id

    async def get(self) -> dict[str, Any]:
        """Get the full relationship object.

        Returns:
            Relationship dict with all fields
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    @staticmethod
    def _safe_json(response) -> dict[str, Any]:
        """Safely parse JSON from response."""
        try:
            return response.json() if response.text else {}
        except Exception:
            return {"raw": response.text}

    async def send(
        self,
        message: str,
        *,
        config: dict[str, Any] | None = None,
        image_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a message and get a response.

        Uses the composite endpoint which creates the relationship if needed.

        Args:
            message: The message content
            config: Optional message config (temperature, model, etc.)
            image_ids: Optional list of image IDs to include

        Returns:
            Response dict with keys: relationship_id, message, trace (optional)
        """
        from .exceptions import APIError

        payload: dict[str, Any] = {"content": message}
        if config:
            payload["config"] = config
        if image_ids:
            payload["image_ids"] = image_ids

        response = await self._http_client.post(
            f"{self._composite_url()}/messages",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        data = response.json()
        self._relationship_id = data.get("relationship_id")
        return data

    async def stream(
        self,
        message: str,
        *,
        config: dict[str, Any] | None = None,
        image_ids: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a response.

        Uses the composite endpoint which creates the relationship if needed.

        Args:
            message: The message content
            config: Optional message config
            image_ids: Optional list of image IDs

        Yields:
            SSE events with keys: event, id, data
            Common event types: ack, status, delta, message, error
        """
        from .exceptions import APIError

        payload: dict[str, Any] = {"content": message}
        if config:
            payload["config"] = config
        if image_ids:
            payload["image_ids"] = image_ids

        headers = {**self._headers, "Accept": "text/event-stream"}

        async with self._http_client.stream(
            "POST",
            f"{self._composite_url()}/messages",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise APIError(
                    body.decode(),
                    status_code=response.status_code,
                )

            async def line_iterator() -> AsyncIterator[str]:
                async for line in response.aiter_lines():
                    yield line

            async for event in parse_sse_async(line_iterator()):
                # Track relationship_id from events
                if isinstance(event.get("data"), dict):
                    if rid := event["data"].get("relationship_id"):
                        self._relationship_id = rid
                yield event

    def connect(
        self,
        *,
        since_seq: int | None = None,
        reconnect: bool = True,
        use_header_auth: bool = True,
    ) -> WebSocketConnection:
        """Create a WebSocket connection for real-time messaging.

        Args:
            since_seq: Replay events since this sequence number
            reconnect: Whether to auto-reconnect on disconnect
            use_header_auth: If True (default), send JWT in Authorization header.
                           If False, send JWT as query parameter (for browser clients).

        Returns:
            WebSocketConnection context manager

        Usage:
            async with rel.connect() as ws:
                await ws.send("Hello!")
                async for event in ws:
                    if event["type"] == "delta":
                        print(event["data"]["content"], end="")
                    elif event["type"] == "proactive":
                        print(f"[Companion]: {event['data']['content']}")
        """
        # Determine WebSocket URL (replace http with ws)
        ws_base = self._base_url.replace("http://", "ws://").replace(
            "https://", "wss://"
        )

        return WebSocketConnection(
            http_client=self._http_client,
            token_url=f"{self._composite_url()}/ws-token",
            connect_url=f"{ws_base}/v2/companions/{self.companion_id}/relationships/{self.user_id}/connect",
            headers=self._headers,
            since_seq=since_seq,
            reconnect=reconnect,
            use_header_auth=use_header_auth,
        )

    def voice(
        self,
        *,
        config: dict[str, Any] | None = None,
        use_header_auth: bool = True,
    ) -> VoiceConnection:
        """Create a voice WebSocket connection.

        Args:
            config: Voice configuration (pipeline_type, voice_name, etc.)
            use_header_auth: If True (default), send JWT in Authorization header.
                           If False, send JWT as query parameter (for browser clients).

        Returns:
            VoiceConnection context manager

        Usage:
            async with rel.voice(config={"voice_name": "alloy"}) as voice:
                async for event in voice:
                    handle(event)
        """
        return VoiceConnection(
            http_client=self._http_client,
            token_url=f"{self._composite_url()}/voice/token",
            headers=self._headers,
            config=config,
            use_header_auth=use_header_auth,
        )

    # =========================================================================
    # Profile methods
    # =========================================================================

    async def profile_get(self) -> dict[str, Any]:
        """Get the relationship profile."""
        from .exceptions import APIError

        # Ensure we have a relationship ID
        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}/profile",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json().get("profile", {})

    async def profile_set(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Set the relationship profile (replaces entirely)."""
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.put(
            f"{self._relationship_url(self._relationship_id)}/profile",
            headers=self._headers,
            json=profile,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def profile_patch(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Patch the relationship profile (merges changes)."""
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.patch(
            f"{self._relationship_url(self._relationship_id)}/profile",
            headers=self._headers,
            json=changes,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    async def profile_clear(self) -> None:
        """Clear the relationship profile."""
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.delete(
            f"{self._relationship_url(self._relationship_id)}/profile",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

    # =========================================================================
    # Config methods
    # =========================================================================

    async def config_patch(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Patch the relationship config.

        Common config options:
            - include_profile_in_prompt: bool - inject profile into system prompt
            - context_mode: "legacy" | "layered" - context engine mode

        Args:
            changes: Config changes to merge

        Returns:
            Updated config response
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.patch(
            f"{self._relationship_url(self._relationship_id)}/config",
            headers=self._headers,
            json=changes,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    async def config_get(self) -> dict[str, Any]:
        """Get the relationship config.

        Returns:
            Config response with config dict and version
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}/config",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    async def config_resolved(self) -> dict[str, Any]:
        """Get the fully resolved config (companion + relationship merged).

        Returns:
            Dict with:
                - config: The merged config
                - companion_config: Base companion config
                - relationship_overrides: Relationship-level overrides
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}/config/resolved",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    # =========================================================================
    # Session methods
    # =========================================================================

    async def session_start(
        self,
        *,
        type: str | None = None,
        isolated: bool = False,
    ) -> Session:
        """Start a new session.

        Args:
            type: Optional session type (e.g., "coaching")
            isolated: If True, session is isolated from relationship context

        Returns:
            Session handle for sending messages within the session
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        payload: dict[str, Any] = {}
        if type:
            payload["type"] = type
        if isolated:
            payload["isolated"] = isolated

        response = await self._http_client.post(
            f"{self._relationship_url(self._relationship_id)}/sessions",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        data = response.json()
        return Session(
            http_client=self._http_client,
            base_url=self._base_url,
            headers=self._headers,
            session_id=data["id"],
            relationship_id=self._relationship_id,
        )

    async def session_list(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List sessions for this relationship.

        Args:
            limit: Max sessions to return (1-100)
            cursor: Pagination cursor

        Returns:
            Dict with sessions[], next_cursor, total
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}/sessions",
            headers=self._headers,
            params=params,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    async def session_get(self, session_id: str) -> dict[str, Any]:
        """Get a specific session by ID.

        Args:
            session_id: The session ID

        Returns:
            Session dict
        """
        from .exceptions import APIError

        response = await self._http_client.get(
            f"{self._base_url}/v2/sessions/{session_id}",
            headers=self._headers,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    async def session_active(self) -> dict[str, Any] | None:
        """Get the active session for this relationship, if any.

        Returns:
            Active session dict or None
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}/sessions/active",
            headers=self._headers,
        )

        if response.status_code == 404:
            return None

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json()

    # =========================================================================
    # Inbox methods
    # =========================================================================

    async def inbox_check(
        self,
        *,
        limit: int = 50,
        include_delivered: bool = False,
    ) -> list[dict[str, Any]]:
        """Check inbox for proactive messages.

        Proactive messages are created by behaviors (via ctx.send_message()) when
        there's no active WebSocket connection. They queue up in the inbox until
        retrieved and acknowledged.

        Message lifecycle:
        1. Behavior calls ctx.send_message("Hey!")
        2. If WebSocket connected: pushed immediately, status='delivered'
        3. If not connected: stored with status='pending'
        4. Client calls inbox_check() → marks as 'delivered' and returns messages
        5. Client calls inbox_ack() to mark as 'acknowledged'

        Note: Reading messages via inbox_check() automatically marks them as
        'delivered'. This prevents duplicate delivery if the user later connects
        via WebSocket. You should still call inbox_ack() after displaying messages
        to the user.

        Args:
            limit: Max messages to return (1-100, default 50)
            include_delivered: If True, also return messages already pushed via
                             WebSocket but not yet acknowledged

        Returns:
            List of message dicts with keys:
            - id: Message UUID
            - content: Message text
            - seq: Sequence number
            - source_behavior_key: Which behavior generated this (if any)
            - delivery_status: 'delivered' (marked on read)
            - expires_at: When message expires (typically 24h)
            - created_at: When message was created
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        params: dict[str, Any] = {"limit": limit}
        if include_delivered:
            params["include_delivered"] = "true"

        response = await self._http_client.get(
            f"{self._relationship_url(self._relationship_id)}/inbox",
            headers=self._headers,
            params=params,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=self._safe_json(response),
            )

        return response.json().get("messages", [])

    async def inbox_ack(self, message_ids: list[str]) -> dict[str, Any]:
        """Acknowledge inbox messages."""
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        response = await self._http_client.post(
            f"{self._relationship_url(self._relationship_id)}/inbox/ack",
            headers=self._headers,
            json={"message_ids": message_ids},
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()

    # =========================================================================
    # Behavior methods
    # =========================================================================

    async def behavior_trigger(
        self,
        behavior_key: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Manually trigger a behavior.

        Args:
            behavior_key: The behavior to trigger
            context: Optional context data for the behavior

        Returns:
            Dict with job_id for tracking the async execution
        """
        from .exceptions import APIError

        if not self._relationship_id:
            await self.ensure()

        payload: dict[str, Any] = {}
        if context:
            payload["context"] = context

        response = await self._http_client.post(
            f"{self._relationship_url(self._relationship_id)}/behaviors/{behavior_key}/trigger",
            headers=self._headers,
            json=payload,
        )

        if response.status_code >= 400:
            raise APIError(
                response.text,
                status_code=response.status_code,
                payload=response.json() if response.text else {},
            )

        return response.json()
