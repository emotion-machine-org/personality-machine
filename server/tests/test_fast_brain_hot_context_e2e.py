#!/usr/bin/env python3
"""E2E sanity test for Fast Brain → OpenClaw → hot_context.

Run:
  cd server
  uv run python tests/test_fast_brain_hot_context_e2e.py

Requires:
  - OPENCLAW_WEBHOOK_URL (and optionally OPENCLAW_AUTH_TOKEN)
  - KNOWLEDGE_S3_BUCKET (+ AWS creds) for S3-backed hot_context
  - EM server reachable by OpenClaw plugin (for em_voice_task callbacks)
"""

from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

import httpx

from app.routers.voice.fast_brain_llm import FastBrainConfig, FastBrainLLMService
from app.routers.voice.voice_workspace import HotContextS3


async def main() -> None:
    webhook_url = os.environ.get("OPENCLAW_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("Missing OPENCLAW_WEBHOOK_URL. Aborting.")
        return

    base_url = os.environ.get("EM_API_BASE_URL", "http://localhost:8100").rstrip("/")
    api_key = os.environ.get("EM_API_KEY", "").strip()
    companion_id = os.environ.get(
        "TEST_COMPANION_ID", "f5a93f4d-4d62-4f5f-92b0-3e447a80cd0c"
    ).strip()
    user_id = os.environ.get("TEST_USER_ID") or f"test-user-{uuid4().hex[:8]}"

    if not api_key:
        print("Missing EM_API_KEY. Aborting.")
        return

    relationship_id = os.environ.get("TEST_RELATIONSHIP_ID")
    task_id = str(uuid4())
    timeout_s = int(os.environ.get("TEST_TIMEOUT_SECONDS", "90"))

    print(f"EM_API_BASE_URL: {base_url}")
    print(f"Companion ID: {companion_id}")
    print(f"User ID: {user_id}")
    print(f"Task id: {task_id}")
    print(f"OPENCLAW_WEBHOOK_URL: {webhook_url}")
    print(f"Timeout: {timeout_s}s")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Create relationship (or reuse existing if TEST_RELATIONSHIP_ID set)
        if not relationship_id:
            create_url = f"{base_url}/v2/companions/{companion_id}/relationships/{user_id}"
            resp = await client.put(
                create_url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={},
            )
            resp.raise_for_status()
            relationship_id = resp.json()["id"]
            print(f"Created relationship_id: {relationship_id}")
        else:
            print(f"Using relationship_id: {relationship_id}")

        ctx = HotContextS3(relationship_id)
        try:
            ctx.workspace.delete("hot_context.md")
        except Exception:
            pass

        fast = FastBrainLLMService(
            config=FastBrainConfig.from_env(),
            relationship_id=relationship_id,
            companion_id=companion_id,
            user_id=user_id,
        )

        try:
            # Log start + ack locally (matches Fast Brain behavior on delegation)
            fast._log_task_event("start", task_id, "Quick test: reply OK")
            fast._log_task_event("ack", task_id, "Testing ack")

            user_message = "Please reply with the single word OK."
            task_description = (
                "Reply with the single word OK. Then call em_voice_task with action done "
                f'and result "OK". relationshipId: "{relationship_id}"'
            )

            print("Delegating to OpenClaw...")
            ok = await fast._delegate_to_openclaw(task_id, user_message, task_description)
            if not ok:
                print("OpenClaw delegation failed.")
                return

            print("Waiting for hot_context update from OpenClaw...")
            start = time.time()
            while time.time() - start < timeout_s:
                result = ctx.get_task_result(task_id)
                if result:
                    status, data = result
                    if status in ("done", "failed"):
                        print(f"Task status: {status}")
                        print(f"Result/Error: {data}")
                        break
                await asyncio.sleep(3)
            else:
                print("Timed out waiting for task completion.")
        finally:
            await fast.close()
            # Best-effort cleanup: delete relationship
            if os.environ.get("TEST_RELATIONSHIP_ID") is None and relationship_id:
                delete_url = f"{base_url}/v2/relationships/{relationship_id}"
                try:
                    resp = await client.delete(
                        delete_url, headers={"Authorization": f"Bearer {api_key}"}
                    )
                    if resp.status_code == 204:
                        print("Deleted relationship.")
                    else:
                        print(f"Relationship delete returned {resp.status_code}")
                except Exception as e:
                    print(f"Failed to delete relationship: {e}")


if __name__ == "__main__":
    asyncio.run(main())
