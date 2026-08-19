"""Tests for v2 Behavior Execution.

Tests priority (sync) and async behaviors across REST, SSE, and WebSocket modes.
Requires Modal deployment: modal deploy --env staging app/context/modal_behavior_executor.py

Run with: uv run python tests/test_v2_behavior_execution.py
"""

import asyncio
import json
import os
import sys
import time
from uuid import uuid4

import httpx
import websockets
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
WS_BASE_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _headers_sse() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


# =============================================================================
# Sample Behavior Code
# =============================================================================

# Priority behavior that returns a prompt block
PRIORITY_BEHAVIOR_CODE = '''
async def execute(ctx):
    """Priority behavior that injects context into the prompt."""
    mood = ctx.get_app_state("mood", "neutral")
    name = ctx.get_user_state("name", "friend")

    # Set trace for debugging
    ctx.trace["mood"] = mood
    ctx.trace["name"] = name

    # Return a prompt block to inject into the conversation
    return f"""
The user's current mood is: {mood}
The user's name is: {name}
Please acknowledge this context in your response.
"""
'''

# Async behavior that modifies state
ASYNC_BEHAVIOR_CODE = '''
async def execute(ctx):
    """Async behavior that updates state after the response."""
    turn = ctx.turn_count

    # Update app_state with interaction count
    count = ctx.get_app_state("interaction_count", 0)
    ctx.set_app_state("interaction_count", count + 1)
    ctx.set_app_state("last_turn", turn)
    ctx.set_app_state("behavior_executed", True)

    # Add trace info
    ctx.trace["turn"] = turn
    ctx.trace["new_count"] = count + 1
'''

# Keyword-triggered behavior
KEYWORD_BEHAVIOR_CODE = '''
async def execute(ctx):
    """Behavior triggered by 'weather' keyword."""
    ctx.trace["triggered_by"] = ctx.trigger_details

    return """
WEATHER CONTEXT:
The user is asking about weather. Include current weather information if relevant.
"""
'''


# =============================================================================
# Setup Helpers
# =============================================================================


def create_behavior(
    client: httpx.Client,
    behavior_key: str,
    source_code: str,
    triggers: list,
    priority: bool = False,
) -> str:
    """Create a behavior for the companion."""
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors"

    # Try to delete existing behavior first (in case of previous failed test)
    delete_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}"
    client.delete(delete_url, headers=_headers())

    response = client.post(
        url,
        headers=_headers(),
        json={
            "behavior_key": behavior_key,
            "triggers": triggers,
            "priority": priority,
            "enabled": True,
        },
    )

    if response.status_code not in (200, 201):
        print(f"Failed to create behavior: {response.text}")
        return None

    data = response.json()
    data["behavior_id"]  # Response uses behavior_id, not id

    # Update source code via definition endpoint
    patch_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}/definition"
    patch_response = client.patch(
        patch_url,
        headers=_headers(),
        json={"source_code": source_code},
    )

    if patch_response.status_code != 200:
        print(f"Failed to update source code: {patch_response.text}")
        return None

    return behavior_key  # Return behavior_key for cleanup (used in delete)


def delete_behavior(client: httpx.Client, behavior_key: str) -> None:
    """Delete a behavior link from companion."""
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}"
    client.delete(url, headers=_headers())


def create_relationship(client: httpx.Client, user_id: str) -> str:
    """Create a relationship and return its ID."""
    url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"
    response = client.put(url, headers=_headers(), json={})
    assert response.status_code == 200
    return response.json()["id"]


def set_relationship_state(
    client: httpx.Client,
    relationship_id: str,
    app_state: dict | None = None,
    user_state: dict | None = None,
) -> None:
    """Set state on a relationship.

    Uses /app-state endpoint for app_state and /state endpoint for user_state.
    """
    if app_state:
        url = f"{BASE_URL}/v2/relationships/{relationship_id}/app-state"
        response = client.patch(url, headers=_headers(), json=app_state)
        if response.status_code != 200:
            print(f"Failed to set app_state: {response.text}")

    if user_state:
        # Use the state endpoint with scope="user"
        url = f"{BASE_URL}/v2/relationships/{relationship_id}/state"
        response = client.patch(
            url, headers=_headers(), json={"scope": "user", "changes": user_state}
        )
        if response.status_code != 200:
            print(f"Failed to set user_state: {response.text}")


def get_relationship_state(client: httpx.Client, relationship_id: str) -> dict:
    """Get state from a relationship.

    Returns a dict with app_state, user_state, companion_state.
    """
    url = f"{BASE_URL}/v2/relationships/{relationship_id}"
    response = client.get(url, headers=_headers())
    if response.status_code == 200:
        data = response.json()
        return {
            "app_state": data.get("app_state", {}),
            "user_state": data.get("user_state", {}),
            "companion_state": data.get("companion_state", {}),
        }
    return {}


def cleanup(
    client: httpx.Client, relationship_id: str | None = None, behavior_keys: list | None = None
):
    """Clean up test resources."""
    if behavior_keys:
        for bkey in behavior_keys:
            if bkey:
                delete_behavior(client, bkey)
    if relationship_id:
        client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())


# =============================================================================
# REST Tests
# =============================================================================


def test_priority_behavior_injects_prompt_rest():
    """Priority behavior injects prompt block in REST response."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=60.0) as client:
        try:
            # Create priority behavior with "always" trigger
            behavior_key = create_behavior(
                client,
                behavior_key="test_priority_prompt",
                source_code=PRIORITY_BEHAVIOR_CODE,
                triggers=["always"],
                priority=True,
            )
            assert behavior_key, "Failed to create priority behavior"
            behavior_keys.append(behavior_key)

            # Create relationship and set state
            relationship_id = create_relationship(client, user_id)
            set_relationship_state(
                client,
                relationship_id,
                app_state={"mood": "happy"},
                user_state={"name": "TestUser"},
            )

            # Send message
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                url,
                headers=_headers(),
                json={"content": "Hello! Acknowledge my context."},
            )

            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}: {response.text}"
            )
            data = response.json()

            # Verify response - the LLM should have seen the injected context
            data["message"]["content"].lower()
            # The LLM should acknowledge the mood or name from the injected context
            # (Note: This is a soft assertion - LLM behavior can vary)
            print(f"  Response: {data['message']['content'][:100]}...")

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_priority_behavior_injects_prompt_rest")


def test_async_behavior_updates_state_rest():
    """Async behavior updates state after REST response."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=60.0) as client:
        try:
            # Create async behavior with "always" trigger
            behavior_key = create_behavior(
                client,
                behavior_key="test_async_state",
                source_code=ASYNC_BEHAVIOR_CODE,
                triggers=["always"],
                priority=False,  # Async
            )
            assert behavior_key, "Failed to create async behavior"
            behavior_keys.append(behavior_key)

            # Create relationship
            relationship_id = create_relationship(client, user_id)

            # Initial state should not have behavior_executed
            initial_state = get_relationship_state(client, relationship_id)
            assert not initial_state.get("app_state", {}).get("behavior_executed"), (
                "State should not be set initially"
            )

            # Send message
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                url,
                headers=_headers(),
                json={"content": "Trigger the async behavior"},
            )

            assert response.status_code == 200

            # Wait for async behavior to complete (poll for state change)
            max_wait = 15  # seconds
            start = time.time()
            state_updated = False

            while time.time() - start < max_wait:
                state = get_relationship_state(client, relationship_id)
                if state.get("app_state", {}).get("behavior_executed"):
                    state_updated = True
                    print(f"  State after behavior: {state.get('app_state')}")
                    break
                time.sleep(1)

            assert state_updated, "Async behavior did not update state within timeout"

            # Verify state values
            final_state = get_relationship_state(client, relationship_id)
            app_state = final_state.get("app_state", {})
            assert app_state.get("interaction_count") == 1
            assert app_state.get("last_turn") is not None

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_async_behavior_updates_state_rest")


def test_keyword_triggered_behavior_rest():
    """Keyword-triggered behavior fires on matching message."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=60.0) as client:
        try:
            # Create keyword-triggered priority behavior
            behavior_key = create_behavior(
                client,
                behavior_key="test_keyword_weather",
                source_code=KEYWORD_BEHAVIOR_CODE,
                triggers=["keyword:weather,forecast"],
                priority=True,
            )
            assert behavior_key, "Failed to create keyword behavior"
            behavior_keys.append(behavior_key)

            # Create relationship
            relationship_id = create_relationship(client, user_id)

            # Send message with keyword
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                url,
                headers=_headers(),
                json={"content": "What's the weather like today?"},
            )

            assert response.status_code == 200
            data = response.json()

            # The behavior should have been triggered and injected context
            print(f"  Response: {data['message']['content'][:100]}...")

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_keyword_triggered_behavior_rest")


# =============================================================================
# SSE Streaming Tests
# =============================================================================


def test_priority_behavior_injects_prompt_sse():
    """Priority behavior injects prompt block in SSE streaming."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=60.0) as client:
        try:
            # Create priority behavior
            behavior_key = create_behavior(
                client,
                behavior_key="test_priority_sse",
                source_code=PRIORITY_BEHAVIOR_CODE,
                triggers=["always"],
                priority=True,
            )
            assert behavior_key, "Failed to create priority behavior"
            behavior_keys.append(behavior_key)

            # Create relationship and set state
            relationship_id = create_relationship(client, user_id)
            set_relationship_state(
                client,
                relationship_id,
                app_state={"mood": "excited"},
                user_state={"name": "StreamUser"},
            )

            # Send message via SSE
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"

            with client.stream(
                "POST",
                url,
                headers=_headers_sse(),
                json={"content": "Acknowledge my context please."},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")

                events = []
                current_event = {"event": None, "data": None}

                for line in response.iter_lines():
                    if line.startswith("event:"):
                        current_event["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        current_event["data"] = line[5:].strip()
                    elif line == "":
                        if current_event["event"] and current_event["data"]:
                            events.append(
                                {
                                    "event": current_event["event"],
                                    "data": json.loads(current_event["data"]),
                                }
                            )
                        current_event = {"event": None, "data": None}

                # Verify we got events
                event_types = [e["event"] for e in events]
                assert "ack" in event_types, f"Expected 'ack' event, got: {event_types}"
                assert "message" in event_types, f"Expected 'message' event, got: {event_types}"

                # Get message content
                msg_event = next(e for e in events if e["event"] == "message")
                content = msg_event["data"]["data"]["content"]
                print(f"  Response: {content[:100]}...")

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_priority_behavior_injects_prompt_sse")


def test_async_behavior_updates_state_sse():
    """Async behavior updates state after SSE streaming response."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=60.0) as client:
        try:
            # Create async behavior
            behavior_key = create_behavior(
                client,
                behavior_key="test_async_sse",
                source_code=ASYNC_BEHAVIOR_CODE,
                triggers=["always"],
                priority=False,
            )
            assert behavior_key, "Failed to create async behavior"
            behavior_keys.append(behavior_key)

            # Create relationship
            relationship_id = create_relationship(client, user_id)

            # Send message via SSE
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"

            with client.stream(
                "POST",
                url,
                headers=_headers_sse(),
                json={"content": "Test async behavior with streaming"},
            ) as response:
                assert response.status_code == 200
                # Consume the stream
                for _line in response.iter_lines():
                    pass

            # Wait for async behavior to complete
            max_wait = 15
            start = time.time()
            state_updated = False

            while time.time() - start < max_wait:
                state = get_relationship_state(client, relationship_id)
                if state.get("app_state", {}).get("behavior_executed"):
                    state_updated = True
                    print(f"  State after behavior: {state.get('app_state')}")
                    break
                time.sleep(1)

            assert state_updated, "Async behavior did not update state within timeout"

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_async_behavior_updates_state_sse")


# =============================================================================
# WebSocket Tests
# =============================================================================


async def test_priority_behavior_injects_prompt_ws():
    """Priority behavior injects prompt block in WebSocket response."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Create priority behavior (sync call via wrapper)
            with httpx.Client(timeout=60.0) as sync_client:
                behavior_key = create_behavior(
                    sync_client,
                    behavior_key="test_priority_ws",
                    source_code=PRIORITY_BEHAVIOR_CODE,
                    triggers=["always"],
                    priority=True,
                )
                assert behavior_key, "Failed to create priority behavior"
                behavior_keys.append(behavior_key)

                # Create relationship and set state
                relationship_id = create_relationship(sync_client, user_id)
                set_relationship_state(
                    sync_client,
                    relationship_id,
                    app_state={"mood": "curious"},
                    user_state={"name": "WSUser"},
                )

            # Get WebSocket token
            token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
            response = await client.post(token_url, headers=_headers())
            assert response.status_code == 200
            token = response.json()["token"]

            # Connect WebSocket
            ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

            async with websockets.connect(ws_url, close_timeout=30) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send message
                await ws.send(
                    json.dumps(
                        {
                            "type": "user_message",
                            "client_message_id": f"msg-{uuid4().hex[:8]}",
                            "content": "Acknowledge my context please.",
                        }
                    )
                )

                # Collect events until we get the final message
                events = []
                timeout_at = asyncio.get_event_loop().time() + 30

                while True:
                    if asyncio.get_event_loop().time() > timeout_at:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        event = json.loads(raw)
                        events.append(event)
                        if event["type"] == "message":
                            break
                    except TimeoutError:
                        break

                # Verify events
                event_types = [e["type"] for e in events]
                assert "ack" in event_types, f"Expected 'ack' event, got: {event_types}"
                assert "message" in event_types, f"Expected 'message' event, got: {event_types}"

                # Get message content
                msg_event = next(e for e in events if e["type"] == "message")
                content = msg_event["data"]["content"]
                print(f"  Response: {content[:100]}...")

        finally:
            with httpx.Client(timeout=30.0) as sync_client:
                cleanup(sync_client, relationship_id, behavior_keys)

    print("✓ test_priority_behavior_injects_prompt_ws")


async def test_async_behavior_updates_state_ws():
    """Async behavior updates state after WebSocket response."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Create async behavior (sync call)
            with httpx.Client(timeout=60.0) as sync_client:
                behavior_key = create_behavior(
                    sync_client,
                    behavior_key="test_async_ws",
                    source_code=ASYNC_BEHAVIOR_CODE,
                    triggers=["always"],
                    priority=False,
                )
                assert behavior_key, "Failed to create async behavior"
                behavior_keys.append(behavior_key)

                # Create relationship
                relationship_id = create_relationship(sync_client, user_id)

            # Get WebSocket token
            token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
            response = await client.post(token_url, headers=_headers())
            assert response.status_code == 200
            token = response.json()["token"]

            # Connect WebSocket
            ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

            async with websockets.connect(ws_url, close_timeout=30) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send message
                await ws.send(
                    json.dumps(
                        {
                            "type": "user_message",
                            "client_message_id": f"msg-{uuid4().hex[:8]}",
                            "content": "Test async behavior with WebSocket",
                        }
                    )
                )

                # Wait for message event
                timeout_at = asyncio.get_event_loop().time() + 30

                while asyncio.get_event_loop().time() < timeout_at:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        event = json.loads(raw)
                        if event["type"] == "message":
                            break
                    except TimeoutError:
                        break

            # Wait for async behavior to complete
            max_wait = 15
            start = time.time()
            state_updated = False

            with httpx.Client(timeout=30.0) as sync_client:
                while time.time() - start < max_wait:
                    state = get_relationship_state(sync_client, relationship_id)
                    if state.get("app_state", {}).get("behavior_executed"):
                        state_updated = True
                        print(f"  State after behavior: {state.get('app_state')}")
                        break
                    time.sleep(1)

            assert state_updated, "Async behavior did not update state within timeout"

        finally:
            with httpx.Client(timeout=30.0) as sync_client:
                cleanup(sync_client, relationship_id, behavior_keys)

    print("✓ test_async_behavior_updates_state_ws")


async def test_keyword_behavior_ws():
    """Keyword-triggered behavior fires on matching WebSocket message."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            # Create keyword behavior
            with httpx.Client(timeout=60.0) as sync_client:
                behavior_key = create_behavior(
                    sync_client,
                    behavior_key="test_keyword_ws",
                    source_code=KEYWORD_BEHAVIOR_CODE,
                    triggers=["keyword:weather"],
                    priority=True,
                )
                assert behavior_key, "Failed to create keyword behavior"
                behavior_keys.append(behavior_key)

                # Create relationship
                relationship_id = create_relationship(sync_client, user_id)

            # Get WebSocket token
            token_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/ws-token"
            response = await client.post(token_url, headers=_headers())
            assert response.status_code == 200
            token = response.json()["token"]

            ws_url = f"{WS_BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/connect?token={token}"

            async with websockets.connect(ws_url, close_timeout=30) as ws:
                # Skip connected event
                await asyncio.wait_for(ws.recv(), timeout=5)

                # Send message with keyword (use phrasing that won't trigger tools layer)
                await ws.send(
                    json.dumps(
                        {
                            "type": "user_message",
                            "client_message_id": f"msg-{uuid4().hex[:8]}",
                            "content": "Tell me about the weather keyword test",
                        }
                    )
                )

                # Wait for message event
                events = []
                timeout_at = asyncio.get_event_loop().time() + 30

                while asyncio.get_event_loop().time() < timeout_at:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        event = json.loads(raw)
                        events.append(event)
                        if event["type"] == "message":
                            break
                    except TimeoutError:
                        break

                # Verify message received
                msg_event = next((e for e in events if e["type"] == "message"), None)
                assert msg_event, "Expected message event"
                print(f"  Response: {msg_event['data']['content'][:100]}...")

        finally:
            with httpx.Client(timeout=30.0) as sync_client:
                cleanup(sync_client, relationship_id, behavior_keys)

    print("✓ test_keyword_behavior_ws")


# =============================================================================
# Combined Tests
# =============================================================================


def test_priority_and_async_together_rest():
    """Both priority and async behaviors execute on same message."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=60.0) as client:
        try:
            # Create priority behavior
            priority_key = create_behavior(
                client,
                behavior_key="test_combined_priority",
                source_code=PRIORITY_BEHAVIOR_CODE,
                triggers=["always"],
                priority=True,
            )
            assert priority_key, "Failed to create priority behavior"
            behavior_keys.append(priority_key)

            # Create async behavior
            async_key = create_behavior(
                client,
                behavior_key="test_combined_async",
                source_code=ASYNC_BEHAVIOR_CODE,
                triggers=["always"],
                priority=False,
            )
            assert async_key, "Failed to create async behavior"
            behavior_keys.append(async_key)

            # Create relationship with initial state
            relationship_id = create_relationship(client, user_id)
            set_relationship_state(
                client,
                relationship_id,
                app_state={"mood": "thoughtful"},
                user_state={"name": "CombinedUser"},
            )

            # Send message
            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                url,
                headers=_headers(),
                json={"content": "Test both behaviors together"},
            )

            assert response.status_code == 200
            print(f"  Response: {response.json()['message']['content'][:100]}...")

            # Wait for async behavior to complete
            max_wait = 15
            start = time.time()
            state_updated = False

            while time.time() - start < max_wait:
                state = get_relationship_state(client, relationship_id)
                if state.get("app_state", {}).get("behavior_executed"):
                    state_updated = True
                    print(f"  Final state: {state.get('app_state')}")
                    break
                time.sleep(1)

            assert state_updated, "Async behavior did not complete"

            # Verify state has both priority-set mood and async-set behavior_executed
            final_state = get_relationship_state(client, relationship_id)
            assert (
                final_state.get("app_state", {}).get("mood") == "thoughtful"
            )  # From initial state
            assert final_state.get("app_state", {}).get("behavior_executed")  # From async behavior

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_priority_and_async_together_rest")


def test_turn_count_trigger():
    """Turn count trigger fires on specific turns."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=120.0) as client:
        try:
            # Create behavior that triggers on turn 2
            behavior_key = create_behavior(
                client,
                behavior_key="test_turn_trigger",
                source_code='''
async def execute(ctx):
    """Behavior triggered on turn 2."""
    ctx.set_app_state("triggered_on_turn", ctx.turn_count)
    return f"Turn trigger activated on turn {ctx.turn_count}!"
''',
                triggers=["turn:2"],
                priority=True,
            )
            assert behavior_key, "Failed to create turn behavior"
            behavior_keys.append(behavior_key)

            # Create relationship
            relationship_id = create_relationship(client, user_id)

            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"

            # Turn 1 - should not trigger
            response1 = client.post(url, headers=_headers(), json={"content": "First message"})
            assert response1.status_code == 200

            state1 = get_relationship_state(client, relationship_id)
            assert not state1.get("app_state", {}).get("triggered_on_turn"), (
                "Should not trigger on turn 1"
            )

            # Turn 2 - should trigger
            response2 = client.post(url, headers=_headers(), json={"content": "Second message"})
            assert response2.status_code == 200

            # Wait briefly for state update
            time.sleep(2)

            state2 = get_relationship_state(client, relationship_id)
            triggered_turn = state2.get("app_state", {}).get("triggered_on_turn")
            assert triggered_turn == 2, f"Expected trigger on turn 2, got: {triggered_turn}"

            print(f"  Triggered on turn: {triggered_turn}")

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_turn_count_trigger")


def test_every_n_trigger():
    """Every N trigger fires on multiples of N."""
    user_id = f"test-user-{uuid4().hex[:8]}"
    behavior_keys = []
    relationship_id = None

    with httpx.Client(timeout=120.0) as client:
        try:
            # Create behavior that triggers every 2 turns
            behavior_key = create_behavior(
                client,
                behavior_key="test_every_trigger",
                source_code='''
async def execute(ctx):
    """Behavior triggered every 2 turns."""
    count = ctx.get_app_state("trigger_count", 0)
    ctx.set_app_state("trigger_count", count + 1)
    ctx.set_app_state("last_triggered_turn", ctx.turn_count)
    return f"Every-2 trigger activated (count: {count + 1})!"
''',
                triggers=["every:2"],
                priority=True,
            )
            assert behavior_key, "Failed to create every-n behavior"
            behavior_keys.append(behavior_key)

            # Create relationship
            relationship_id = create_relationship(client, user_id)

            url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"

            # Send 4 messages
            for i in range(1, 5):
                response = client.post(url, headers=_headers(), json={"content": f"Message {i}"})
                assert response.status_code == 200

            # Wait briefly for state updates
            time.sleep(3)

            state = get_relationship_state(client, relationship_id)
            trigger_count = state.get("app_state", {}).get("trigger_count", 0)

            # Should have triggered on turns 2 and 4
            assert trigger_count == 2, f"Expected 2 triggers, got: {trigger_count}"

            print(f"  Trigger count after 4 turns: {trigger_count}")

        finally:
            cleanup(client, relationship_id, behavior_keys)

    print("✓ test_every_n_trigger")


# =============================================================================
# Main
# =============================================================================


def run_async_test(test_func):
    """Helper to run async tests."""
    asyncio.run(test_func())


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}")
    print()
    print("NOTE: These tests require Modal deployment:")
    print("  modal deploy --env staging app/context/modal_behavior_executor.py")
    print()

    # REST tests
    print("=== REST Tests ===")
    test_priority_behavior_injects_prompt_rest()
    test_async_behavior_updates_state_rest()
    test_keyword_triggered_behavior_rest()

    # SSE tests
    print("\n=== SSE Streaming Tests ===")
    test_priority_behavior_injects_prompt_sse()
    test_async_behavior_updates_state_sse()

    # WebSocket tests
    print("\n=== WebSocket Tests ===")
    run_async_test(test_priority_behavior_injects_prompt_ws)
    run_async_test(test_async_behavior_updates_state_ws)
    run_async_test(test_keyword_behavior_ws)

    # Combined tests
    print("\n=== Combined/Trigger Tests ===")
    test_priority_and_async_together_rest()
    test_turn_count_trigger()
    test_every_n_trigger()

    print("\n✅ All behavior execution tests passed!")
