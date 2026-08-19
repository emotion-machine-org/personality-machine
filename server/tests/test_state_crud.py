import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

import asyncio
from uuid import uuid4

from app.db import init_db
from app.repositories.state_repository import StateRepository


async def test():
    pool = await init_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM companions LIMIT 1")
        if not row:
            print("No companions in DB - create one first")
            return

        companion_id = row["id"]
        test_user = f"test-user-{uuid4().hex[:8]}"

        # Test state creation
        state = await StateRepository.get_or_create_user_state(
            conn, companion_id=companion_id, external_user_id=test_user
        )
        print(f"Created state: version={state.version}, profile={state.profile}")

        # Test patching
        patched = await StateRepository.patch_user_state(
            conn,
            companion_id=companion_id,
            external_user_id=test_user,
            patches=[{"key": "test_key", "value": "test_value"}],
        )
        print(f"Patched state: version={patched.version}, profile={patched.profile}")

        # Cleanup
        await StateRepository.delete_user_state(
            conn, companion_id=companion_id, external_user_id=test_user
        )
        print("Cleaned up test state")


if __name__ == "__main__":
    asyncio.run(test())
