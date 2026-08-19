"""Emotion Machine SDK - Build AI companions with persistent relationships.

Usage:
    from emotion_machine import EmotionMachine, behavior

    async with EmotionMachine(api_key="...") as em:
        # Get a relationship handle
        rel = em.relationship(companion_id, user_id)

        # Send a message
        response = await rel.send("Hello!")
        print(response["message"]["content"])

        # Stream a response
        async for chunk in rel.stream("Tell me a story"):
            if chunk["data"].get("type") == "delta":
                print(chunk["data"]["data"]["content"], end="")

        # WebSocket for real-time messaging
        async with rel.connect() as ws:
            await ws.send("Hello!")
            async for event in ws:
                handle(event)

        # Define and deploy behaviors
        @behavior(triggers=["always"], priority=True)
        async def mood_tracker(ctx):
            if "anxious" in ctx.message.lower():
                return "User seems anxious."

        await em.behaviors.deploy(companion_id)
"""

from .behaviors import (
    behavior,
    clear_behavior_registry,
    get_registered_behaviors,
    BehaviorValidationError,
)
from .client import EmotionMachine
from .exceptions import (
    APIError,
    ConnectionClosed,
    KnowledgeJobFailed,
    WebSocketError,
)
from .relationship import Relationship, Session
from .streaming import parse_sse_async, parse_sse_sync
from .websocket import VoiceConnection, WebSocketConnection

__all__ = [
    # Main client
    "EmotionMachine",
    # Relationship
    "Relationship",
    "Session",
    # WebSocket
    "WebSocketConnection",
    "VoiceConnection",
    # Behaviors
    "behavior",
    "get_registered_behaviors",
    "clear_behavior_registry",
    "BehaviorValidationError",
    # Streaming
    "parse_sse_async",
    "parse_sse_sync",
    # Exceptions
    "APIError",
    "KnowledgeJobFailed",
    "WebSocketError",
    "ConnectionClosed",
]

__version__ = "2.0.0"
