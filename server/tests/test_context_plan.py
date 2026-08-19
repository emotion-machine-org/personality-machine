import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

import asyncio

from app.context import build_context_plan
from app.context.context_hydrator import ContextHydrator
from app.db import init_db


class MockConfig:
    class Memory:
        enabled = False

    memory = Memory()
    layers = []
    context_mode = "layered"


async def test():
    pool = await init_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM companions LIMIT 1")
        if not row:
            print("No companions in DB")
            return

        companion_id = row["id"]

        # Test ContextHydrator directly first
        print("\n=== Testing ContextHydrator directly ===")
        try:
            hydrated = await ContextHydrator.hydrate(
                conn,
                companion_id=companion_id,
                conversation_id=None,
                external_user_id="test-user",
                use_cache=True,
            )
            print("ContextHydrator success!")
            print(f"  companion_config: {type(hydrated.companion_config)}")
            print(
                f"  core_system_prompt: {hydrated.core_system_prompt[:100]}..."
                if hydrated.core_system_prompt
                else "  core_system_prompt: (empty)"
            )
            print(f"  companion_state: {hydrated.companion_state}")
            print(f"  user_state: {hydrated.user_state}")
            print(f"  app_state: {hydrated.app_state}")
            print(f"  state_version: {hydrated.state_version}")
            print(f"  conversation_state: {hydrated.conversation_state}")
            print(f"  history: {len(hydrated.history)} messages")
        except Exception as e:
            print(f"ContextHydrator error: {e}")
            import traceback

            traceback.print_exc()

        # Now test build_context_plan
        print("\n=== Testing build_context_plan ===")
        plan = await build_context_plan(
            conn=conn,
            companion_id=companion_id,
            companion_config=MockConfig(),
            conversation_id=None,
            user_message="Hello",
            external_user_id="test-user",
            hydrate_state=True,
        )

        print(f"Plan built: {len(plan.messages)} messages")
        print(f"Trace: hydrated={plan.trace.get('hydrated')}")
        print(f"Trace: user_state_version={plan.trace.get('user_state_version')}")
        print(f"Trace: hydrate_ms={plan.trace.get('hydrate_ms')}")
        print(f"Trace: hydrated_context present={plan.trace.get('hydrated_context') is not None}")
        print(f"Effects: {len(plan.effects)}")
        # Check for any error events
        for ev in plan.events:
            if "error" in ev.phase:
                print(f"  Error event: {ev.name} - {ev.meta}")


if __name__ == "__main__":
    asyncio.run(test())
