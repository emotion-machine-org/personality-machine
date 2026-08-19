"""Integration tests for v2 message history endpoints.

Run with:
    uv run python tests/test_v2_message_history.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID, uuid4

import asyncpg
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = (
    os.getenv("EM_BASE_URL") or os.getenv("EM_API_BASE_URL") or "http://localhost:8100"
).rstrip("/")
API_KEY = os.getenv("TEST_EM_API_KEY")
DATABASE_DSN = os.getenv("DATABASE_DSN")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _create_temp_companion(client: httpx.Client) -> str:
    response = client.post(
        f"{BASE_URL}/v1/companions",
        headers=_headers(),
        json={"name": f"history-test-{uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, (
        f"Failed to create companion: {response.status_code} {response.text}"
    )
    return response.json()["id"]


def _delete_companion(client: httpx.Client, companion_id: str | None) -> None:
    if not companion_id:
        return
    client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())


def _delete_relationship(client: httpx.Client, relationship_id: str | None) -> None:
    if not relationship_id:
        return
    client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())


def _create_relationship(client: httpx.Client, companion_id: str, user_id: str) -> str:
    response = client.put(
        f"{BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}",
        headers=_headers(),
        json={},
    )
    assert response.status_code == 200, (
        f"Failed to create relationship: {response.status_code} {response.text}"
    )
    return response.json()["id"]


async def _insert_seed_messages(relationship_id: UUID) -> dict[str, int]:
    conn = await asyncpg.connect(DATABASE_DSN)
    try:

        async def insert(
            *,
            role: str,
            content: str,
            is_proactive: bool = False,
            delivery_status: str | None = None,
        ) -> int:
            seq = await conn.fetchval(
                "SELECT next_relationship_message_seq($1)",
                relationship_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO messages (
                    id,
                    relationship_id,
                    role,
                    content,
                    seq,
                    is_proactive,
                    delivery_status,
                    input_modality
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'text')
                RETURNING seq
                """,
                uuid4(),
                relationship_id,
                role,
                content,
                seq,
                is_proactive,
                delivery_status,
            )
            return int(row["seq"])

        seq_1 = await insert(role="assistant", content="seed assistant #1")
        seq_2 = await insert(role="user", content="seed user #1")
        seq_3 = await insert(
            role="assistant",
            content="seed proactive #1",
            is_proactive=True,
            delivery_status="delivered",
        )
        seq_4 = await insert(role="assistant", content="seed assistant #2")

        return {
            "seq_1": seq_1,
            "seq_2": seq_2,
            "seq_3": seq_3,
            "seq_4": seq_4,
        }
    finally:
        await conn.close()


def test_message_history_endpoints() -> None:
    companion_id: str | None = None
    relationship_id: str | None = None

    with httpx.Client(timeout=30.0) as client:
        try:
            companion_id = _create_temp_companion(client)
            user_id = f"history-user-{uuid4().hex[:8]}"
            relationship_id = _create_relationship(client, companion_id, user_id)
            seqs = asyncio.run(_insert_seed_messages(UUID(relationship_id)))

            # Latest-window mode (limit only)
            latest = client.get(
                f"{BASE_URL}/v2/relationships/{relationship_id}/messages",
                headers=_headers(),
                params={"limit": 2},
            )
            assert latest.status_code == 200, latest.text
            latest_data = latest.json()

            assert latest_data["relationship_id"] == relationship_id
            assert latest_data["has_more"] is True
            assert latest_data["oldest_seq"] == seqs["seq_3"]
            assert latest_data["newest_seq"] == seqs["seq_4"]
            assert [m["seq"] for m in latest_data["messages"]] == [
                seqs["seq_3"],
                seqs["seq_4"],
            ]

            # Backward pagination (before_seq)
            before = client.get(
                f"{BASE_URL}/v2/relationships/{relationship_id}/messages",
                headers=_headers(),
                params={"limit": 2, "before_seq": seqs["seq_3"]},
            )
            assert before.status_code == 200, before.text
            before_data = before.json()
            assert before_data["has_more"] is False
            assert [m["seq"] for m in before_data["messages"]] == [
                seqs["seq_1"],
                seqs["seq_2"],
            ]

            # Forward pagination (after_seq)
            after = client.get(
                f"{BASE_URL}/v2/relationships/{relationship_id}/messages",
                headers=_headers(),
                params={"limit": 10, "after_seq": seqs["seq_2"]},
            )
            assert after.status_code == 200, after.text
            after_data = after.json()
            assert after_data["has_more"] is False
            assert [m["seq"] for m in after_data["messages"]] == [
                seqs["seq_3"],
                seqs["seq_4"],
            ]

            # Companion+user endpoint should resolve the same relationship and include proactive
            by_user = client.get(
                f"{BASE_URL}/v2/companions/{companion_id}/relationships/{user_id}/messages",
                headers=_headers(),
                params={"limit": 10},
            )
            assert by_user.status_code == 200, by_user.text
            by_user_data = by_user.json()
            assert by_user_data["relationship_id"] == relationship_id
            assert [m["seq"] for m in by_user_data["messages"]] == [
                seqs["seq_1"],
                seqs["seq_2"],
                seqs["seq_3"],
                seqs["seq_4"],
            ]

            proactive = [m for m in by_user_data["messages"] if m["is_proactive"]]
            assert len(proactive) == 1, f"Expected one proactive message, got {len(proactive)}"
            assert proactive[0]["content"] == "seed proactive #1"

            # Invalid cursor combination
            invalid = client.get(
                f"{BASE_URL}/v2/relationships/{relationship_id}/messages",
                headers=_headers(),
                params={"before_seq": seqs["seq_4"], "after_seq": seqs["seq_1"]},
            )
            assert invalid.status_code == 400, invalid.text
        finally:
            _delete_relationship(client, relationship_id)
            _delete_companion(client, companion_id)

    print("✓ test_message_history_endpoints")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not DATABASE_DSN:
        print("ERROR: Set DATABASE_DSN environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}\n")
    test_message_history_endpoints()
    print("\n✅ v2 message history tests passed!")
