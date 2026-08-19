"""Tests for Phase 3: app_state injection into companion prompt.

These tests verify that app_state is properly injected into the LLM context
when include_app_state_in_prompt is enabled in the relationship config.

Run with: uv run python tests/test_v2_app_state_injection.py
"""

import json
import os
import sys
from uuid import uuid4

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")
COMPANION_ID = os.getenv("TEST_EM_COMPANION_ID")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _headers_sse() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


# Sample app_state based on real client data structure
SAMPLE_APP_STATE = {
    # Core Identity
    "core_identity": {
        "name": "Sarah Chen",
        "age": 32,
        "location": "San Francisco, CA",
        "occupation": "Product Manager",
        "life_stage": "early_career",
        "gender": "female",
        "pronouns": "she/her",
        "sexual_orientation": "heterosexual",
    },
    # Companion Relationship
    "companion_relationship": {
        "topics_summary": "Work stress, career growth, relationship advice, fitness goals",
        "primary_use_cases": ["emotional_support", "goal_tracking", "daily_reflection"],
        "boundaries_established": ["no_medical_advice", "no_financial_advice"],
    },
    # Personality (Big Five)
    "personality": {
        "openness": {"score": 0.78, "facets": ["curious", "creative", "open_to_experience"]},
        "conscientiousness": {"score": 0.85, "facets": ["organized", "goal_oriented", "reliable"]},
        "extraversion": {"score": 0.45, "facets": ["ambivert", "selective_social"]},
        "agreeableness": {"score": 0.72, "facets": ["empathetic", "cooperative"]},
        "neuroticism": {"score": 0.55, "facets": ["occasional_anxiety", "stress_reactive"]},
        "social_style": "thoughtful_connector",
        "verbosity": "moderate",
    },
    # Health Data
    "health_data": {
        "cycle_data": {
            "average_length": 28,
            "symptom_patterns": ["fatigue_day_1", "mood_swings_day_25_27"],
            "phases": {
                "current_phase": "luteal",
                "days_until_next": 5,
            },
        },
    },
    # Memory Context
    "memory_context": {
        "conversation_topics": [
            "promotion_discussion_2024_01",
            "relationship_with_partner_mark",
            "marathon_training_goal",
        ],
    },
    # Values & Beliefs
    "values_beliefs": {
        "core_values": ["authenticity", "growth", "connection", "balance"],
        "beliefs": ["hard_work_pays_off", "mental_health_matters", "continuous_learning"],
        "goals": [
            {"goal": "get_promoted_to_senior_pm", "timeline": "6_months", "priority": "high"},
            {"goal": "run_first_marathon", "timeline": "1_year", "priority": "medium"},
            {"goal": "improve_work_life_balance", "timeline": "ongoing", "priority": "high"},
        ],
        "fears": ["burnout", "missing_life_milestones", "not_reaching_potential"],
        "motivations": ["career_success", "personal_fulfillment", "helping_others"],
    },
}

# Minimal app_state for basic testing
MINIMAL_APP_STATE = {
    "user_name": "TestUser",
    "tier": "premium",
}


def _create_relationship_with_app_state(
    client: httpx.Client,
    user_id: str,
    app_state: dict,
    include_in_prompt: bool = True,
) -> str:
    """Helper to create a relationship with app_state and config."""
    rel_url = f"{BASE_URL}/v2/companions/{COMPANION_ID}/relationships/{user_id}"

    response = client.put(
        rel_url,
        headers=_headers(),
        json={
            "config": {"include_app_state_in_prompt": include_in_prompt},
            "app_state": app_state,
        },
    )
    assert response.status_code == 200, f"Failed to create relationship: {response.text}"
    return response.json()["id"]


def _cleanup_relationship(client: httpx.Client, relationship_id: str) -> None:
    """Helper to delete a relationship."""
    client.delete(f"{BASE_URL}/v2/relationships/{relationship_id}", headers=_headers())


def test_basic_app_state_injection():
    """Basic test: app_state is injected when flag is enabled."""
    user_id = f"test-app-state-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, MINIMAL_APP_STATE, include_in_prompt=True
        )

        try:
            # Send a message
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "Hello!"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"]["role"] == "assistant"
            assert len(data["message"]["content"]) > 0
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_basic_app_state_injection")


def test_complex_nested_app_state():
    """Test injection with complex nested app_state structure."""
    user_id = f"test-complex-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, SAMPLE_APP_STATE, include_in_prompt=True
        )

        try:
            # Send a message
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "Hi there!"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"]["role"] == "assistant"
            assert len(data["message"]["content"]) > 0
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_complex_nested_app_state")


def test_companion_references_app_state_name():
    """Test that companion can reference the user's name from app_state."""
    user_id = f"test-name-ref-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, SAMPLE_APP_STATE, include_in_prompt=True
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "What is my name?"},
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # The companion should mention "Sarah" from the app_state
            assert "sarah" in content, f"Expected 'Sarah' in response, got: {content}"
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_companion_references_app_state_name")


def test_companion_references_app_state_location():
    """Test that companion can reference location from app_state."""
    user_id = f"test-loc-ref-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, SAMPLE_APP_STATE, include_in_prompt=True
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "Where do I live?"},
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # Should mention San Francisco
            assert "san francisco" in content or "sf" in content, (
                f"Expected 'San Francisco' in response, got: {content}"
            )
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_companion_references_app_state_location")


def test_companion_references_goals():
    """Test that companion can reference goals from app_state."""
    user_id = f"test-goals-ref-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, SAMPLE_APP_STATE, include_in_prompt=True
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "What are my goals? List them briefly."},
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # Should mention at least one of the goals
            has_promotion = "promot" in content or "senior" in content or "pm" in content
            has_marathon = "marathon" in content or "run" in content
            has_balance = "balance" in content or "work" in content

            assert has_promotion or has_marathon or has_balance, (
                f"Expected at least one goal reference in response, got: {content}"
            )
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_companion_references_goals")


def test_app_state_not_visible_when_disabled():
    """Test that companion CANNOT see app_state when flag is disabled."""
    user_id = f"test-disabled-{uuid4().hex[:8]}"

    # Use a distinctive name that would be obvious if mentioned
    app_state_with_unique_name = {
        "core_identity": {
            "name": "Zephyrina Moonwhisper",  # Very distinctive name
        }
    }

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client,
            user_id,
            app_state_with_unique_name,
            include_in_prompt=False,  # DISABLED
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "What is my name?"},
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # Should NOT mention the distinctive name since app_state is not injected
            assert "zephyrina" not in content, (
                f"Expected name NOT to be in response when disabled, got: {content}"
            )
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_app_state_not_visible_when_disabled")


def test_streaming_with_app_state():
    """Test that app_state injection works with SSE streaming."""
    user_id = f"test-stream-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, SAMPLE_APP_STATE, include_in_prompt=True
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"

            with client.stream(
                "POST",
                msg_url,
                headers=_headers_sse(),
                json={"content": "Say my name."},
            ) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")

                events = []
                current_event = {"event": None, "data": None}
                full_content = []

                for line in response.iter_lines():
                    if line.startswith("event:"):
                        current_event["event"] = line[6:].strip()
                    elif line.startswith("data:"):
                        current_event["data"] = line[5:].strip()
                    elif line == "":
                        if current_event["event"] and current_event["data"]:
                            event_data = json.loads(current_event["data"])
                            events.append(
                                {
                                    "event": current_event["event"],
                                    "data": event_data,
                                }
                            )
                            # Collect delta content
                            if current_event["event"] == "delta":
                                delta_content = event_data.get("data", {}).get("content", "")
                                if delta_content:
                                    full_content.append(delta_content)
                        current_event = {"event": None, "data": None}

                # Verify we got streaming events
                event_types = [e["event"] for e in events]
                assert "ack" in event_types
                assert "message" in event_types

                # Verify final content mentions the name
                final_text = "".join(full_content).lower()
                assert "sarah" in final_text, (
                    f"Expected 'Sarah' in streamed response, got: {final_text}"
                )
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_streaming_with_app_state")


def test_update_app_state_reflected_in_next_message():
    """Test that updating app_state is reflected in the next message."""
    user_id = f"test-update-{uuid4().hex[:8]}"

    with httpx.Client(timeout=90.0) as client:
        # Create with initial app_state
        initial_app_state = {
            "core_identity": {"name": "InitialName"},
        }
        relationship_id = _create_relationship_with_app_state(
            client, user_id, initial_app_state, include_in_prompt=True
        )

        try:
            # Update app_state with new name
            app_state_url = f"{BASE_URL}/v2/relationships/{relationship_id}/app-state"
            update_response = client.put(
                app_state_url,
                headers=_headers(),
                json={
                    "core_identity": {"name": "UpdatedNameXyz"},
                },
            )
            assert update_response.status_code == 200

            # Send message asking for name
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "What is my name?"},
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # Should mention the UPDATED name, not the initial one
            assert "updatednamexyz" in content or "updated" in content, (
                f"Expected updated name in response, got: {content}"
            )
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_update_app_state_reflected_in_next_message")


def test_personality_traits_accessible():
    """Test that personality traits (Big Five) are accessible from app_state."""
    user_id = f"test-personality-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client, user_id, SAMPLE_APP_STATE, include_in_prompt=True
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={
                    "content": "Based on what you know about my personality, am I more introverted or extroverted?"
                },
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # Should reference something about extraversion/introversion
            # (extraversion score is 0.45, which is ambivert/slightly introverted)
            has_relevant_response = (
                "introvert" in content
                or "extrovert" in content
                or "ambivert" in content
                or "balance" in content
                or "middle" in content
            )
            assert has_relevant_response, f"Expected personality-related response, got: {content}"
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_personality_traits_accessible")


def test_empty_app_state_no_error():
    """Test that empty app_state doesn't cause errors."""
    user_id = f"test-empty-{uuid4().hex[:8]}"

    with httpx.Client(timeout=60.0) as client:
        relationship_id = _create_relationship_with_app_state(
            client,
            user_id,
            {},
            include_in_prompt=True,  # Empty app_state
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "Hello!"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["message"]["role"] == "assistant"
            assert len(data["message"]["content"]) > 0
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_empty_app_state_no_error")


def test_large_app_state():
    """Test that large app_state objects are handled correctly."""
    user_id = f"test-large-{uuid4().hex[:8]}"

    # Create a larger app_state with more data
    large_app_state = {
        **SAMPLE_APP_STATE,
        "extended_history": {
            f"event_{i}": {
                "date": f"2024-01-{i:02d}",
                "description": f"Sample event number {i}",
            }
            for i in range(1, 6)  # 5 events (reduced for test speed)
        },
        "preferences": {
            "notification_settings": {
                "email": True,
                "push": True,
                "sms": False,
            },
            "theme": "dark",
            "timezone": "America/Los_Angeles",
        },
    }

    with httpx.Client(timeout=120.0) as client:  # Increased timeout
        relationship_id = _create_relationship_with_app_state(
            client, user_id, large_app_state, include_in_prompt=True
        )

        try:
            msg_url = f"{BASE_URL}/v2/relationships/{relationship_id}/messages"
            response = client.post(
                msg_url,
                headers=_headers(),
                json={"content": "Hello! What timezone am I in?"},
            )
            assert response.status_code == 200
            data = response.json()
            content = data["message"]["content"].lower()

            # Should be able to access data even with large app_state
            # Check for various ways the timezone might be expressed
            has_timezone_ref = (
                "los angeles" in content
                or "los_angeles" in content
                or "america/los" in content
                or "pacific" in content
                or "pst" in content
                or "pdt" in content
            )
            assert has_timezone_ref, f"Expected timezone reference, got: {content}"
        finally:
            _cleanup_relationship(client, relationship_id)

    print("✓ test_large_app_state")


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: Set TEST_EM_API_KEY environment variable")
        sys.exit(1)
    if not COMPANION_ID:
        print("ERROR: Set TEST_EM_COMPANION_ID environment variable")
        sys.exit(1)

    print(f"Testing against {BASE_URL}")
    print(f"Companion: {COMPANION_ID}")
    print("\nPhase 3: app_state injection tests\n")

    # Basic functionality tests
    test_basic_app_state_injection()
    test_complex_nested_app_state()
    test_empty_app_state_no_error()
    test_large_app_state()

    # Verify companion can access app_state data
    test_companion_references_app_state_name()
    test_companion_references_app_state_location()
    test_companion_references_goals()
    test_personality_traits_accessible()

    # Verify app_state is NOT visible when disabled
    test_app_state_not_visible_when_disabled()

    # Streaming test
    test_streaming_with_app_state()

    # Update test
    test_update_app_state_reflected_in_next_message()

    print("\n✅ All Phase 3 app_state injection tests passed!")
