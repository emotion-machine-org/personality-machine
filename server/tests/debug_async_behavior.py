"""Debug script to test async behavior enqueuing."""

import asyncio
import os
import sys
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")


def _headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


SIMPLE_BEHAVIOR = '''
async def execute(ctx):
    """Simple behavior that updates profile."""
    ctx.profile.set("debug_test.executed", True)
    ctx.profile.set("debug_test.turn", ctx.turn_count)
    return "Debug behavior executed"
'''


async def main():
    print("=== Debug Async Behavior Test ===")
    print(f"Base URL: {BASE_URL}")
    print(f"Companion ID: {COMPANION_ID}")
    print()

    behavior_key = "debug_async_behavior"
    user_id = f"test_debug_{uuid4().hex[:8]}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Delete existing behavior
        print("1. Cleaning up existing behavior...")
        await client.delete(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}",
            headers=_headers(),
        )

        # 2. Create async behavior (priority=False)
        print(f"2. Creating async behavior: {behavior_key}")
        response = await client.post(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors",
            headers=_headers(),
            json={
                "behavior_key": behavior_key,
                "triggers": ["always"],
                "priority": False,
                "enabled": True,
            },
        )
        if response.status_code not in (200, 201):
            print(f"   FAIL: {response.status_code} - {response.text}")
            return
        print(f"   Created: {response.json()}")

        # Update source code
        response = await client.patch(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}/definition",
            headers=_headers(),
            json={"source_code": SIMPLE_BEHAVIOR},
        )
        if response.status_code != 200:
            print(f"   FAIL updating source: {response.text}")
            return
        print("   Source code updated")

        # Verify the behavior
        response = await client.get(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}",
            headers=_headers(),
        )
        data = response.json()
        print(
            f"   Behavior config: triggers={data.get('triggers')}, priority={data.get('priority')}, enabled={data.get('enabled')}"
        )

        # 3. Create relationship
        print(f"\n3. Creating relationship for user: {user_id}")
        response = await client.put(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}",
            headers=_headers(),
        )
        rel_data = response.json()
        relationship_id = rel_data["id"]
        print(f"   Relationship ID: {relationship_id}")

        # 4. Send message
        print("\n4. Sending message to trigger behavior...")
        response = await client.post(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}/messages",
            headers=_headers(),
            json={"content": "Hello! Test async behavior."},
        )
        if response.status_code != 200:
            print(f"   FAIL: {response.status_code} - {response.text}")
            return
        msg_data = response.json()
        print(f"   Response: {msg_data.get('message', {}).get('content', '')[:80]}...")

        # 5. Wait and check profile
        print("\n5. Waiting for async behavior to execute...")
        for i in range(10):
            await asyncio.sleep(2)
            response = await client.get(
                f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}",
                headers=_headers(),
            )
            profile = response.json().get("profile", {})
            debug_test = profile.get("debug_test", {})
            print(f"   [{i * 2 + 2}s] profile.debug_test = {debug_test}")
            if debug_test.get("executed"):
                print("   SUCCESS: Async behavior executed!")
                break
        else:
            print("   FAIL: Async behavior did not execute within timeout")

        # 6. Cleanup
        print("\n6. Cleanup...")
        await client.delete(
            f"{BASE_URL}/v2/companions/{COMPANION_ID}/behaviors/{behavior_key}",
            headers=_headers(),
        )
        print("   Done")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID")
        sys.exit(1)
    asyncio.run(main())
