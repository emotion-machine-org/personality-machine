"""
Emotion Machine API Load Tests.

Usage:
    # Start Locust web UI
    cd server/loadtests
    locust

    # Run headless with specific parameters
    locust --headless -u 100 -r 10 --run-time 5m --host http://localhost:8100

    # Run specific user class only
    locust --headless -u 50 -r 5 --run-time 2m MessageUser

Environment variables required:
    EM_API_KEY: API key for authentication
    EM_TEST_COMPANION_ID: UUID of test companion
    EM_BASE_URL: Base URL (defaults to http://localhost:8100)
"""

import random
import string
import uuid

from config import config
from locust import HttpUser, between, events, tag, task


def generate_user_id() -> str:
    """Generate a unique test user ID."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{config.test_user_prefix}{suffix}"


def generate_message() -> str:
    """Generate a random test message."""
    messages = [
        "Hello, how are you today?",
        "Can you help me with something?",
        "What's the weather like?",
        "Tell me a joke.",
        "I need some advice.",
        "What do you think about this?",
        "Can you explain that again?",
        "Thanks for your help!",
        "I have a question about my account.",
        "What are your capabilities?",
    ]
    return random.choice(messages)


class EmotionMachineUser(HttpUser):
    """Base user class with authentication and common setup."""

    abstract = True
    wait_time = between(1, 3)
    host = config.base_url

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = generate_user_id()
        self.relationship_id: str | None = None
        self.session_id: str | None = None

    def on_start(self):
        """Called when user starts - validate config and setup."""
        errors = config.validate()
        if errors:
            raise RuntimeError(f"Configuration errors: {errors}")

    @property
    def headers(self) -> dict[str, str]:
        """Common headers for all requests."""
        return {
            **config.auth_header,
            "Content-Type": "application/json",
        }

    def ensure_relationship(self) -> str | None:
        """Ensure relationship exists and return relationship_id."""
        if self.relationship_id:
            return self.relationship_id

        with self.client.put(
            f"/v2/companions/{config.companion_id}/relationships/{self.user_id}",
            headers=self.headers,
            name="/v2/companions/[cid]/relationships/[uid]",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                data = response.json()
                self.relationship_id = data.get("id")
                response.success()
                return self.relationship_id
            else:
                response.failure(f"Failed to create relationship: {response.status_code}")
                return None


# =============================================================================
# Message Load Tests (Critical - highest load)
# =============================================================================


class MessageUser(EmotionMachineUser):
    """
    User that sends messages - the primary load test scenario.

    This simulates the core user interaction: sending messages and receiving responses.
    Weight: 10 (most common operation)
    """

    weight = 10

    @task(10)
    @tag("messages", "critical")
    def send_message(self):
        """Send a message via REST endpoint."""
        if not self.ensure_relationship():
            return

        with self.client.post(
            f"/v2/companions/{config.companion_id}/relationships/{self.user_id}/messages",
            json={"content": generate_message()},
            headers=self.headers,
            name="/v2/.../messages (REST)",
            catch_response=True,
            timeout=60,  # LLM responses can be slow
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 409:
                # Turn in progress - this is expected under load
                response.success()
            else:
                response.failure(f"Message failed: {response.status_code} - {response.text[:200]}")

    @task(5)
    @tag("messages", "streaming", "critical")
    def send_message_streaming(self):
        """Send a message with streaming response (SSE)."""
        if not self.ensure_relationship():
            return

        headers = {**self.headers, "Accept": "text/event-stream"}

        with self.client.post(
            f"/v2/companions/{config.companion_id}/relationships/{self.user_id}/messages",
            json={"content": generate_message()},
            headers=headers,
            name="/v2/.../messages (SSE)",
            catch_response=True,
            stream=True,
            timeout=60,
        ) as response:
            if response.status_code == 200:
                # Consume the stream
                for _ in response.iter_lines():
                    pass
                response.success()
            elif response.status_code == 409:
                response.success()
            else:
                response.failure(f"Streaming failed: {response.status_code}")


# =============================================================================
# Relationship Load Tests (High load - session setup)
# =============================================================================


class RelationshipUser(EmotionMachineUser):
    """
    User that tests relationship operations.

    Simulates new users arriving and setting up relationships.
    Weight: 5
    """

    weight = 5

    @task(10)
    @tag("relationships", "high")
    def ensure_relationship_exists(self):
        """Test the idempotent relationship creation endpoint."""
        # Generate new user each time to simulate new users
        new_user_id = generate_user_id()

        with self.client.put(
            f"/v2/companions/{config.companion_id}/relationships/{new_user_id}",
            headers=self.headers,
            name="/v2/.../relationships/[uid] (PUT)",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                response.success()
            else:
                response.failure(f"Relationship creation failed: {response.status_code}")

    @task(5)
    @tag("relationships", "high")
    def get_relationship(self):
        """Get relationship details."""
        if not self.ensure_relationship():
            return

        with self.client.get(
            f"/v2/companions/{config.companion_id}/relationships/{self.user_id}",
            headers=self.headers,
            name="/v2/.../relationships/[uid] (GET)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Get relationship failed: {response.status_code}")


# =============================================================================
# Profile Load Tests (High load - frequently accessed)
# =============================================================================


class ProfileUser(EmotionMachineUser):
    """
    User that tests profile operations.

    Simulates reading and updating user profiles.
    Weight: 3
    """

    weight = 3

    @task(10)
    @tag("profile", "high")
    def get_profile(self):
        """Get user profile."""
        if not self.ensure_relationship():
            return

        with self.client.get(
            f"/v2/relationships/{self.relationship_id}/profile",
            headers=self.headers,
            name="/v2/relationships/[rid]/profile (GET)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Get profile failed: {response.status_code}")

    @task(3)
    @tag("profile", "high")
    def patch_profile(self):
        """Update user profile."""
        if not self.ensure_relationship():
            return

        profile_data = {
            "loadtest": {
                "last_updated": str(uuid.uuid4())[:8],
                "test_value": random.randint(1, 100),
            }
        }

        with self.client.patch(
            f"/v2/relationships/{self.relationship_id}/profile",
            json=profile_data,
            headers=self.headers,
            name="/v2/relationships/[rid]/profile (PATCH)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Patch profile failed: {response.status_code}")


# =============================================================================
# Inbox Load Tests (High load - polling endpoint)
# =============================================================================


class InboxUser(EmotionMachineUser):
    """
    User that polls inbox for proactive messages.

    Simulates clients polling for proactive messages.
    Weight: 3
    """

    weight = 3

    @task(10)
    @tag("inbox", "high")
    def check_inbox(self):
        """Check inbox for proactive messages."""
        if not self.ensure_relationship():
            return

        with self.client.get(
            f"/v2/relationships/{self.relationship_id}/inbox",
            headers=self.headers,
            name="/v2/relationships/[rid]/inbox",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Check inbox failed: {response.status_code}")


# =============================================================================
# Session Load Tests (Medium load)
# =============================================================================


class SessionUser(EmotionMachineUser):
    """
    User that tests session lifecycle.

    Simulates users starting and ending sessions.
    Weight: 2
    """

    weight = 2

    @task(5)
    @tag("sessions", "medium")
    def start_session(self):
        """Start a new session."""
        if not self.ensure_relationship():
            return

        with self.client.post(
            f"/v2/relationships/{self.relationship_id}/sessions",
            json={"type": "loadtest", "isolated": True},
            headers=self.headers,
            name="/v2/relationships/[rid]/sessions (POST)",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                data = response.json()
                self.session_id = data.get("id")
                response.success()
            elif response.status_code == 409:
                # Session already active
                response.success()
            else:
                response.failure(f"Start session failed: {response.status_code}")

    @task(3)
    @tag("sessions", "medium")
    def get_active_session(self):
        """Get active session."""
        if not self.ensure_relationship():
            return

        with self.client.get(
            f"/v2/relationships/{self.relationship_id}/sessions/active",
            headers=self.headers,
            name="/v2/relationships/[rid]/sessions/active",
            catch_response=True,
        ) as response:
            if response.status_code in (200, 404):  # 404 is valid (no active session)
                response.success()
            else:
                response.failure(f"Get active session failed: {response.status_code}")

    @task(2)
    @tag("sessions", "medium")
    def end_session(self):
        """End current session."""
        if not self.session_id:
            return

        with self.client.post(
            f"/v2/sessions/{self.session_id}/end",
            headers=self.headers,
            name="/v2/sessions/[sid]/end",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.session_id = None
                response.success()
            elif response.status_code == 404:
                # Session already ended
                self.session_id = None
                response.success()
            else:
                response.failure(f"End session failed: {response.status_code}")


# =============================================================================
# Knowledge Load Tests (Medium load)
# =============================================================================


class KnowledgeUser(EmotionMachineUser):
    """
    User that tests knowledge search.

    Simulates knowledge base queries.
    Weight: 2
    """

    weight = 2

    @task(10)
    @tag("knowledge", "medium")
    def search_knowledge(self):
        """Search knowledge base."""
        queries = [
            "How do I reset my password?",
            "What are the pricing plans?",
            "How do I contact support?",
            "What features are available?",
            "How do I get started?",
        ]

        with self.client.post(
            f"/v1/companions/{config.companion_id}/knowledge/search",
            json={"query": random.choice(queries), "max_results": 5},
            headers=self.headers,
            name="/v1/companions/[cid]/knowledge/search",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 404:
                # Knowledge not enabled - still valid
                response.success()
            else:
                response.failure(f"Knowledge search failed: {response.status_code}")


# =============================================================================
# Companion Load Tests (Low load - dashboard operations)
# =============================================================================


class CompanionUser(EmotionMachineUser):
    """
    User that tests companion listing/retrieval.

    Simulates dashboard loads.
    Weight: 1
    """

    weight = 1

    @task(5)
    @tag("companions", "low")
    def list_companions(self):
        """List all companions."""
        with self.client.get(
            "/v1/companions",
            headers=self.headers,
            name="/v1/companions (GET)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"List companions failed: {response.status_code}")

    @task(3)
    @tag("companions", "low")
    def get_companion(self):
        """Get specific companion details."""
        with self.client.get(
            f"/v1/companions/{config.companion_id}",
            headers=self.headers,
            name="/v1/companions/[cid] (GET)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Get companion failed: {response.status_code}")


# =============================================================================
# Config Load Tests (Low load - infrequent)
# =============================================================================


class ConfigUser(EmotionMachineUser):
    """
    User that tests configuration retrieval.

    Simulates config checks.
    Weight: 1
    """

    weight = 1

    @task(5)
    @tag("config", "low")
    def get_resolved_config(self):
        """Get resolved configuration."""
        if not self.ensure_relationship():
            return

        with self.client.get(
            f"/v2/relationships/{self.relationship_id}/config/resolved",
            headers=self.headers,
            name="/v2/relationships/[rid]/config/resolved",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Get config failed: {response.status_code}")


# =============================================================================
# Event Hooks for Results Collection
# =============================================================================


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    errors = config.validate()
    if errors:
        print(f"\n{'=' * 60}")
        print("CONFIGURATION ERRORS:")
        for error in errors:
            print(f"  - {error}")
        print(f"{'=' * 60}\n")
        print("Set the required environment variables and try again.")
        print("Example:")
        print('  export EM_API_KEY="em_live_your_key_here"')
        print('  export EM_TEST_COMPANION_ID="your-companion-uuid"')
        print('  export EM_BASE_URL="http://localhost:8100"')
        print(f"{'=' * 60}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops."""
    print("\n" + "=" * 60)
    print("LOAD TEST COMPLETED")
    print("=" * 60)
