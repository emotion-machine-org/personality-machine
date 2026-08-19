"""Quick debug script to test behavior loading and trigger evaluation."""

import asyncio
import os
import sys
from uuid import UUID

# Add app to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID", "4d1f9e85-88b6-49a5-9e28-604bf3f6c583")
# Get a relationship ID from the test output
RELATIONSHIP_ID = None  # Will be set from command line or left None


async def main():
    from app.context.behavior_runtime import evaluate_behavior_triggers
    from app.db import get_db_connection
    from app.repositories.behavior_repository import BehaviorRepository

    companion_uuid = UUID(COMPANION_ID)
    relationship_uuid = UUID(RELATIONSHIP_ID) if RELATIONSHIP_ID else None

    async with get_db_connection() as conn:
        print(f"Companion ID: {companion_uuid}")
        print(f"Relationship ID: {relationship_uuid}")
        print()

        # Load behaviors
        behaviors = await BehaviorRepository.get_active_behaviors_for_companion(
            conn, companion_uuid, relationship_id=relationship_uuid
        )

        print(f"Loaded {len(behaviors)} behaviors:")
        for b in behaviors:
            print(f"  - {b['key']}")
            print(f"    triggers (shorthand): {b.get('triggers')}")
            print(f"    triggers_parsed: {b.get('triggers_parsed')}")
            print(f"    priority: {b.get('priority')}")
            print(f"    webhook_url: {b.get('webhook_url')}")
            print()

        # Test trigger evaluation
        print("\n=== Trigger Evaluation Test ===")
        test_message = "Hello! Testing async webhook."
        test_turn_count = 1
        test_keywords = ["hello", "testing", "async", "webhook"]

        for b in behaviors:
            # Use triggers_parsed (dict format) for evaluation
            behavior_for_eval = {
                **b,
                "triggers": b.get("triggers_parsed", []),
            }
            matched, source, details = evaluate_behavior_triggers(
                behavior_for_eval, test_message, test_turn_count, test_keywords
            )
            print(
                f"  {b['key']}: matched={matched}, source={source.value if matched else 'N/A'}, details={details}"
            )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        RELATIONSHIP_ID = sys.argv[1]

    asyncio.run(main())
