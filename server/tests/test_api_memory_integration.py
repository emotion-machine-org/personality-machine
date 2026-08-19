"""
Integration tests for API memory registration functionality.

Tests that memories are properly registered when using the /v1/ API endpoints,
matching the behavior of the dashboard conversation flow.

Requirements:
- TEST_EM_API_KEY environment variable must be set
- EM_BASE_URL environment variable (defaults to http://localhost:8100)
- Server must be running with Modal memory worker configured
"""

import json
import os
import time
import uuid
from http import HTTPStatus

import httpx
import pytest
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")

# Skip all tests if API key is not set
pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="Set TEST_EM_API_KEY to run API memory integration tests.",
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


class TestAPIMemoryIntegration:
    """
    End-to-end test for memory registration via API.

    Flow:
    1. Create a companion with memory enabled
    2. Start a conversation
    3. Send a message that should create a memory (e.g., "Remember my name is TestUser")
    4. Wait for memory ingestion to complete
    5. Send a follow-up message to verify memory retrieval
    6. Clean up by deleting the test companion
    """

    @pytest.fixture(scope="class")
    def test_companion(self):
        """Create a test companion with memory enabled, clean up after tests."""
        companion_id = None
        unique_id = uuid.uuid4().hex[:8]

        with httpx.Client(timeout=30.0) as client:
            # Create companion with memory enabled
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Memory Test Companion {unique_id}",
                "description": "Test companion for memory API integration tests",
                "config": {
                    "system_prompt": {
                        "full_system_prompt": "You are a helpful assistant with memory capabilities. When users share personal information, acknowledge it naturally. When asked about previously shared information, recall it accurately."
                    },
                    "memory": {
                        "enabled": True,
                        "core_memories": [],
                        "memory_evaluation_prompt": "Evaluate if this message contains personal information worth remembering.",
                        "recency": 0.995,
                        "top_k": 50,
                        "min_saliency": 0.2,
                    },
                },
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED, (
                f"Failed to create companion: {response.text}"
            )
            companion_data = response.json()
            companion_id = companion_data["id"]

            yield {
                "id": companion_id,
                "name": companion_data["name"],
                "unique_id": unique_id,
            }

            # Cleanup: delete the companion after tests
            if companion_id:
                delete_url = f"{BASE_URL}/v1/companions/{companion_id}"
                try:
                    client.delete(delete_url, headers=_headers())
                except Exception:
                    pass  # Best effort cleanup

    def test_memory_registration_via_sync_chat(self, test_companion):
        """
        Test that memories are registered when using the synchronous chat endpoint.
        """
        companion_id = test_companion["id"]
        unique_id = test_companion["unique_id"]
        external_user_id = f"test-user-{unique_id}"

        # Use a unique name to verify memory retrieval later
        test_name = f"MemoryTestUser_{unique_id}"

        with httpx.Client(timeout=60.0) as client:
            chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"

            # Step 1: Send a message that should create a memory
            memory_message = f"Please remember that my name is {test_name} and I really love hiking in the mountains."

            response = client.post(
                chat_url,
                headers=_headers(),
                json={
                    "external_user_id": external_user_id,
                    "message": memory_message,
                    "model": "openai-gpt4o-mini",
                },
            )
            assert response.status_code == HTTPStatus.OK, f"Chat request failed: {response.text}"
            first_response = response.json()
            # conversation_id is nested in choices[0].emotion_machine.metadata
            conversation_id = (
                first_response.get("choices", [{}])[0]
                .get("emotion_machine", {})
                .get("metadata", {})
                .get("conversation_id")
            )
            assert conversation_id, f"No conversation_id returned. Response: {first_response}"

            # Step 2: Wait for memory ingestion to complete
            # Memory ingestion is async via Modal, so we need to wait
            print("\nWaiting for memory ingestion (15 seconds)...")
            time.sleep(15)

            # Step 3: Send a follow-up message to verify memory retrieval
            recall_message = "What is my name and what do I like to do?"

            response = client.post(
                chat_url,
                headers=_headers(),
                json={
                    "external_user_id": external_user_id,
                    "conversation_id": conversation_id,
                    "message": recall_message,
                    "model": "openai-gpt4o-mini",
                },
            )
            assert response.status_code == HTTPStatus.OK, f"Recall request failed: {response.text}"
            recall_response = response.json()

            # Verify the assistant recalls the information
            assistant_content = recall_response["choices"][0]["message"]["content"].lower()

            # Check if the response contains our unique test name
            # (case-insensitive check)
            assert (
                test_name.lower() in assistant_content or "memorytestuser" in assistant_content
            ), (
                f"Expected assistant to recall the name '{test_name}', "
                f"but got: {assistant_content[:500]}"
            )

            print("\n✓ Memory registration test passed!")
            print(f"  - Companion ID: {companion_id}")
            print(f"  - Conversation ID: {conversation_id}")
            print(f"  - Test name: {test_name}")
            print(f"  - Assistant recalled: {assistant_content[:200]}...")

    def test_memory_registration_via_stream_chat(self, test_companion):
        """
        Test that memories are registered when using the streaming chat endpoint.
        """
        companion_id = test_companion["id"]
        unique_id = test_companion["unique_id"]
        external_user_id = f"test-stream-user-{unique_id}"

        # Use a unique favorite color to verify memory retrieval
        test_color = f"turquoise_{unique_id[:4]}"

        with httpx.Client(timeout=60.0) as client:
            stream_url = f"{BASE_URL}/v1/companions/{companion_id}/chat/stream"
            headers = _headers()
            headers["Accept"] = "text/event-stream"

            # Step 1: Send a message that should create a memory (streaming)
            memory_message = (
                f"Remember that my favorite color is {test_color} and I collect vintage watches."
            )

            conversation_id = None
            first_response_content = []

            with client.stream(
                "POST",
                stream_url,
                headers=headers,
                json={
                    "external_user_id": external_user_id,
                    "message": memory_message,
                    "model": "openai-gpt4o-mini",
                },
            ) as response:
                assert response.status_code == HTTPStatus.OK, (
                    f"Stream request failed: {response.status_code}"
                )

                current_event = None
                for raw_line in response.iter_lines():
                    if raw_line is None:
                        continue
                    line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
                    if not line:
                        continue

                    if line.startswith("event:"):
                        current_event = line.split("event:")[1].strip()
                    elif line.startswith("data:") and current_event:
                        data = json.loads(line.split("data:")[1].strip())

                        if current_event == "ack":
                            conversation_id = data.get("conversation_id")
                        elif current_event == "delta":
                            content = (
                                data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            )
                            if content:
                                first_response_content.append(content)
                        elif current_event == "done":
                            break

            assert conversation_id, "No conversation_id from stream"

            # Step 2: Wait for memory ingestion
            print("\nWaiting for memory ingestion (15 seconds)...")
            time.sleep(15)

            # Step 3: Send a follow-up message (can use sync endpoint)
            chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"
            recall_message = "What is my favorite color and what do I collect?"

            response = client.post(
                chat_url,
                headers=_headers(),
                json={
                    "external_user_id": external_user_id,
                    "conversation_id": conversation_id,
                    "message": recall_message,
                    "model": "openai-gpt4o-mini",
                },
            )
            assert response.status_code == HTTPStatus.OK, f"Recall request failed: {response.text}"
            recall_response = response.json()

            assistant_content = recall_response["choices"][0]["message"]["content"].lower()

            # Check if the response mentions turquoise or watches
            has_color = "turquoise" in assistant_content or test_color.lower() in assistant_content
            has_hobby = "watch" in assistant_content

            assert has_color or has_hobby, (
                f"Expected assistant to recall color '{test_color}' or 'watches', "
                f"but got: {assistant_content[:500]}"
            )

            print("\n✓ Streaming memory registration test passed!")
            print(f"  - Companion ID: {companion_id}")
            print(f"  - Conversation ID: {conversation_id}")
            print(f"  - Test color: {test_color}")
            print(f"  - Assistant recalled: {assistant_content[:200]}...")


class TestCompanionMemoryConfig:
    """Test that companion memory configuration works correctly via API."""

    def test_create_companion_with_memory_enabled(self):
        """Test creating a companion with memory enabled via API."""
        unique_id = uuid.uuid4().hex[:8]

        with httpx.Client(timeout=30.0) as client:
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Memory Config Test {unique_id}",
                "config": {
                    "system_prompt": {"full_system_prompt": "You are a test assistant."},
                    "memory": {
                        "enabled": True,
                    },
                },
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED, f"Failed: {response.text}"

            companion_data = response.json()
            companion_id = companion_data["id"]

            # Verify memory is enabled
            get_url = f"{BASE_URL}/v1/companions/{companion_id}"
            response = client.get(get_url, headers=_headers())
            assert response.status_code == HTTPStatus.OK

            config = response.json().get("config", {})
            memory_config = config.get("memory", {})
            assert memory_config.get("enabled") is True, "Memory should be enabled"

            # Cleanup
            client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())

            print("\n✓ Companion memory config test passed!")

    def test_update_companion_memory_config(self):
        """Test updating a companion's memory configuration via API."""
        unique_id = uuid.uuid4().hex[:8]

        with httpx.Client(timeout=30.0) as client:
            # Create companion with memory disabled
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Memory Update Test {unique_id}",
                "config": {
                    "system_prompt": {"full_system_prompt": "You are a test assistant."},
                    "memory": {
                        "enabled": False,
                    },
                },
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED
            companion_id = response.json()["id"]

            # Update to enable memory
            update_url = f"{BASE_URL}/v1/companions/{companion_id}"
            update_payload = {
                "config": {
                    "system_prompt": {
                        "full_system_prompt": "You are a test assistant with memory."
                    },
                    "memory": {
                        "enabled": True,
                        "memory_evaluation_prompt": "Remember important facts.",
                    },
                }
            }

            response = client.patch(update_url, headers=_headers(), json=update_payload)
            assert response.status_code == HTTPStatus.OK, f"Update failed: {response.text}"

            # Verify memory is now enabled
            response = client.get(update_url, headers=_headers())
            config = response.json().get("config", {})
            memory_config = config.get("memory", {})
            assert memory_config.get("enabled") is True, "Memory should now be enabled"

            # Cleanup
            client.delete(update_url, headers=_headers())

            print("\n✓ Companion memory update test passed!")


if __name__ == "__main__":
    # Allow running directly for quick manual testing
    pytest.main([__file__, "-v", "-s"])
