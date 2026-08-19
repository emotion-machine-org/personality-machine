"""WebSocket connection with auto-reconnect."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator

from .exceptions import ConnectionClosed, WebSocketError

if TYPE_CHECKING:
    import httpx
    import websockets

logger = logging.getLogger("emotion_machine.websocket")


class WebSocketConnection:
    """WebSocket connection with auto-reconnect and heartbeat support.

    Usage:
        async with rel.connect() as ws:
            await ws.send("Hello!")
            async for event in ws:
                handle(event)

    Authentication:
        By default, the JWT token is sent in the Authorization header (preferred
        for mobile/native clients). Set `use_header_auth=False` to use query
        parameter authentication instead (required for browser clients).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        token_url: str,
        connect_url: str,
        headers: dict[str, str],
        since_seq: int | None = None,
        reconnect: bool = True,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 30.0,
        heartbeat_interval: float = 30.0,
        use_header_auth: bool = True,
    ) -> None:
        self._http_client = http_client
        self._token_url = token_url
        self._connect_url = connect_url
        self._headers = headers
        self._since_seq = since_seq
        self._reconnect = reconnect
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._heartbeat_interval = heartbeat_interval
        self._use_header_auth = use_header_auth

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._token: str | None = None
        self._relationship_id: str | None = None
        self._closed = False
        self._last_seq: int | None = since_seq
        self._heartbeat_task: asyncio.Task | None = None
        self._current_delay = reconnect_delay

    async def _fetch_token(self) -> tuple[str, str]:
        """Fetch WebSocket token from the API."""
        response = await self._http_client.post(
            self._token_url,
            headers=self._headers,
        )
        if response.status_code >= 400:
            raise WebSocketError(
                f"Failed to fetch WebSocket token: {response.status_code}",
                code=response.status_code,
            )
        data = response.json()
        return data["token"], data["relationship_id"]

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        import websockets

        self._token, self._relationship_id = await self._fetch_token()

        # Build connection URL
        url = self._connect_url
        query_params = []

        if self._use_header_auth:
            # Use Authorization header (preferred for mobile/native clients)
            ws_headers = {"Authorization": f"Bearer {self._token}"}
        else:
            # Use query parameter (fallback for browser clients)
            ws_headers = None
            query_params.append(f"token={self._token}")

        if self._last_seq is not None:
            query_params.append(f"since_seq={self._last_seq}")

        if query_params:
            url += "?" + "&".join(query_params)

        logger.debug("Connecting to WebSocket: %s (header_auth=%s)", url, self._use_header_auth)
        self._ws = await websockets.connect(url, additional_headers=ws_headers)
        self._current_delay = self._reconnect_delay  # Reset delay on success

        # Start heartbeat
        self._start_heartbeat()

    def _start_heartbeat(self) -> None:
        """Start the heartbeat task."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats."""
        try:
            while self._ws and not self._closed:
                await asyncio.sleep(self._heartbeat_interval)
                if self._ws:
                    try:
                        await self._ws.ping()
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    async def _reconnect_loop(self) -> bool:
        """Attempt to reconnect with exponential backoff. Returns True if successful."""
        while self._reconnect and not self._closed:
            logger.info("Attempting to reconnect in %.1fs...", self._current_delay)
            await asyncio.sleep(self._current_delay)

            try:
                await self._connect()
                logger.info("Reconnected successfully")
                return True
            except Exception as e:
                logger.warning("Reconnection failed: %s", e)
                self._current_delay = min(
                    self._current_delay * 2, self._max_reconnect_delay
                )

        return False

    async def send(self, message: str) -> None:
        """Send a message through the WebSocket."""
        if not self._ws:
            raise WebSocketError("WebSocket not connected")

        payload = {"type": "message", "content": message}
        await self._ws.send(json.dumps(payload))

    async def send_raw(self, data: dict[str, Any]) -> None:
        """Send raw JSON data through the WebSocket."""
        if not self._ws:
            raise WebSocketError("WebSocket not connected")
        await self._ws.send(json.dumps(data))

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._receive_events()

    async def _receive_events(self) -> AsyncIterator[dict[str, Any]]:
        """Receive events from the WebSocket with auto-reconnect."""
        import websockets

        while not self._closed:
            if not self._ws:
                if not await self._reconnect_loop():
                    break
                continue

            try:
                message = await self._ws.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8")

                event = json.loads(message)

                # Track sequence number for reconnection
                if "seq" in event and event["seq"] is not None:
                    self._last_seq = event["seq"]

                yield event

            except websockets.ConnectionClosed as e:
                logger.warning("WebSocket closed: code=%s", e.code)
                self._ws = None

                if not self._reconnect or self._closed:
                    raise ConnectionClosed(
                        f"WebSocket connection closed: {e.code}", code=e.code
                    )

                # Try to reconnect
                if not await self._reconnect_loop():
                    raise ConnectionClosed(
                        "Failed to reconnect after connection closed"
                    )

            except Exception as e:
                if self._closed:
                    break
                logger.error("WebSocket error: %s", e)
                raise WebSocketError(str(e))

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self._closed = True

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def relationship_id(self) -> str | None:
        """The relationship ID for this connection."""
        return self._relationship_id

    @property
    def last_seq(self) -> int | None:
        """The last sequence number received."""
        return self._last_seq

    async def __aenter__(self) -> WebSocketConnection:
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class VoiceConnection:
    """Voice WebSocket connection.

    Similar to WebSocketConnection but for voice streaming.

    Authentication:
        By default, the JWT token is sent in the Authorization header (preferred
        for mobile/native clients). Set `use_header_auth=False` to use query
        parameter authentication instead (required for browser clients).
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        token_url: str,
        headers: dict[str, str],
        config: dict[str, Any] | None = None,
        use_header_auth: bool = True,
    ) -> None:
        self._http_client = http_client
        self._token_url = token_url
        self._headers = headers
        self._config = config or {}
        self._use_header_auth = use_header_auth

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._ws_url: str | None = None
        self._token: str | None = None
        self._relationship_id: str | None = None
        self._closed = False

    async def _fetch_token(self) -> tuple[str, str, str]:
        """Fetch voice session token from the API.

        Returns:
            Tuple of (token, ws_url, relationship_id)
        """
        payload: dict[str, Any] = {}
        if self._config:
            payload["voice_config"] = self._config

        response = await self._http_client.post(
            self._token_url,
            headers=self._headers,
            json=payload if payload else None,
        )
        if response.status_code >= 400:
            raise WebSocketError(
                f"Failed to fetch voice token: {response.status_code}",
                code=response.status_code,
            )
        data = response.json()
        return data["token"], data["ws_url"], data.get("relationship_id", "")

    async def _connect(self) -> None:
        """Establish voice WebSocket connection."""
        import websockets

        self._token, self._ws_url, self._relationship_id = await self._fetch_token()

        if self._use_header_auth:
            # Use Authorization header (preferred for mobile/native clients)
            ws_headers = {"Authorization": f"Bearer {self._token}"}
            url = self._ws_url
        else:
            # Use query parameter (fallback for browser clients)
            ws_headers = None
            url = f"{self._ws_url}?token={self._token}"

        logger.debug("Connecting to voice WebSocket: %s (header_auth=%s)", url, self._use_header_auth)
        self._ws = await websockets.connect(url, additional_headers=ws_headers)

    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data through the WebSocket."""
        if not self._ws:
            raise WebSocketError("Voice WebSocket not connected")
        await self._ws.send(audio_data)

    async def send_event(self, event: dict[str, Any]) -> None:
        """Send a control event through the WebSocket."""
        if not self._ws:
            raise WebSocketError("Voice WebSocket not connected")
        await self._ws.send(json.dumps(event))

    def __aiter__(self) -> AsyncIterator[dict[str, Any] | bytes]:
        return self._receive_events()

    async def _receive_events(self) -> AsyncIterator[dict[str, Any] | bytes]:
        """Receive events from the voice WebSocket."""
        import websockets

        while not self._closed and self._ws:
            try:
                message = await self._ws.recv()

                if isinstance(message, bytes):
                    yield message
                else:
                    yield json.loads(message)

            except websockets.ConnectionClosed as e:
                if not self._closed:
                    raise ConnectionClosed(
                        f"Voice WebSocket closed: {e.code}", code=e.code
                    )
                break
            except Exception as e:
                if self._closed:
                    break
                raise WebSocketError(str(e))

    async def close(self) -> None:
        """Close the voice WebSocket connection."""
        self._closed = True
        if self._ws:
            await self._ws.close()
            self._ws = None

    @property
    def relationship_id(self) -> str | None:
        """The relationship ID for this voice connection."""
        return self._relationship_id

    async def __aenter__(self) -> VoiceConnection:
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
