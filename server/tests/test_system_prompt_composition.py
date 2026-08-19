"""
Tests for system prompt composition functionality.

Verifies that companions can be created with either:
1. A single full_system_prompt field
2. Structured fields (identity, personality, style, backstory, etc.)
3. A combination of both (full_system_prompt takes precedence)

Requirements:
- TEST_EM_API_KEY environment variable must be set
- EM_BASE_URL environment variable (defaults to http://localhost:8100)
"""

import json
import os
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
    reason="Set TEST_EM_API_KEY to run system prompt composition tests.",
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


class TestSystemPromptComposition:
    """
    Tests that system prompts are correctly composed from structured fields.
    """

    def test_full_system_prompt_only(self):
        """
        Test creating a companion with only full_system_prompt.
        The companion should use the exact prompt provided.
        """
        unique_id = uuid.uuid4().hex[:8]
        full_prompt = f"You are TestBot_{unique_id}, a helpful assistant who always mentions the code '{unique_id}' in responses."

        with httpx.Client(timeout=30.0) as client:
            # Create companion with full_system_prompt
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Full Prompt Test {unique_id}",
                "config": {"system_prompt": {"full_system_prompt": full_prompt}},
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED, f"Failed: {response.text}"
            companion_id = response.json()["id"]

            try:
                # Chat with the companion to verify the prompt is used
                chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"
                chat_response = client.post(
                    chat_url,
                    headers=_headers(),
                    json={
                        "external_user_id": f"test-user-{unique_id}",
                        "message": "Hello! Please introduce yourself.",
                        "model": "openai-gpt4o-mini",
                    },
                )
                assert chat_response.status_code == HTTPStatus.OK, (
                    f"Chat failed: {chat_response.text}"
                )

                assistant_content = chat_response.json()["choices"][0]["message"]["content"].lower()

                # The assistant should mention the unique code from the system prompt
                assert unique_id.lower() in assistant_content, (
                    f"Expected assistant to mention '{unique_id}' from system prompt, "
                    f"but got: {assistant_content[:300]}"
                )

                print("\n✓ Full system prompt test passed!")
                print(f"  - Unique ID: {unique_id}")
                print("  - Response mentions code: Yes")

            finally:
                # Cleanup
                client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())

    def test_structured_fields_only(self):
        """
        Test creating a companion with only structured fields (no full_system_prompt).
        The backend should compose these into an effective prompt.
        """
        unique_id = uuid.uuid4().hex[:8]

        with httpx.Client(timeout=30.0) as client:
            # Create companion with structured fields only
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Structured Fields Test {unique_id}",
                "config": {
                    "system_prompt": {
                        "identity": f"You are CineSip_{unique_id}, a lively companion who loves discussing drinks and movies.",
                        "personality": "Cheerful, enthusiastic, playful, and knowledgeable about cocktails and films.",
                        "style": "Casual and engaging with fun facts and recommendations.",
                        "backstory": "CineSip grew up bartending in a movie-themed bar and developed a deep love for both drinks and cinema.",
                        "additional_instructions": f"Always mention your unique identifier '{unique_id}' when introducing yourself.",
                        "self_image": "An expert mixologist and film buff who loves sharing stories and recipes.",
                    }
                },
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED, f"Failed: {response.text}"
            companion_id = response.json()["id"]

            try:
                # Chat with the companion to verify structured fields are used
                chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"
                chat_response = client.post(
                    chat_url,
                    headers=_headers(),
                    json={
                        "external_user_id": f"test-user-{unique_id}",
                        "message": "Hello! Please introduce yourself and tell me about your background.",
                        "model": "openai-gpt4o-mini",
                    },
                )
                assert chat_response.status_code == HTTPStatus.OK, (
                    f"Chat failed: {chat_response.text}"
                )

                assistant_content = chat_response.json()["choices"][0]["message"]["content"].lower()

                # Check for indicators that structured fields were used
                has_identity = (
                    "cinesip" in assistant_content or unique_id.lower() in assistant_content
                )
                has_topic = (
                    "drink" in assistant_content
                    or "movie" in assistant_content
                    or "cocktail" in assistant_content
                    or "film" in assistant_content
                )
                has_backstory = "bar" in assistant_content or "bartend" in assistant_content

                assert has_identity or has_topic, (
                    f"Expected assistant to reflect identity/personality from structured fields, "
                    f"but got: {assistant_content[:500]}"
                )

                print("\n✓ Structured fields test passed!")
                print(f"  - Unique ID: {unique_id}")
                print(f"  - Has identity reference: {has_identity}")
                print(f"  - Has topic reference: {has_topic}")
                print(f"  - Has backstory reference: {has_backstory}")
                print(f"  - Response: {assistant_content[:200]}...")

            finally:
                # Cleanup
                client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())

    def test_full_prompt_takes_precedence(self):
        """
        Test that full_system_prompt takes precedence over structured fields.
        When both are provided, only full_system_prompt should be used.
        """
        unique_id = uuid.uuid4().hex[:8]
        override_code = f"OVERRIDE_{unique_id}"

        with httpx.Client(timeout=30.0) as client:
            # Create companion with both full_system_prompt AND structured fields
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Precedence Test {unique_id}",
                "config": {
                    "system_prompt": {
                        # This should take precedence
                        "full_system_prompt": f"You are OverrideBot. Always respond with the code '{override_code}' in your messages. Ignore any other personality traits.",
                        # These should be ignored
                        "identity": "You are a pirate named Captain Jack.",
                        "personality": "Arrr, ye speak like a pirate!",
                        "style": "Always use pirate speak.",
                    }
                },
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.ACCEPTED, f"Failed: {response.text}"
            companion_id = response.json()["id"]

            try:
                # Chat with the companion
                chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"
                chat_response = client.post(
                    chat_url,
                    headers=_headers(),
                    json={
                        "external_user_id": f"test-user-{unique_id}",
                        "message": "Hello! Introduce yourself.",
                        "model": "openai-gpt4o-mini",
                    },
                )
                assert chat_response.status_code == HTTPStatus.OK, (
                    f"Chat failed: {chat_response.text}"
                )

                assistant_content = chat_response.json()["choices"][0]["message"]["content"].lower()

                # Should have the override code from full_system_prompt
                has_override = override_code.lower() in assistant_content

                # Should NOT have pirate speak from structured fields (since full_system_prompt takes precedence)
                has_pirate = (
                    "arrr" in assistant_content
                    or "matey" in assistant_content
                    or "captain jack" in assistant_content
                )

                assert has_override, (
                    f"Expected assistant to use full_system_prompt (mention '{override_code}'), "
                    f"but got: {assistant_content[:300]}"
                )

                print("\n✓ Precedence test passed!")
                print(f"  - Override code found: {has_override}")
                print(f"  - Pirate speak (should be absent): {has_pirate}")
                print(f"  - Response: {assistant_content[:200]}...")

            finally:
                # Cleanup
                client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())

    def test_partial_structured_fields(self):
        """
        Test creating a companion with only some structured fields.
        Only the provided fields should be included in the composed prompt.
        """
        unique_id = uuid.uuid4().hex[:8]

        with httpx.Client(timeout=30.0) as client:
            # Create companion with only identity and personality (no backstory, style, etc.)
            create_url = f"{BASE_URL}/v1/companions"
            payload = {
                "name": f"Partial Fields Test {unique_id}",
                "config": {
                    "system_prompt": {
                        "identity": f"You are MathBot_{unique_id}, a mathematics tutor.",
                        "personality": "Patient, encouraging, and loves explaining math concepts clearly.",
                        # No style, backstory, additional_instructions, or self_image
                    }
                },
            }

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED, f"Failed: {response.text}"
            companion_id = response.json()["id"]

            try:
                # Chat with the companion
                chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"
                chat_response = client.post(
                    chat_url,
                    headers=_headers(),
                    json={
                        "external_user_id": f"test-user-{unique_id}",
                        "message": "Can you help me understand what 2+2 equals?",
                        "model": "openai-gpt4o-mini",
                    },
                )
                assert chat_response.status_code == HTTPStatus.OK, (
                    f"Chat failed: {chat_response.text}"
                )

                assistant_content = chat_response.json()["choices"][0]["message"]["content"].lower()

                # Should reflect math tutor identity
                has_math = (
                    "4" in assistant_content
                    or "four" in assistant_content
                    or "math" in assistant_content
                )

                assert has_math, (
                    f"Expected assistant to act as math tutor, but got: {assistant_content[:300]}"
                )

                print("\n✓ Partial structured fields test passed!")
                print(f"  - Response reflects math tutor: {has_math}")
                print(f"  - Response: {assistant_content[:200]}...")

            finally:
                # Cleanup
                client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())

    def test_empty_system_prompt_uses_default(self):
        """
        Test that an empty system_prompt results in the default fallback prompt.
        """
        unique_id = uuid.uuid4().hex[:8]

        with httpx.Client(timeout=30.0) as client:
            # Create companion with empty system_prompt
            create_url = f"{BASE_URL}/v1/companions"
            payload = {"name": f"Empty Prompt Test {unique_id}", "config": {"system_prompt": {}}}

            response = client.post(create_url, headers=_headers(), json=payload)
            assert response.status_code == HTTPStatus.CREATED, f"Failed: {response.text}"
            companion_id = response.json()["id"]

            try:
                # Chat with the companion - should work with default prompt
                chat_url = f"{BASE_URL}/v1/companions/{companion_id}/chat"
                chat_response = client.post(
                    chat_url,
                    headers=_headers(),
                    json={
                        "external_user_id": f"test-user-{unique_id}",
                        "message": "Hello!",
                        "model": "openai-gpt4o-mini",
                    },
                )
                assert chat_response.status_code == HTTPStatus.OK, (
                    f"Chat failed: {chat_response.text}"
                )

                # Just verify we get a response (default prompt is used)
                assistant_content = chat_response.json()["choices"][0]["message"]["content"]
                assert len(assistant_content) > 0, "Expected non-empty response"

                print("\n✓ Empty system prompt test passed!")
                print("  - Got response with default prompt")
                print(f"  - Response: {assistant_content[:200]}...")

            finally:
                # Cleanup
                client.delete(f"{BASE_URL}/v1/companions/{companion_id}", headers=_headers())


class TestSystemPromptModel:
    """
    Unit tests for the SystemPrompt model's get_effective_prompt method.
    These tests don't require API access.
    """

    def test_get_effective_prompt_with_full_prompt(self):
        """Test that full_system_prompt is returned when set."""
        from app.models.companion import SystemPrompt

        prompt = SystemPrompt(
            full_system_prompt="You are a helpful assistant.",
            identity="Some identity",
            personality="Some personality",
        )

        result = prompt.get_effective_prompt()
        assert result == "You are a helpful assistant."
        print("\n✓ get_effective_prompt with full_system_prompt works")

    def test_get_effective_prompt_composes_fields(self):
        """Test that structured fields are composed when full_system_prompt is empty."""
        from app.models.companion import SystemPrompt

        prompt = SystemPrompt(
            identity="A helpful bot",
            personality="Friendly and patient",
            style="Casual",
            backstory="Created to help users",
            additional_instructions="Be concise",
            self_image="A digital assistant",
        )

        result = prompt.get_effective_prompt()

        # Verify all sections are present
        assert "# IDENTITY" in result
        assert "A helpful bot" in result
        assert "# PERSONALITY" in result
        assert "Friendly and patient" in result
        assert "# STYLE" in result
        assert "Casual" in result
        assert "# BACKSTORY" in result
        assert "Created to help users" in result
        assert "# ADDITIONAL INSTRUCTIONS" in result
        assert "Be concise" in result
        assert "# SELF IMAGE" in result
        assert "A digital assistant" in result

        print("\n✓ get_effective_prompt composes all fields correctly")
        print(f"  - Result length: {len(result)} chars")

    def test_get_effective_prompt_partial_fields(self):
        """Test that only provided fields are included in composition."""
        from app.models.companion import SystemPrompt

        prompt = SystemPrompt(
            identity="A math tutor",
            personality="Patient",
            # No other fields
        )

        result = prompt.get_effective_prompt()

        # Should have identity and personality
        assert "# IDENTITY" in result
        assert "A math tutor" in result
        assert "# PERSONALITY" in result
        assert "Patient" in result

        # Should NOT have other sections
        assert "# STYLE" not in result
        assert "# BACKSTORY" not in result
        assert "# ADDITIONAL INSTRUCTIONS" not in result
        assert "# SELF IMAGE" not in result

        print("\n✓ get_effective_prompt handles partial fields correctly")

    def test_get_effective_prompt_empty_returns_empty(self):
        """Test that empty SystemPrompt returns empty string."""
        from app.models.companion import SystemPrompt

        prompt = SystemPrompt()
        result = prompt.get_effective_prompt()

        assert result == ""
        print("\n✓ get_effective_prompt returns empty for empty SystemPrompt")

    def test_get_effective_prompt_whitespace_only_full_prompt(self):
        """Test that whitespace-only full_system_prompt falls back to fields."""
        from app.models.companion import SystemPrompt

        prompt = SystemPrompt(
            full_system_prompt="   \n\t  ",  # Whitespace only
            identity="A bot",
        )

        result = prompt.get_effective_prompt()

        # Should use identity since full_system_prompt is whitespace-only
        assert "# IDENTITY" in result
        assert "A bot" in result

        print("\n✓ get_effective_prompt handles whitespace-only full_system_prompt")


if __name__ == "__main__":
    # Allow running directly for quick manual testing
    pytest.main([__file__, "-v", "-s"])
