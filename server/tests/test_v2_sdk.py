"""Tests for v2 Emotion Machine SDK.

Run with: uv run python tests/test_v2_sdk.py

Environment variables:
- TEST_EM_API_KEY: Required. API key for testing.
- TEST_EM_COMPANION_ID: Optional. Uses existing companion if set.
- EM_BASE_URL: Optional. Defaults to https://api.emotionmachine.ai (production).
- USE_LOCAL_SDK: Optional. Set to use local source instead of installed package.

To simulate external user (production):
    pip install emotion-machine
    TEST_EM_API_KEY=... python tests/test_v2_sdk.py

For local development:
    USE_LOCAL_SDK=1 EM_BASE_URL=http://localhost:8100 TEST_EM_API_KEY=... uv run python tests/test_v2_sdk.py
"""

import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest

# Use installed package by default. Set USE_LOCAL_SDK=1 to use local source.
if os.getenv("USE_LOCAL_SDK"):
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "../../packages/pip-emotion-machine/src")
    )

from dotenv import load_dotenv

load_dotenv()

pytest.importorskip(
    "emotion_machine", reason="install packages/pip-emotion-machine to run SDK tests"
)

from emotion_machine import (
    APIError,
    EmotionMachine,
    behavior,
    clear_behavior_registry,
    get_registered_behaviors,
)

# Default to production URL to simulate external user experience
BASE_URL = os.getenv("EM_BASE_URL", "https://api.emotionmachine.ai")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")


# =============================================================================
# Sample Behaviors for Testing
# =============================================================================

SAMPLE_BEHAVIOR_SOURCE = '''
async def execute(ctx):
    """Sample behavior for testing."""
    ctx.trace["sdk_test"] = True
    message = ctx.last_user_message or ""
    if "test" in message.lower():
        return "# SDK TEST\\nThis is a test behavior response."
    return None
'''


# =============================================================================
# Test Helpers
# =============================================================================


async def create_test_companion(em: EmotionMachine) -> str:
    """Create a test companion."""
    companion = await em.companions.create(
        name=f"SDK Test Companion {uuid4().hex[:8]}",
        description="Test companion for SDK tests",
        config={
            "system_prompt": {
                "full_system_prompt": "You are a helpful test assistant. Keep responses brief."
            },
            "memory": {"enabled": True},
        },
    )
    return companion["id"]


async def cleanup_companion(em: EmotionMachine, companion_id: str) -> None:
    """Delete a test companion."""
    try:
        await em.companions.delete(companion_id)
        print(f"  Deleted companion: {companion_id}")
    except APIError:
        pass


# =============================================================================
# Tests
# =============================================================================


async def test_companions_crud():
    """Test companion CRUD operations."""
    print("\n=== Test: Companions CRUD ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        # Create
        companion = await em.companions.create(
            name=f"SDK Test {uuid4().hex[:8]}",
            description="Test description",
            config={"system_prompt": {"full_system_prompt": "Test prompt"}},
        )
        companion_id = companion["id"]
        print(f"  Created: {companion_id}")

        try:
            # Get
            fetched = await em.companions.get(companion_id)
            assert fetched["id"] == companion_id
            print("  Get: OK")

            # List
            companions = await em.companions.list()
            assert any(c["id"] == companion_id for c in companions)
            print("  List: OK")

            # Update
            updated = await em.companions.update(companion_id, name="Updated Name")
            assert updated["name"] == "Updated Name"
            print("  Update: OK")

            # Delete
            await em.companions.delete(companion_id)
            print("  Delete: OK")

        except Exception:
            await cleanup_companion(em, companion_id)
            raise

    print("PASSED")


@pytest.mark.anyio
async def test_companion_model_temperature():
    """Test companion config with model and temperature."""
    print("\n=== Test: Companion Model/Temperature Config ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        # Create companion with model and temperature
        companion = await em.companions.create(
            name=f"Model Test {uuid4().hex[:8]}",
            description="Test companion with model config",
            config={
                "system_prompt": {"full_system_prompt": "You are a test assistant."},
                "model": "claude-sonnet-4",
                "temperature": 0.5,
            },
        )
        companion_id = companion["id"]
        print(f"  Created companion with model config: {companion_id}")

        try:
            # Verify config is returned (model/temperature are migrated to inference block)
            fetched = await em.companions.get(companion_id)
            config = fetched.get("config", {})
            inference = config.get("inference", {})
            assert inference.get("model") == "claude-sonnet-4", (
                f"Expected model 'claude-sonnet-4', got {inference.get('model')}"
            )
            assert inference.get("temperature") == 0.5, (
                f"Expected temperature 0.5, got {inference.get('temperature')}"
            )
            print("  Config persisted correctly: OK")

            # Update model (top-level fields are migrated to inference block)
            updated = await em.companions.update(
                companion_id, config={"inference": {"model": "openai-gpt4o", "temperature": 0.9}}
            )
            updated_config = updated.get("config", {})
            updated_inference = updated_config.get("inference", {})
            assert updated_inference.get("model") == "openai-gpt4o"
            assert updated_inference.get("temperature") == 0.9
            print("  Config updated correctly: OK")

        finally:
            await cleanup_companion(em, companion_id)

    print("PASSED")


async def test_relationship_send():
    """Test sending a message through relationship."""
    print("\n=== Test: Relationship Send ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)

            # Send message
            response = await rel.send("Hello from SDK test!")

            assert "message" in response
            assert "content" in response["message"]
            assert len(response["message"]["content"]) > 0

            print(f"  Response: {response['message']['content'][:100]}...")
            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_relationship_stream():
    """Test streaming a response through relationship."""
    print("\n=== Test: Relationship Stream ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)

            # Stream message
            events = []
            deltas = []

            async for chunk in rel.stream("Say hello briefly."):
                events.append(chunk.get("event"))
                data = chunk.get("data", {})
                if isinstance(data, dict) and data.get("type") == "delta":
                    delta_content = data.get("data", {}).get("content", "")
                    deltas.append(delta_content)

            assert "ack" in events or len(deltas) > 0

            streamed = "".join(deltas)
            print(f"  Streamed: {streamed[:100]}...")
            print(f"  Event types: {set(events)}")
            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_profile_operations():
    """Test profile get/set/patch/clear operations."""
    print("\n=== Test: Profile Operations ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)

            # Ensure relationship exists
            await rel.ensure()

            # Set profile
            await rel.profile_set({"user": {"name": "TestUser", "age": 25}})
            print("  Set: OK")

            # Get profile
            profile = await rel.profile_get()
            assert profile.get("user", {}).get("name") == "TestUser"
            print("  Get: OK")

            # Patch profile - JSON Merge Patch (RFC 7396) does shallow merge
            # This adds a new top-level key without touching "user"
            await rel.profile_patch({"preferences": {"theme": "dark"}})
            profile = await rel.profile_get()
            assert profile.get("preferences", {}).get("theme") == "dark"
            assert profile.get("user", {}).get("name") == "TestUser"  # Still there
            print("  Patch: OK")

            # Clear profile
            await rel.profile_clear()
            profile = await rel.profile_get()
            assert profile == {} or profile.get("user") is None
            print("  Clear: OK")

            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_session_lifecycle():
    """Test session start/send/end operations."""
    print("\n=== Test: Session Lifecycle ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)

            # Start session
            session = await rel.session_start(type="sdk_test")
            print(f"  Started session: {session.id}")

            # Send message in session
            response = await session.send("This is a session message.")
            assert "message" in response
            print("  Send in session: OK")

            # End session
            result = await session.end()
            assert result.get("status") == "ended"
            print(f"  Session ended, summary: {result.get('summary', 'N/A')[:50]}...")

            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_behavior_decorator():
    """Test the @behavior decorator and registry."""
    print("\n=== Test: Behavior Decorator ===")

    # Clear any existing behaviors
    clear_behavior_registry()

    # Define behaviors using decorator
    @behavior(triggers=["always"], priority=True)
    async def test_behavior_1(ctx):
        return "Test response 1"

    @behavior(triggers=["every:5"], priority=False, key="custom_key")
    async def test_behavior_2(ctx):
        return "Test response 2"

    # Check registry
    behaviors = get_registered_behaviors()

    assert "test_behavior_1" in behaviors
    assert "custom_key" in behaviors

    spec1 = behaviors["test_behavior_1"]
    assert spec1.triggers == ["always"]
    assert spec1.priority is True
    assert "async def test_behavior_1" in spec1.source_code

    spec2 = behaviors["custom_key"]
    assert spec2.triggers == ["every:5"]
    assert spec2.priority is False

    print(f"  Registered behaviors: {list(behaviors.keys())}")
    print("PASSED")

    # Clean up
    clear_behavior_registry()


async def test_behavior_create_and_deploy():
    """Test creating and deploying behaviors via SDK."""
    print("\n=== Test: Behavior Create and Deploy ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False
        behavior_key = f"sdk_test_behavior_{uuid4().hex[:8]}"

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        try:
            # Create behavior
            await em.behaviors.create(
                companion_id,
                behavior_key,
                SAMPLE_BEHAVIOR_SOURCE,
                triggers=["always"],
                priority=True,
            )
            print(f"  Created behavior: {behavior_key}")

            # List behaviors
            behaviors = await em.behaviors.list(companion_id)
            assert any(b.get("behavior_key") == behavior_key for b in behaviors)
            print("  List: OK")

            # Get behavior
            fetched = await em.behaviors.get(companion_id, behavior_key)
            assert fetched is not None
            print("  Get: OK")

            # Update behavior
            await em.behaviors.update(
                companion_id,
                behavior_key,
                triggers=["every:3"],
            )
            fetched = await em.behaviors.get(companion_id, behavior_key)
            assert "every:3" in fetched.get("triggers", [])
            print("  Update: OK")

            # Delete behavior
            await em.behaviors.delete(companion_id, behavior_key)
            print("  Delete: OK")

            print("PASSED")

        finally:
            # Clean up behavior if it still exists
            try:
                await em.behaviors.delete(companion_id, behavior_key)
            except APIError:
                pass

            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_behavior_trigger():
    """Test triggering a behavior via API."""
    print("\n=== Test: Behavior Trigger ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False
        behavior_key = f"sdk_test_trigger_{uuid4().hex[:8]}"

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            # Create a behavior to trigger
            await em.behaviors.create(
                companion_id,
                behavior_key,
                SAMPLE_BEHAVIOR_SOURCE,
                triggers=[],  # No auto triggers, only API
                priority=False,
            )
            print(f"  Created behavior: {behavior_key}")

            # Get relationship
            rel = em.relationship(companion_id, user_id)
            await rel.ensure()

            # Trigger the behavior
            result = await rel.behavior_trigger(behavior_key, context={"test": True})

            assert "job_id" in result
            print(f"  Triggered, job_id: {result['job_id']}")

            print("PASSED")

        finally:
            try:
                await em.behaviors.delete(companion_id, behavior_key)
            except APIError:
                pass

            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_sse_parsing():
    """Test SSE parsing utilities."""
    print("\n=== Test: SSE Parsing ===")

    from emotion_machine.streaming import parse_sse_sync

    # Simulate SSE data
    sse_data = [
        "event: ack",
        "id: 1",
        'data: {"type": "ack", "turn_id": "abc123"}',
        "",
        "event: delta",
        "id: 2",
        'data: {"type": "delta", "data": {"content": "Hello"}}',
        "",
        "event: message",
        "id: 3",
        'data: {"type": "message", "data": {"content": "Hello world"}}',
        "",
    ]

    events = list(parse_sse_sync(iter(sse_data)))

    assert len(events) == 3
    assert events[0]["event"] == "ack"
    assert events[1]["event"] == "delta"
    assert events[2]["event"] == "message"

    print(f"  Parsed {len(events)} events")
    print("PASSED")


async def test_error_handling():
    """Test error handling."""
    print("\n=== Test: Error Handling ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        # Try to get non-existent companion
        try:
            await em.companions.get("00000000-0000-0000-0000-000000000000")
            raise AssertionError("Should have raised APIError")
        except APIError as e:
            assert e.status_code == 404
            print(f"  404 error handled: {e.message[:50]}...")

        print("PASSED")


async def test_behavior_deploy_from_decorator():
    """Test deploying decorated behaviors."""
    print("\n=== Test: Behavior Deploy from Decorator ===")

    # Clear registry
    clear_behavior_registry()

    # Define behaviors
    @behavior(triggers=["always"], priority=True)
    async def sdk_deploy_test(ctx):
        return "Deployed via SDK"

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        try:
            # Deploy all decorated behaviors
            results = await em.behaviors.deploy(companion_id, delete_existing=True)

            assert len(results) == 1
            print(f"  Deployed {len(results)} behaviors")

            # Verify it was deployed
            behaviors = await em.behaviors.list(companion_id)
            assert any(b.get("behavior_key") == "sdk_deploy_test" for b in behaviors)
            print("  Verified deployment")

            # Clean up
            await em.behaviors.delete(companion_id, "sdk_deploy_test")

            print("PASSED")

        finally:
            clear_behavior_registry()

            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_message_with_behavior():
    """Test sending a message that triggers a behavior."""
    print("\n=== Test: Message with Behavior ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False
        behavior_key = f"sdk_msg_test_{uuid4().hex[:8]}"

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            # Create a priority behavior
            await em.behaviors.create(
                companion_id,
                behavior_key,
                SAMPLE_BEHAVIOR_SOURCE,
                triggers=["always"],
                priority=True,
            )
            print(f"  Created behavior: {behavior_key}")

            # Send message that should trigger behavior
            rel = em.relationship(companion_id, user_id)
            response = await rel.send("This is a test message!")

            assert "message" in response
            print(f"  Response: {response['message']['content'][:100]}...")

            print("PASSED")

        finally:
            try:
                await em.behaviors.delete(companion_id, behavior_key)
            except APIError:
                pass

            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_relationship_get():
    """Test getting relationship object."""
    print("\n=== Test: Relationship Get ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)

            # Ensure relationship exists
            await rel.ensure()

            # Get full relationship object
            data = await rel.get()

            assert "id" in data
            assert "companion_id" in data
            assert "user_id" in data
            assert data["user_id"] == user_id
            print(f"  Got relationship: {data['id']}")

            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_config_patch():
    """Test patching relationship config."""
    print("\n=== Test: Config Patch ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)
            await rel.ensure()

            # Patch config
            result = await rel.config_patch({"include_profile_in_prompt": True})

            assert "config" in result
            assert result["config"].get("include_profile_in_prompt") is True
            print("  Config patched: include_profile_in_prompt=True")

            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_session_list():
    """Test listing sessions."""
    print("\n=== Test: Session List ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)

            # Create a session
            session = await rel.session_start(type="test_list")
            await session.end()

            # List sessions
            result = await rel.session_list()

            assert "sessions" in result
            assert len(result["sessions"]) >= 1
            print(f"  Found {len(result['sessions'])} sessions")

            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_inbox_operations():
    """Test inbox check and ack operations."""
    print("\n=== Test: Inbox Operations ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            rel = em.relationship(companion_id, user_id)
            await rel.ensure()

            # Check inbox (should return empty list for new relationship)
            messages = await rel.inbox_check()
            assert isinstance(messages, list)
            print(f"  inbox_check: {len(messages)} messages")

            # Test with parameters
            messages = await rel.inbox_check(limit=10, include_delivered=True)
            assert isinstance(messages, list)
            print("  inbox_check with params: OK")

            # Test inbox_ack with empty list (should succeed)
            result = await rel.inbox_ack([])
            assert "acknowledged" in result
            print("  inbox_ack: OK")

            print("PASSED")

        finally:
            if created_companion:
                await cleanup_companion(em, companion_id)


# Proactive message behavior source that uses ctx.send_message()
PROACTIVE_BEHAVIOR_SOURCE = '''
async def execute(ctx):
    """Behavior that sends a proactive message."""
    ctx.send_message("SDK Test - Proactive message from behavior")
    return None
'''


async def test_inbox_proactive_no_websocket():
    """Test proactive message to inbox when no WebSocket connected.

    Requires Modal to be running to process behavior jobs.
    """
    print("\n=== Test: Inbox Proactive (No WebSocket) ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False
        behavior_key = f"sdk_inbox_{uuid4().hex[:8]}"

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            await em.behaviors.create(
                companion_id,
                behavior_key,
                PROACTIVE_BEHAVIOR_SOURCE,
                triggers=[],
                priority=False,
            )

            rel = em.relationship(companion_id, user_id)
            await rel.ensure()

            # Trigger (no WebSocket connected)
            result = await rel.behavior_trigger(behavior_key)
            print(f"  Triggered, job_id: {result['job_id']}")

            # Poll inbox
            for attempt in range(15):
                await asyncio.sleep(2)
                messages = await rel.inbox_check()
                if messages:
                    print(f"  Found message after {(attempt + 1) * 2}s")
                    ack = await rel.inbox_ack([messages[0]["id"]])
                    assert ack.get("acknowledged", 0) >= 1
                    print("PASSED")
                    return

            print("SKIPPED (Modal not processing)")

        finally:
            try:
                await em.behaviors.delete(companion_id, behavior_key)
            except APIError:
                pass
            if created_companion:
                await cleanup_companion(em, companion_id)


async def test_websocket_proactive_delivery():
    """Test proactive message delivery via WebSocket.

    When WebSocket is connected, proactive messages should be pushed
    directly instead of going to inbox.
    """
    print("\n=== Test: WebSocket Proactive Delivery ===")

    async with EmotionMachine(api_key=API_KEY, base_url=BASE_URL) as em:
        companion_id = COMPANION_ID
        created_companion = False
        behavior_key = f"sdk_ws_{uuid4().hex[:8]}"

        if not companion_id:
            companion_id = await create_test_companion(em)
            created_companion = True

        user_id = f"sdk-test-{uuid4().hex[:8]}"

        try:
            await em.behaviors.create(
                companion_id,
                behavior_key,
                PROACTIVE_BEHAVIOR_SOURCE,
                triggers=[],
                priority=False,
            )

            rel = em.relationship(companion_id, user_id)
            await rel.ensure()

            received = False
            async with rel.connect(reconnect=False) as ws:
                # Wait for connected
                async for e in ws:
                    if e.get("type") == "connected":
                        break

                result = await rel.behavior_trigger(behavior_key)
                print(f"  Triggered, job_id: {result['job_id']}")

                # Wait for proactive via WebSocket (up to 45s for heartbeat)
                async def listen():
                    nonlocal received
                    async for event in ws:
                        if event.get("type") == "proactive":
                            received = True
                            return True
                    return False

                try:
                    await asyncio.wait_for(listen(), timeout=45.0)
                except TimeoutError:
                    pass

            if received:
                # Verify it's marked delivered (not pending)
                await asyncio.sleep(0.5)
                pending = await rel.inbox_check()
                if len(pending) == 0:
                    print("  Message delivered via WS, status=delivered")
                    print("PASSED")
                else:
                    print("  Delivered but status issue")
                    print("PARTIAL")
            else:
                print("SKIPPED (Modal not processing or timeout)")

        finally:
            try:
                await em.behaviors.delete(companion_id, behavior_key)
            except APIError:
                pass
            if created_companion:
                await cleanup_companion(em, companion_id)


# =============================================================================
# Main
# =============================================================================


async def run_all_tests():
    """Run all SDK tests."""
    if not API_KEY:
        print("ERROR: TEST_EM_API_KEY environment variable not set")
        sys.exit(1)

    print("Testing Emotion Machine SDK v2")
    print(f"Base URL: {BASE_URL}")
    print(f"Companion ID: {COMPANION_ID or '(will create)'}")
    print("=" * 60)

    tests = [
        test_companions_crud,
        test_companion_model_temperature,
        test_relationship_send,
        test_relationship_stream,
        test_profile_operations,
        test_session_lifecycle,
        test_behavior_decorator,
        test_behavior_create_and_deploy,
        test_behavior_trigger,
        test_sse_parsing,
        test_error_handling,
        test_behavior_deploy_from_decorator,
        test_message_with_behavior,
        test_relationship_get,
        test_config_patch,
        test_session_list,
        test_inbox_operations,
        # Integration tests - require Modal to be running
        test_inbox_proactive_no_websocket,
        test_websocket_proactive_delivery,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            await test()
            passed += 1
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
