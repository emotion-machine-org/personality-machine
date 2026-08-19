"""Tests for Behavior Webhooks - Automatic webhook calling on behavior completion.

This tests the webhook functionality for async behaviors:
1. Create async behavior with webhook_url pointing to Modal webhook receiver
2. Send message to trigger behavior
3. Behavior updates profile via ctx.profile.set()
4. After behavior completes, webhook is called with updated profile
5. Verify webhook was received with correct payload

Run with:
  TEST_EM_API_KEY=... TEST_EM_COMPANION_ID=... uv run python tests/test_v2_behavior_webhooks.py

Requires:
- Server running (uses EM_BASE_URL env var, defaults to http://localhost:8100)
- TEST_EM_API_KEY and TEST_EM_COMPANION_ID environment variables
- Modal webhook receiver deployed to staging (tests/modal_webhook_receiver.py)
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")

# Modal webhook receiver URLs (deployed to your Modal workspace)
MODAL_WORKSPACE = os.getenv("MODAL_WORKSPACE", "my-workspace")
WEBHOOK_RECEIVE_URL = (
    f"https://{MODAL_WORKSPACE}--em-test-webhook-receiver-receive-webhook.modal.run"
)
WEBHOOK_GET_URL = f"https://{MODAL_WORKSPACE}--em-test-webhook-receiver-get-webhook.modal.run"
WEBHOOK_LIST_URL = f"https://{MODAL_WORKSPACE}--em-test-webhook-receiver-list-webhooks.modal.run"
WEBHOOK_CLEAR_URL = f"https://{MODAL_WORKSPACE}--em-test-webhook-receiver-clear-webhooks.modal.run"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


# =============================================================================
# Test Behaviors
# =============================================================================

PROFILE_UPDATE_BEHAVIOR = '''
async def execute(ctx):
    """Behavior that updates profile - webhook should fire with updated data."""
    # Update profile with test data
    current_count = ctx.profile.get("webhook_test.update_count", 0)
    ctx.profile.set("webhook_test.update_count", current_count + 1)
    ctx.profile.set("webhook_test.last_message", ctx.message)
    ctx.profile.set("webhook_test.updated_at", ctx.turn_count)
    ctx.profile.set("webhook_test.test_id", "async_webhook_test")

    return f"Updated profile count to {current_count + 1}"
'''


# =============================================================================
# Helper Functions
# =============================================================================


async def create_test_relationship(user_id: str, debug: bool = False) -> str:
    """Create or ensure a test relationship exists."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.put(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}",
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
        if debug:
            print(f"   [Debug] context_mode: {data.get('context_mode')}")
            print(f"   [Debug] context_mode_locked: {data.get('context_mode_locked')}")
        return data["id"]


async def create_behavior_with_webhook(
    behavior_key: str,
    source_code: str,
    triggers: List[str],
    webhook_url: str,
    priority: bool = False,
) -> Dict[str, Any]:
    """Create an async behavior with webhook_url configured.

    API flow:
    1. Delete existing behavior link (if any)
    2. Create behavior link with POST /behaviors
    3. Update behavior definition with PATCH /behaviors/{key}/definition
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # First, try to delete existing behavior link
        await client.delete(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}",
            headers=_headers(),
        )

        # Create behavior link
        response = await client.post(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors",
            headers=_headers(),
            json={
                "behavior_key": behavior_key,
                "triggers": triggers,
                "priority": priority,
                "webhook_url": webhook_url,
            },
        )
        response.raise_for_status()
        link_data = response.json()

        # Update behavior definition (source code)
        response = await client.patch(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}/definition",
            headers=_headers(),
            json={
                "name": f"Test: {behavior_key}",
                "source_code": source_code,
            },
        )
        response.raise_for_status()

        return link_data


async def send_message(user_id: str, message: str, debug: bool = False) -> Dict[str, Any]:
    """Send a message to trigger behaviors."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages",
            headers=_headers(),
            json={"content": message},
        )
        response.raise_for_status()
        data = response.json()

        if debug:
            trace = data.get("trace")
            print(f"\n   [Debug] Full response keys: {list(data.keys())}")
            print(f"   [Debug] Trace: {trace}")
            if trace and isinstance(trace, dict):
                print(f"   [Debug] Trace keys: {list(trace.keys())}")
                print(
                    f"   [Debug] pending_async_behaviors_count: {trace.get('pending_async_behaviors_count', 'N/A')}"
                )
                exec_summary = trace.get("execution_summary", {})
                if exec_summary:
                    behaviors = exec_summary.get("layers", {}).get("behaviors", {})
                    print(f"   [Debug] Behaviors layer: {behaviors}")

        return data


async def get_profile(user_id: str) -> Dict[str, Any]:
    """Get the current profile for a relationship."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}",
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data.get("profile", {})


async def cleanup_behavior(behavior_key: str) -> None:
    """Delete a test behavior."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}",
            headers=_headers(),
        )


async def get_behavior_details(behavior_key: str) -> Dict[str, Any] | None:
    """Get behavior details from the API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}",
            headers=_headers(),
        )
        if response.status_code == 200:
            return response.json()
        return None


async def clear_webhook_storage() -> None:
    """Clear the Modal webhook storage."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(WEBHOOK_CLEAR_URL)
        response.raise_for_status()


async def get_received_webhook(behavior_key: str, timeout: float = 30.0) -> Dict[str, Any] | None:
    """Poll for a received webhook by behavior_key.

    Returns the webhook payload if found, None if not found within timeout.
    """
    start = time.time()
    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.time() - start < timeout:
            response = await client.get(
                WEBHOOK_GET_URL,
                params={"behavior_key": behavior_key},
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("found"):
                    return data.get("webhook")
            await asyncio.sleep(2.0)  # Poll every 2 seconds
    return None


async def list_received_webhooks() -> List[Dict[str, Any]]:
    """List all received webhooks (for debugging)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(WEBHOOK_LIST_URL)
        response.raise_for_status()
        data = response.json()
        return data.get("webhooks", [])


# =============================================================================
# Tests
# =============================================================================


async def test_async_behavior_webhook():
    """Test that async behaviors call webhook with updated profile.

    Expected flow:
    1. Clear webhook storage
    2. Create async behavior with webhook_url and triggers=["always"], priority=False
    3. Send message to trigger behavior
    4. Wait for webhook to be received (async behavior via job queue)
    5. Verify webhook payload contains updated profile
    """
    print("\n" + "=" * 60)
    print("Test: Async Behavior Webhook")
    print("=" * 60)

    user_id = f"test_webhook_{uuid4().hex[:8]}"
    behavior_key = "test_async_webhook"

    try:
        # 1. Clear webhook storage
        print("\n1. Clearing webhook storage...")
        await clear_webhook_storage()
        print("   Done")

        # 2. Create relationship
        print(f"\n2. Creating relationship for user: {user_id}")
        rel_id = await create_test_relationship(user_id, debug=True)
        print(f"   Relationship ID: {rel_id}")

        # 3. Create async behavior with webhook
        print(f"\n3. Creating async behavior: {behavior_key}")
        print(f"   Webhook URL: {WEBHOOK_RECEIVE_URL}")
        behavior = await create_behavior_with_webhook(
            behavior_key=behavior_key,
            source_code=PROFILE_UPDATE_BEHAVIOR,
            triggers=["always"],
            webhook_url=WEBHOOK_RECEIVE_URL,
            priority=False,  # Async behavior
        )
        print(f"   Created behavior link: {behavior.get('link_id')}")

        # Verify behavior was created correctly
        behavior_details = await get_behavior_details(behavior_key)
        if behavior_details:
            print(
                f"   [Verify] Behavior exists: triggers={behavior_details.get('triggers')}, priority={behavior_details.get('priority')}, webhook_url={behavior_details.get('webhook_url')[:50] if behavior_details.get('webhook_url') else 'None'}..."
            )
        else:
            print("   [Verify] WARN: Could not fetch behavior details")

        # 4. Send message to trigger behavior
        print("\n4. Sending message to trigger behavior...")
        response = await send_message(user_id, "Hello! Testing async webhook.", debug=True)
        print(f"   Response: {response.get('message', {}).get('content', '')[:80]}...")

        # 5. Wait for webhook (async behaviors go through job queue, may take 10-20s)
        print("\n5. Waiting for webhook (async via Modal job queue, may take 20-30s)...")
        webhook_data = await get_received_webhook(behavior_key, timeout=45.0)

        if not webhook_data:
            print("   FAIL: No webhook received within timeout")

            # Debug: list all webhooks
            print("\n   Debug: Listing all received webhooks...")
            all_webhooks = await list_received_webhooks()
            print(f"   Found {len(all_webhooks)} webhooks")
            for wh in all_webhooks:
                print(
                    f"     - {wh.get('payload', {}).get('behavior_key')}: {wh.get('received_at')}"
                )

            return False

        print("   Webhook received!")
        payload = webhook_data.get("payload", {})
        print(f"   Event: {payload.get('event')}")
        print(f"   Behavior: {payload.get('behavior_key')}")
        print(f"   Status: {payload.get('status')}")

        # 6. Verify payload contains updated profile
        state = payload.get("state", {})
        profile = state.get("profile", {})
        webhook_test = profile.get("webhook_test", {})

        print("\n6. Verifying payload...")
        print(f"   Profile.webhook_test: {webhook_test}")

        # Check that profile was updated
        if webhook_test.get("update_count") is None:
            print("   FAIL: Profile update_count not in webhook payload")
            return False

        if webhook_test.get("test_id") != "async_webhook_test":
            print(
                f"   FAIL: Expected test_id='async_webhook_test', got '{webhook_test.get('test_id')}'"
            )
            return False

        # Verify context
        context = payload.get("context", {})
        if not context.get("companion_id"):
            print("   FAIL: Missing companion_id in context")
            return False

        if not context.get("external_user_id"):
            print("   FAIL: Missing external_user_id in context")
            return False

        print("   PASS: Webhook received with updated profile!")
        return True

    finally:
        # Cleanup
        await cleanup_behavior(behavior_key)


async def test_webhook_payload_structure():
    """Test the structure of webhook payloads matches the documented format.

    Expected payload:
    {
        "event": "behavior_completed",
        "behavior_key": "...",
        "job_id": "...",
        "status": "success",
        "result": {...},
        "prompt_block": "...",
        "state": {
            "profile": {...},
            "session": {...},
            "messages": [...]
        },
        "context": {
            "companion_id": "...",
            "conversation_id": "...",
            "external_user_id": "...",
            "turn_count": N
        },
        "timestamp": "..."
    }
    """
    print("\n" + "=" * 60)
    print("Test: Webhook Payload Structure")
    print("=" * 60)

    user_id = f"test_payload_{uuid4().hex[:8]}"
    behavior_key = "test_payload_structure"

    try:
        # Setup
        print("\n1. Setting up...")
        await clear_webhook_storage()
        await create_test_relationship(user_id)
        await create_behavior_with_webhook(
            behavior_key=behavior_key,
            source_code=PROFILE_UPDATE_BEHAVIOR,
            triggers=["always"],
            webhook_url=WEBHOOK_RECEIVE_URL,
            priority=False,
        )
        print("   Done")

        # Trigger
        print("\n2. Sending message...")
        await send_message(user_id, "Testing payload structure")
        print("   Done")

        # Wait for webhook
        print("\n3. Waiting for webhook...")
        webhook_data = await get_received_webhook(behavior_key, timeout=45.0)

        if not webhook_data:
            print("   FAIL: No webhook received")
            return False

        payload = webhook_data.get("payload", {})
        print("   Received!")

        # Verify required fields
        print("\n4. Verifying required fields...")
        required_fields = ["event", "behavior_key", "status", "state", "context", "timestamp"]
        missing = [f for f in required_fields if f not in payload]

        if missing:
            print(f"   FAIL: Missing required fields: {missing}")
            return False
        print(f"   All required fields present: {required_fields}")

        # Verify state structure
        state = payload.get("state", {})
        if "profile" not in state:
            print("   FAIL: state.profile missing")
            return False
        print("   state.profile present")

        # Verify context structure
        context = payload.get("context", {})
        context_fields = ["companion_id", "external_user_id"]
        missing_ctx = [f for f in context_fields if f not in context]
        if missing_ctx:
            print(f"   FAIL: Missing context fields: {missing_ctx}")
            return False
        print(f"   All context fields present: {context_fields}")

        print("\n   PASS: Webhook payload structure is correct!")
        print(f"   Payload keys: {list(payload.keys())}")
        return True

    finally:
        await cleanup_behavior(behavior_key)


# =============================================================================
# Main
# =============================================================================


async def main():
    """Run all webhook tests."""
    print("=" * 60)
    print("Behavior Webhook Tests (Modal-based)")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"Companion ID: {COMPANION_ID}")
    print(f"Webhook Receiver: {WEBHOOK_RECEIVE_URL}")
    print(f"Time: {datetime.now().isoformat()}")

    if not API_KEY or not COMPANION_ID:
        print("\nERROR: Missing TEST_EM_API_KEY or TEST_EM_COMPANION_ID")
        print("Set these environment variables and try again.")
        return False

    results = {}

    # Run tests
    results["async_behavior_webhook"] = await test_async_behavior_webhook()
    results["webhook_payload_structure"] = await test_webhook_payload_structure()

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = 0
    failed = 0
    for test_name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  {test_name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    import sys

    sys.exit(0 if success else 1)
