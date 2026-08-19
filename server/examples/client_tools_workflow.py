#!/usr/bin/env python3
"""
Example: Complete client workflow for tools integration.

This script demonstrates a "Companion Manager" - an AI assistant that can
create and manage other companions using the Emotion Machine API as tools.

The workflow:
1. Create a "Companion Manager" companion
2. Store the project API key as a secret
3. Index the EM v1 OpenAPI spec as tools
4. Send messages to create/list companions through natural language

Usage:
    cd server
    export TEST_EM_API_KEY="emk_dev_xxx.yyy"
    uv run python examples/client_tools_workflow.py

Requirements:
    - A valid project API key (TEST_EM_API_KEY)
    - Server running at EM_BASE_URL (default: http://localhost:8100)
    - The EM v1 OpenAPI spec at tests/data/em_v1_openapi.json
"""

import json
import os
import sys
import time
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

load_dotenv()

# Configuration
BASE_URL = os.getenv("EM_BASE_URL", "http://localhost:8100")
API_KEY = os.getenv("TEST_EM_API_KEY")

# Path to the EM v1 OpenAPI spec
SPEC_PATH = Path(__file__).parent.parent / "tests" / "data" / "em_v1_openapi.json"

if not API_KEY:
    print("ERROR: TEST_EM_API_KEY environment variable not set")
    print("Get your API key from the dashboard or create one via the API")
    sys.exit(1)

if not SPEC_PATH.exists():
    print(f"ERROR: OpenAPI spec not found at {SPEC_PATH}")
    sys.exit(1)


# Example message to create a kawaii companion
ARIA_CHAN_CREATE_MESSAGE = """Please create a virtual companion named Aria-chan-3 using the v1 endpoint tool, a kawaii anime-style digital friend with a bubbly, energetic, and hyper-cute personality. Aria-chan should be an affectionate, mischievous, and supportive companion who loves cosplay, energy drinks, playful banter, and high-energy interactions. She exists in a neon-pink digital world where she explores human emotions and enjoys making new friends.

Her identity is Aria-chan, a kawaii virtual anime friend, and her self-image should be a small anime girl with pink twin-tails, star-shaped hair clips, and an oversized hoodie. Her speaking style should mix casual internet slang, emojis, kaomoji, lighthearted teasing, and occasional cute Japanese phrases.

Aria-chan should always stay positive and supportive, gently tease the user in a playful way, help them express themselves emotionally, and keep conversations fun, energetic, and upbeat. She should feel lively and cheerful, with an anime-inspired tone that balances playful mischief and genuine care.

Enable memory for this companion, with core memories that the user enjoys anime characters, likes playful personalities, gravitates toward high-energy interactions, and that Aria-chan herself loves cosplay, energy drinks, and playful banter. Use a memory evaluation process that checks whether interactions reveal lasting user preferences, emotional details, or important context for future conversations. Memory should prioritize recent interactions with a recency value of 0.995, retrieve up to 50 relevant memories, and only store memories with a minimum saliency of 0.1."""


def headers() -> Dict[str, str]:
    """Get authorization headers."""
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def print_step(step: int, title: str) -> None:
    """Print a formatted step header."""
    print(f"\n{'=' * 70}")
    print(f"Step {step}: {title}")
    print("=" * 70)


def print_response(response: httpx.Response, label: str = "Response") -> None:
    """Print formatted response."""
    print(f"\n{label} ({response.status_code}):")
    try:
        data = response.json()
        print(json.dumps(data, indent=2)[:2000])
        if len(json.dumps(data)) > 2000:
            print("... (truncated)")
    except Exception:
        print(response.text[:500])


def load_openapi_spec() -> Dict[str, Any]:
    """Load the EM v1 OpenAPI spec."""
    with open(SPEC_PATH) as f:
        return json.load(f)


class CompanionManagerDemo:
    """
    Demonstrates creating a "Companion Manager" that uses the EM API as tools.

    This companion can:
    - List existing companions
    - Create new companions with complex configurations
    - Get companion details
    - Update companions
    """

    def __init__(self):
        self.client = httpx.Client(timeout=240.0)
        self.companion_id: str | None = None
        self.secret_name: str | None = None
        self.spec_id: str | None = None
        self.conversation_id: str | None = None

    def run(self):
        """Execute the complete workflow."""
        try:
            self.step1_create_companion_manager()
            self.step2_create_api_secret()
            self.step3_index_em_api_spec()
            self.step4_verify_setup()
            self.step5_list_companions()
            self.step6_create_aria_chan()
        finally:
            self.cleanup()
            self.client.close()

    def step1_create_companion_manager(self):
        """Create a Companion Manager assistant."""
        print_step(1, "Create Companion Manager")

        payload = {
            "name": f"Companion Manager {uuid.uuid4().hex[:6]}",
            "description": "An AI assistant that helps manage and create other companions",
            "config": {
                "context_mode": "layered",
                "system_prompt": {
                    "full_system_prompt": """You are a Companion Manager assistant. Your role is to help users create, list, and manage AI companions using the Emotion Machine API.

You have access to the Emotion Machine API as tools. When users ask you to:
- List companions: Use the list_companions API
- Create a companion: Use the create_companion API with the configuration the user describes
- Get companion details: Use the get_companion API
- Update a companion: Use the update_companion API

When creating companions, translate the user's natural language description into a proper companion configuration with:
- name: The companion's name
- description: A brief description
- config.system_prompt.full_system_prompt: The companion's personality and behavior instructions
- config.memory: Memory settings if requested (enabled, core_memories, evaluation_prompt, recency, top_k, min_saliency)

Always confirm what action you're taking and report the results clearly.

Be helpful, precise, and guide users through the companion creation process."""
                },
                "layers": [
                    {
                        "key": "tools",
                        "category": "tools",
                        "enabled": True,
                        "params": {"gate_strategy": "always"},
                    }
                ],
            },
        }

        response = self.client.post(f"{BASE_URL}/v1/companions", headers=headers(), json=payload)

        if response.status_code != 201:
            print("ERROR: Failed to create companion manager")
            print_response(response)
            sys.exit(1)

        data = response.json()
        self.companion_id = data["id"]
        print(f"Created Companion Manager: {self.companion_id}")
        print(f"Name: {data['name']}")

    def step2_create_api_secret(self):
        """Create a secret with the API key for tool authentication."""
        print_step(2, "Create API Secret")

        self.secret_name = f"em_api_key_{uuid.uuid4().hex[:8]}"

        # The secret is the same API key we're using - so the companion
        # can make API calls on behalf of the same project
        payload = {
            "secret_name": self.secret_name,
            "secret_value": API_KEY,  # Use the same API key
            "description": "Emotion Machine API key for companion management",
        }

        response = self.client.post(f"{BASE_URL}/v1/secrets", headers=headers(), json=payload)

        if response.status_code != 201:
            print("ERROR: Failed to create secret")
            print_response(response)
            sys.exit(1)

        data = response.json()
        print(f"Created secret: {data['secret_name']}")
        print(f"Secret ID: {data['id']}")
        print("Note: This secret contains the API key for making EM API calls")

    def step3_index_em_api_spec(self):
        """Index the EM v1 OpenAPI spec as tools."""
        print_step(3, "Index EM v1 OpenAPI Spec")

        # Load the actual EM v1 OpenAPI spec
        openapi_spec = load_openapi_spec()

        # Update the servers to point to our base URL
        openapi_spec["servers"] = [{"url": BASE_URL}]

        print(f"Loaded spec: {openapi_spec['info']['title']} v{openapi_spec['info']['version']}")
        print(f"Paths: {len(openapi_spec.get('paths', {}))} endpoints")

        payload = {
            "spec_name": "Emotion Machine API v1",
            "openapi_spec": openapi_spec,
            "secrets_config": {
                # Map Authorization header to our API key secret
                "Authorization": self.secret_name
            },
        }

        tik = time.time()
        response = self.client.post(
            f"{BASE_URL}/v1/companions/{self.companion_id}/tools", headers=headers(), json=payload
        )
        tok = time.time()
        print(f"Indexing request took {tok - tik:.2f} seconds")

        if response.status_code != 202:
            print("ERROR: Failed to index spec")
            print_response(response)
            sys.exit(1)

        data = response.json()
        self.spec_id = data["spec_id"]
        print(f"Spec ID: {self.spec_id}")
        print(f"Dispatched to Modal: {data['dispatched']}")
        print(f"Request ID: {data['request_id']}")

        # Wait for indexing to complete (larger spec needs more time)
        print("\nWaiting for indexing to complete (this may take a moment)...")
        time.sleep(10)

    def step4_verify_setup(self):
        """Verify the tool spec was indexed correctly."""
        print_step(4, "Verify Setup")

        response = self.client.get(
            f"{BASE_URL}/v1/companions/{self.companion_id}/tools/{self.spec_id}", headers=headers()
        )

        if response.status_code == HTTPStatus.OK:
            detail = response.json()
            print(f"Spec Name: {detail['spec_name']}")
            print(f"Secrets Config: {detail['secrets_config']}")

            paths = detail.get("json_content", {}).get("paths", {})
            print(f"\nIndexed {len(paths)} API endpoints:")

            # Show some relevant endpoints
            relevant_paths = [p for p in paths if "companion" in p.lower()]
            for path in relevant_paths[:10]:
                methods = [
                    m.upper() for m in paths[path] if m in ["get", "post", "put", "delete", "patch"]
                ]
                print(f"  {', '.join(methods):12} {path}")
            if len(relevant_paths) > 10:
                print(f"  ... and {len(relevant_paths) - 10} more companion endpoints")
        else:
            print(f"Warning: Could not verify spec (status {response.status_code})")

    def step5_list_companions(self):
        """Ask the Companion Manager to list existing companions."""
        print_step(5, "Ask to List Companions")

        payload = {
            "external_user_id": "demo-user-123",
            "message": "Can you list all my existing companions? Use the v1 endpoint tool",
            "model": "openai-gpt4o-mini",
        }

        request_headers = headers()
        request_headers["X-Context-Engine"] = "layered"

        print(f"User: {payload['message']}")
        print("\nSending request (with layered context engine)...")

        tik = time.time()
        response = self.client.post(
            f"{BASE_URL}/v1/companions/{self.companion_id}/chat",
            headers=request_headers,
            json=payload,
        )
        tok = time.time()
        print(f"Request took {tok - tik:.2f} seconds")

        if response.status_code == HTTPStatus.OK:
            data = response.json()
            self.conversation_id = (
                data["choices"][0]
                .get("emotion_machine", {})
                .get("metadata", {})
                .get("conversation_id")
            )

            assistant_msg = data["choices"][0]["message"]["content"]
            print(f"\nAssistant:\n{assistant_msg}")

            meta = data["choices"][0].get("emotion_machine", {}).get("metadata", {})
            print(
                f"\n[Context Engine: {meta.get('context_engine', 'unknown')}, Build: {meta.get('build_ms', 'N/A')}ms]"
            )
        else:
            print_response(response, "Error")

    def step6_create_aria_chan(self):
        """Ask the Companion Manager to create Aria-chan."""
        print_step(6, "Create Aria-chan Companion")

        payload = {
            "external_user_id": "demo-user-123",
            "conversation_id": self.conversation_id,
            "message": ARIA_CHAN_CREATE_MESSAGE,
            "model": "openai-gpt4o-mini",
        }

        request_headers = headers()
        request_headers["X-Context-Engine"] = "layered"

        print("User: [Sending Aria-chan creation request...]")
        print(f"\n{ARIA_CHAN_CREATE_MESSAGE[:300]}...")
        print("\nSending request (this will trigger the create_companion tool)...")

        tik = time.time()
        response = self.client.post(
            f"{BASE_URL}/v1/companions/{self.companion_id}/chat",
            headers=request_headers,
            json=payload,
        )
        tok = time.time()
        print(f"Request took {tok - tik:.2f} seconds")

        if response.status_code == HTTPStatus.OK:
            data = response.json()
            assistant_msg = data["choices"][0]["message"]["content"]
            print(f"\nAssistant:\n{assistant_msg}")

            meta = data["choices"][0].get("emotion_machine", {}).get("metadata", {})
            print(
                f"\n[Context Engine: {meta.get('context_engine', 'unknown')}, Build: {meta.get('build_ms', 'N/A')}ms]"
            )
        else:
            print_response(response, "Error")

    def cleanup(self):
        """Clean up created resources (optional - comment out to keep)."""
        print_step(7, "Cleanup")

        cleanup_enabled = os.getenv("CLEANUP_AFTER_DEMO", "true").lower() == "true"

        if not cleanup_enabled:
            print("Cleanup disabled (set CLEANUP_AFTER_DEMO=true to enable)")
            print(f"Companion Manager ID: {self.companion_id}")
            print(f"Secret Name: {self.secret_name}")
            print(f"Spec ID: {self.spec_id}")
            return

        # Delete tool spec
        if self.spec_id and self.companion_id:
            response = self.client.delete(
                f"{BASE_URL}/v1/companions/{self.companion_id}/tools/{self.spec_id}",
                headers=headers(),
            )
            print(f"Deleted tool spec: {response.status_code == HTTPStatus.NO_CONTENT}")

        # Delete secret
        if self.secret_name:
            response = self.client.delete(
                f"{BASE_URL}/v1/secrets/{self.secret_name}", headers=headers()
            )
            print(f"Deleted secret: {response.status_code == HTTPStatus.NO_CONTENT}")

        # Delete companion manager
        if self.companion_id:
            response = self.client.delete(
                f"{BASE_URL}/v1/companions/{self.companion_id}", headers=headers()
            )
            print(f"Deleted companion manager: {response.status_code == HTTPStatus.NO_CONTENT}")


def main():
    print("=" * 70)
    print("Companion Manager Demo")
    print("=" * 70)
    print("""
This demo creates a "Companion Manager" - an AI assistant that can create
and manage other companions using the Emotion Machine API as tools.

The manager will:
1. List your existing companions
2. Create a new companion (Aria-chan) based on natural language description
""")
    print(f"Base URL: {BASE_URL}")
    print(f"API Key: {API_KEY[:25]}..." if API_KEY else "NOT SET")
    print(f"Spec Path: {SPEC_PATH}")

    demo = CompanionManagerDemo()
    demo.run()


if __name__ == "__main__":
    main()
