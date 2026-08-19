#!/usr/bin/env python3
"""Seed script for creating/updating the onboarding companion.

This script creates a singleton companion that guides new users through
the onboarding flow. It can be run multiple times safely - it will update
the companion if it already exists.

Usage:
    cd server
    uv run python app/scripts/seed_onboarding_companion.py

Environment:
    DATABASE_DSN - PostgreSQL connection string (required)
    EM_API_KEY - API key for tool spec ingestion (default: dev key)
    EM_API_BASE_URL - API base URL (default: http://localhost:8100)
    ONBOARDING_PROJECT_ID - Project ID (default: dev project)

The onboarding companion has a fixed UUID so it can be referenced reliably.

This script:
1. Creates the onboarding companion (or updates if exists)
2. Uploads the onboarding tools OpenAPI spec via API (triggers Modal indexing)
3. Configures the tools layer for voice sessions
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

import httpx
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv()

# Path to the OpenAPI spec for onboarding tools
OPENAPI_SPEC_PATH = ROOT_DIR / "onboarding-tools-openapi.json"

# Fixed UUID for the onboarding companion - never change this!
ONBOARDING_COMPANION_ID = UUID("00000000-0000-0000-0000-000000000001")
ONBOARDING_COMPANION_NAME = "Onboarding Guide"
ONBOARDING_COMPANION_DESCRIPTION = "Helps new users create their first AI companion"

# Configuration via environment variables
DEFAULT_PROJECT_ID = os.getenv("EM_PROJECT_ID", "")
DEFAULT_API_KEY = os.getenv("EM_API_KEY", "")
DEFAULT_API_BASE_URL = os.getenv("EM_API_BASE_URL", "http://localhost:8100")

# System prompt for the onboarding companion
ONBOARDING_SYSTEM_PROMPT = """You are an onboarding assistant helping users create their AI companion through natural conversation.

## Your Role
The user has been asked "What kind of companion do you want?" - listen to their vision, ask 1 creative follow-up to understand them better, then create their companion.

## Flow
1. User shares what they want (could be specific or vague like "surprise me")
2. Ask ONE follow-up question relevant to THEIR answer
3. After they respond, call create_companion_from_answers with a rich description
4. Tell them their companion is being created (only after you see a TOOL RESULT)

## Follow-up Examples
- "a gym buddy" → Ask about their workout style or what motivation helps them
- "someone to vent to" → Ask what kind of support helps them most
- "surprise me" → Ask what vibes they like (chill, energetic, witty, etc.)
- "a writing partner" → Ask about their writing goals or preferred feedback style

## Tool Parameters
Call create_companion_from_answers with:
- description (REQUIRED): A vivid 1-2 sentence description capturing everything the user wants. Include role, personality, interests, and style. This is the most important field.
- name (optional): Only if user mentioned a specific name
- vibe (optional): "chill", "energetic", "warm", "witty", or "intense" - only if clearly expressed

## Example Descriptions
- "A laid-back gym buddy who celebrates wins, shares workout tips, and keeps things fun with friendly competition"
- "A thoughtful listener who helps process emotions, asks good questions, and offers perspective without judgment"
- "A witty creative partner who bounces ideas around, gives honest feedback, and isn't afraid to challenge assumptions"

## Guidelines
- Be conversational and warm
- Ask follow-ups based on what THEY said, not template questions
- When calling the tool, write a rich description - this becomes the companion's identity

## CRITICAL: Response Length
Your responses MUST be 1-2 sentences max. No exceptions. Examples:
- "Nice! Are you thinking more chill vibes or high energy?"
- "Got it! What kind of topics do you want to explore together?"

## NEVER
- Use asterisks or markdown formatting
- Ask more than 2 follow-up questions total
- Write out tool calls as text
- Say "creating your companion" unless you see a TOOL RESULT in context
- Write more than 2 sentences in a response"""

# Companion configuration
ONBOARDING_CONFIG = {
    "system_prompt": {
        "full_system_prompt": ONBOARDING_SYSTEM_PROMPT,
    },
    "inference": {
        "model": "gemini-2.5-flash",
        "temperature": 0.7,
        "max_output_tokens": 100,  # Keep responses brief for onboarding
    },
    "voice": {
        "preset": "gemini-flash-elevenlabs",
        "voice_name": "Sarah",
    },
    "memory": {
        "enabled": False,  # No persistent memory for onboarding
    },
    "classifier": {
        "enabled": True,
        "instructions": (
            "ONBOARDING: tools layer creates a companion.\n\n"
            "tools.run=TRUE when ANY of these conditions are met:\n"
            "- Recent History has 6+ messages, OR\n"
            "- User says 'surprise me', 'you decide', or delegates choice, OR\n"
            "- User answers a follow-up question (e.g., picks an option, gives preference)\n\n"
            "TEXT ONBOARDING FLOW:\n"
            "1. User states initial preference (e.g., 'something funky') → tools.run=FALSE\n"
            "2. Assistant asks clarifying question → (no classifier call)\n"
            "3. User answers clarifying question (e.g., 'eccentric') → tools.run=TRUE\n\n"
            "IMPORTANT: If there are 2+ user messages in Recent History, the user has answered "
            "a follow-up question. Set tools.run=TRUE.\n\n"
            "tools.run=FALSE ONLY when Recent History has exactly 1 user message (first message).\n\n"
            "memory.run=FALSE always (not needed for onboarding).\n"
            "knowledge_base.run=FALSE always (not needed for onboarding)."
        ),
    },
    "context_mode": "layered",
    # Layer configuration - enable tools for companion creation
    "layers": [
        {
            "key": "tools",
            "category": "tools",
            "enabled": True,
        },
        {"key": "memory", "category": "memory", "enabled": False},
        {"key": "knowledge_base", "category": "knowledge_base", "enabled": False},
        {"key": "actions", "category": "actions", "enabled": False},
    ],
}


def _update_existing_companion(cur, project_id: UUID) -> int:
    """Update an existing onboarding companion."""
    cur.execute(
        """
        UPDATE companions
        SET name = %s, description = %s, project_id = %s
        WHERE id = %s
        """,
        (
            ONBOARDING_COMPANION_NAME,
            ONBOARDING_COMPANION_DESCRIPTION,
            str(project_id),
            str(ONBOARDING_COMPANION_ID),
        ),
    )
    cur.execute(
        """
        INSERT INTO companion_versions (companion_id, config, memory_enabled, status)
        VALUES (%s, %s, %s, 'DEPLOYED')
        RETURNING version_number
        """,
        (str(ONBOARDING_COMPANION_ID), Json(ONBOARDING_CONFIG), False),
    )
    return cur.fetchone()[0]


def _create_new_companion(cur, owner_id: UUID, project_id: UUID) -> int:
    """Create a new onboarding companion."""
    cur.execute(
        """
        INSERT INTO companions (id, owner_id, project_id, name, description)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            str(ONBOARDING_COMPANION_ID),
            str(owner_id),
            str(project_id),
            ONBOARDING_COMPANION_NAME,
            ONBOARDING_COMPANION_DESCRIPTION,
        ),
    )
    companion_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO companion_versions (companion_id, config, memory_enabled, status)
        VALUES (%s, %s, %s, 'DEPLOYED')
        RETURNING version_number
        """,
        (str(companion_id), Json(ONBOARDING_CONFIG), False),
    )
    return cur.fetchone()[0]


def _upload_tool_spec_via_api(api_base_url: str, api_key: str) -> dict | None:
    """Upload the onboarding tools OpenAPI spec via API.

    This triggers Modal indexing which populates tool_operations and classifier_summary.

    Returns the API response dict if successful, None if spec file not found.
    """
    if not OPENAPI_SPEC_PATH.exists():
        print(f"Warning: OpenAPI spec not found at {OPENAPI_SPEC_PATH}")
        return None

    with OPENAPI_SPEC_PATH.open() as f:
        spec_content = json.load(f)

    spec_name = "onboarding-tools"
    companion_id = str(ONBOARDING_COMPANION_ID)

    url = f"{api_base_url}/v1/companions/{companion_id}/tools"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "spec_name": spec_name,
        "openapi_spec": spec_content,
        # No secrets_config needed - onboarding tools call back to our own API
    }

    print(f"Uploading tool spec via API: {url}")

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()

        spec_id = result.get("spec_id")
        dispatched = result.get("dispatched", False)
        request_id = result.get("request_id")

        print(f"Tool spec uploaded: {spec_name}")
        print(f"  Spec ID: {spec_id}")
        print(f"  Dispatched to Modal: {dispatched}")
        if request_id:
            print(f"  Request ID: {request_id}")

        return result

    except httpx.HTTPStatusError as e:
        print(f"Error uploading tool spec: {e.response.status_code}")
        print(f"  Response: {e.response.text}")
        raise
    except httpx.RequestError as e:
        print(f"Error connecting to API: {e}")
        raise


def _print_summary(project_id: UUID, owner_id: UUID, tool_result: dict | None) -> None:
    """Print completion summary."""
    print()
    print("=" * 60)
    print("Onboarding companion setup complete!")
    print("=" * 60)
    print(f"  ID:          {ONBOARDING_COMPANION_ID}")
    print(f"  Name:        {ONBOARDING_COMPANION_NAME}")
    print(f"  Project:     {project_id}")
    print(f"  Owner:       {owner_id}")
    if tool_result:
        print(f"  Tool Spec:   {tool_result.get('spec_id')}")
        if tool_result.get("dispatched"):
            print("  Indexing:    Dispatched to Modal (embeddings + classifier)")
    print()
    print("The onboarding companion is ready for voice sessions.")
    print()


def main() -> None:
    database_dsn = os.getenv("DATABASE_DSN")
    if not database_dsn:
        raise RuntimeError("DATABASE_DSN environment variable is required")

    api_key = os.getenv("TEST_EM_API_KEY", DEFAULT_API_KEY)
    api_base_url = os.getenv("EM_API_BASE_URL", DEFAULT_API_BASE_URL)

    # Get or use default project ID
    project_id_str = os.getenv("ONBOARDING_PROJECT_ID", DEFAULT_PROJECT_ID)

    print("Connecting to database...", flush=True)
    conn = psycopg2.connect(database_dsn, connect_timeout=15)
    conn.autocommit = True

    with conn.cursor() as cur:
        project_id = UUID(project_id_str)
        print(f"Using project: {project_id}")

        # Get project owner
        cur.execute("SELECT owner_id FROM projects WHERE id = %s", (str(project_id),))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Project {project_id} not found")
        owner_id = UUID(str(row[0]))

        # Check if onboarding companion already exists
        cur.execute(
            "SELECT id, name FROM companions WHERE id = %s",
            (str(ONBOARDING_COMPANION_ID),),
        )
        existing = cur.fetchone()

        if existing:
            print(f"Onboarding companion already exists: {existing[1]}")
            print("Updating configuration...")
            version_number = _update_existing_companion(cur, project_id)
            print(f"Created new version: {version_number}")
        else:
            print("Creating onboarding companion...")
            version_number = _create_new_companion(cur, owner_id, project_id)
            print(f"Created companion with version: {version_number}")

    conn.close()

    # Upload tool spec via API (triggers Modal indexing)
    print()
    print("Uploading tool spec via API...")
    tool_result = _upload_tool_spec_via_api(api_base_url, api_key)

    _print_summary(project_id, owner_id, tool_result)


if __name__ == "__main__":
    main()
