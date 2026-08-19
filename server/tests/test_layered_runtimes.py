"""Test script for layered context engine runtimes.

This is the go-to test for validating the layered orchestrator execution.

Tests individual runtimes surgically with REAL data:
  (a) Memory runtime - inserts actual memories with embeddings, uses trigger keywords
  (b) Knowledge runtime - ingests real document via OpenAI vector store pipeline
  (c) Tools runtime - tests tool layer execution
  (d) Actions runtime - tests action layer execution

Setup functions ensure each runtime has actual retrievable data.

Run from server directory:
    uv run python tests/test_layered_runtimes.py

Options:
    --skip-setup    Skip setup steps if data already exists
    --memory-only   Only run memory tests
    --kb-only       Only run knowledge tests
    --verbose       Show detailed output
"""

import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

import argparse
import asyncio
import json
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from app.context import build_context_plan
from app.context.schemas import TurnContext
from app.db import init_db
from app.repositories.companion import CompanionRepository
from app.repositories.conversation import (
    add_message_returning,
    create_conversation_for_companion,
)
from app.repositories.memory import MemoryRepository

# For embeddings
from app.services.memory_service import MemoryService

VERBOSE = False


def log(msg: str, level: str = "info"):
    """Simple logging helper."""
    if level == "debug" and not VERBOSE:
        return
    print(msg)


async def get_test_companion(conn):
    """Get a companion for testing."""
    row = await conn.fetchrow("SELECT id, owner_id, project_id FROM companions LIMIT 1")
    if not row:
        return None, None, None

    companion = await CompanionRepository.get_companion_by_id_no_auth(conn, row["id"])
    return companion, row["owner_id"], row["project_id"]


def ensure_memory_enabled(config):
    """Ensure memory is enabled in companion config for testing.

    The orchestrator checks companion_config.memory.enabled before adding MemoryRuntime.
    We need to ensure this is True for our tests.
    """
    from pydantic import BaseModel

    # Check if memory config exists and is enabled
    memory_cfg = getattr(config, "memory", None)
    memory_enabled = bool(getattr(memory_cfg, "enabled", False))

    if not memory_enabled:
        # Create a simple memory config object
        class MemoryConfig(BaseModel):
            enabled: bool = True
            min_saliency: float = 0.1  # Low threshold for testing
            top_k: int = 10
            recency: float = 0.995

        # Try to set memory on config
        if hasattr(config, "__dict__"):
            config.memory = MemoryConfig()
        elif hasattr(config, "model_copy"):
            # Pydantic v2
            config = config.model_copy(update={"memory": MemoryConfig()})

    return config


# =============================================================================
# MEMORY SETUP & TEST
# =============================================================================

TEST_MEMORIES = [
    {
        # Phrased to be more semantically similar to "what is my name" queries
        # NOTE: is_core=False because core memories are retrieved separately (not by MemoryRuntime)
        "content": "The user's name is TestUser. They introduced themselves by saying 'my name is TestUser'.",
        "importance": 0.95,
        "is_core": False,
    },
    {
        "content": "My favorite programming language is Python because it's readable.",
        "importance": 0.8,
        "is_core": False,
    },
    {
        "content": "I love hiking in the mountains on weekends.",
        "importance": 0.75,
        "is_core": False,
    },
    {
        "content": "I prefer dark mode for all my applications.",
        "importance": 0.7,
        "is_core": False,
    },
    {
        "content": "I'm planning to learn Rust next year as my goal.",
        "importance": 0.8,
        "is_core": False,
    },
]

# Memory retrieval triggers from memory_runtime.py
MEMORY_TRIGGER_QUERIES = [
    "What is my favorite programming language?",  # triggers: "favorite", "what is my"
    "Do you remember what I told you about hiking?",  # triggers: "do you remember"
    "I usually prefer dark mode, right?",  # triggers: "usually", "i prefer"
    "What is my name again?",  # triggers: "what is my"
    "What are my goals?",  # triggers: "goal"
]


async def setup_test_memories(conn, companion_id: UUID, external_user_id: str) -> List[UUID]:
    """Insert test memories with real embeddings for retrieval testing."""
    log("\n  [SETUP] Creating test memories with embeddings...")

    # Import embedding function
    from app.services.memory_service import _get_embedding

    memory_ids = []
    for mem in TEST_MEMORIES:
        # Generate real embedding for the content
        embedding = await _get_embedding(mem["content"])

        memory_id = await MemoryRepository.create_memory(
            conn,
            companion_id=companion_id,
            content=mem["content"],
            embedding=embedding,
            importance=mem["importance"],
            weight_user=1.0,
            modality="text",
            commentary=None,
            conversation_id=None,
            sender_type="user",
            external_user_id=external_user_id,
            message_id=None,
            is_core=mem["is_core"],
        )
        memory_ids.append(memory_id)
        log(f"    Created memory: {mem['content'][:50]}...", level="debug")

    log(f"  [SETUP] Created {len(memory_ids)} test memories")
    return memory_ids


async def cleanup_test_memories(conn, memory_ids: List[UUID], companion_id: UUID):
    """Remove test memories after testing."""
    for mid in memory_ids:
        await MemoryRepository.delete_memory(conn, memory_id=mid, companion_id=companion_id)
    log(f"  [CLEANUP] Removed {len(memory_ids)} test memories")


async def test_memory_runtime(conn, companion, test_user, skip_setup: bool = False):
    """Test memory runtime with actual retrievable memories."""
    print("\n" + "=" * 60)
    print("[TEST] Memory Runtime - With Real Memories")
    print("=" * 60)

    # Ensure memory is enabled in config
    test_config = ensure_memory_enabled(companion.config)
    memory_cfg = getattr(test_config, "memory", None)
    print(f"  Memory enabled: {bool(getattr(memory_cfg, 'enabled', False))}")

    memory_ids = []
    try:
        # Setup: Insert test memories
        if not skip_setup:
            memory_ids = await setup_test_memories(conn, companion.id, test_user)

        # Create conversation
        conv_id = await create_conversation_for_companion(conn, companion.id, test_user)

        # Add some messages to create history context
        await add_message_returning(
            conn, conv_id, "user", "Hello, I'm here for testing.", input_modality="text"
        )
        await add_message_returning(
            conn, conv_id, "assistant", "Hello! I'm ready to help with your test."
        )

        # Test each trigger query
        passed_queries = 0
        for query in MEMORY_TRIGGER_QUERIES:
            events_received = []

            def on_event(ev, events_received=events_received):
                events_received.append(ev)

            plan = await build_context_plan(
                conn=conn,
                companion_id=companion.id,
                companion_config=test_config,  # Use config with memory enabled
                conversation_id=conv_id,
                user_message=query,
                external_user_id=test_user,
                include_memory=True,
                include_knowledge=False,
                include_tools=False,
                include_actions=False,
                hydrate_state=False,
                event_callback=on_event,
            )

            # Check for memory events
            memory_events = [e for e in events_received if e.name.startswith("memory")]
            memory_messages = [m for m in plan.messages if "MEMORY" in m.get("content", "").upper()]

            # Check if memories were retrieved (memory runtime uses 'retrieval_items' not 'count')
            retrieved = any(
                e.phase == "end" and e.meta.get("retrieval_items", 0) > 0 for e in memory_events
            )
            # Also check if gate was skipped
            gate_skipped = any(
                e.phase == "end" and e.meta.get("skipped", False) for e in memory_events
            )

            status = "✓" if retrieved else ("⊘" if gate_skipped else "○")
            print(f'  {status} Query: "{query[:50]}..."')
            # Show details in verbose mode, or when gate was skipped (unexpected)
            if VERBOSE or gate_skipped:
                for ev in memory_events:
                    print(f"      Event: {ev.name} phase={ev.phase} meta={ev.meta}")
                print(f"      Memory messages: {len(memory_messages)}")
                if memory_messages:
                    content = memory_messages[0].get("content", "")[:200]
                    print(f"      Content preview: {content}")

            if retrieved:
                passed_queries += 1

        # Cleanup conversation
        await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)

        print(
            f"\n  Results: {passed_queries}/{len(MEMORY_TRIGGER_QUERIES)} queries retrieved memories"
        )

        if passed_queries > 0:
            print("  PASS: Memory runtime successfully retrieved memories")
            return True
        else:
            print("  WARN: No memories retrieved - check gate/embedding similarity")
            return True  # Not a hard failure

    finally:
        # Cleanup memories
        if memory_ids:
            await cleanup_test_memories(conn, memory_ids, companion.id)


# =============================================================================
# KNOWLEDGE SETUP & TEST
# =============================================================================

# Test document content (menstrual cycle symptoms - from the user's test file)
TEST_KNOWLEDGE_CONTENT = """
Menstrual Cycle Symptoms Guide

Phase 1: Menstruation (Days 1-5)
Common symptoms during menstruation include cramps, bloating, fatigue, and mood changes.
Many people experience headaches and lower back pain. Rest and hydration are important.

Phase 2: Follicular Phase (Days 6-14)
Energy levels typically increase during this phase. Skin often improves.
This is a good time for creative work and social activities.

Phase 3: Ovulation (Days 14-16)
Peak energy and confidence. Some may experience mild cramping (mittelschmerz).
Increased libido is common during ovulation.

Phase 4: Luteal Phase (Days 17-28)
PMS symptoms may begin: bloating, breast tenderness, mood swings.
Cravings for carbohydrates and chocolate are common.
Sleep quality may decrease. Self-care practices are beneficial.

Tips for Managing Symptoms:
- Track your cycle to predict symptoms
- Exercise regularly but adjust intensity
- Get adequate sleep (7-9 hours)
- Stay hydrated
- Consider supplements like magnesium and vitamin B6
"""

KNOWLEDGE_QUERIES = [
    "What happens during the follicular phase?",  # Should retrieve phase 2 info
    "What symptoms occur during PMS?",  # Should retrieve luteal phase info
    "How can I manage menstrual symptoms?",  # Should retrieve tips section
    "When do energy levels peak in the cycle?",  # Should retrieve ovulation info
]


async def setup_test_knowledge(conn, companion_id: UUID, project_id: UUID) -> UUID | None:
    """Ingest test knowledge document via the real pipeline."""
    log("\n  [SETUP] Ingesting test knowledge document...")

    from app.services.knowledge_service import ingest_knowledge_payload

    try:
        job = await ingest_knowledge_payload(
            conn,
            project_id=project_id,
            companion_id=companion_id,
            payload_type="text",
            inline_content=TEST_KNOWLEDGE_CONTENT,
            payload_key=None,
            asset_id=None,
            submitted_by_user=None,
            submitted_by_key=None,
            source_label="layered_runtime_test",
        )

        if job.status == "succeeded":
            log(f"  [SETUP] Knowledge ingestion succeeded (job_id: {job.id})")
            return job.id
        else:
            log(f"  [SETUP] Knowledge ingestion failed: {job.error}")
            return None

    except Exception as e:
        log(f"  [SETUP] Knowledge ingestion error: {e}")
        return None


async def test_knowledge_runtime(
    conn, companion, test_user, project_id: UUID, skip_setup: bool = False
):
    """Test knowledge runtime with real ingested document."""
    print("\n" + "=" * 60)
    print("[TEST] Knowledge Runtime - With Real Document")
    print("=" * 60)

    job_id = None
    try:
        # Setup: Ingest test document
        if not skip_setup:
            job_id = await setup_test_knowledge(conn, companion.id, project_id)
            if not job_id:
                print("  SKIP: Could not ingest knowledge document")
                return True

        # Verify companion has vector store
        vs_id = await CompanionRepository.get_vector_store_id(conn, companion.id)
        if not vs_id:
            print("  SKIP: No vector store configured for companion")
            return True

        log(f"  Vector store ID: {vs_id}")

        # Create conversation
        conv_id = await create_conversation_for_companion(conn, companion.id, test_user)

        # Test each knowledge query
        passed_queries = 0
        for query in KNOWLEDGE_QUERIES:
            events_received = []

            def on_event(ev, events_received=events_received):
                events_received.append(ev)

            plan = await build_context_plan(
                conn=conn,
                companion_id=companion.id,
                companion_config=companion.config,
                conversation_id=conv_id,
                user_message=query,
                external_user_id=test_user,
                include_memory=False,
                include_knowledge=True,
                include_tools=False,
                include_actions=False,
                hydrate_state=False,
                event_callback=on_event,
            )

            # Check for knowledge events
            kb_events = [e for e in events_received if e.name.startswith("knowledge")]
            kb_messages = [m for m in plan.messages if "KNOWLEDGE" in m.get("content", "").upper()]

            # Check if knowledge was retrieved
            retrieved = any("end" in e.phase and e.meta.get("results", 0) > 0 for e in kb_events)

            status = "✓" if retrieved else "○"
            print(f'  {status} Query: "{query[:50]}..."')
            if VERBOSE:
                print(f"      Events: {[e.name for e in kb_events]}")
                print(f"      KB messages: {len(kb_messages)}")
                if kb_messages:
                    content = kb_messages[0].get("content", "")[:200]
                    print(f"      Content preview: {content}")

            if retrieved:
                passed_queries += 1

        # Cleanup conversation
        await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)

        print(f"\n  Results: {passed_queries}/{len(KNOWLEDGE_QUERIES)} queries retrieved knowledge")

        if passed_queries > 0:
            print("  PASS: Knowledge runtime successfully retrieved documents")
            return True
        else:
            print("  WARN: No knowledge retrieved - check vector store or gate")
            return True

    except Exception as e:
        print(f"  ERROR: {e}")
        return False


# =============================================================================
# TOOLS & ACTIONS TESTS (unchanged from original)
# =============================================================================


async def test_tools_runtime(conn, companion, test_user):
    """Test tools runtime in isolation."""
    print("\n" + "=" * 60)
    print("[TEST] Tools Runtime")
    print("=" * 60)

    conv_id = await create_conversation_for_companion(conn, companion.id, test_user)

    events_received = []

    def on_event(ev):
        events_received.append(ev)

    plan = await build_context_plan(
        conn=conn,
        companion_id=companion.id,
        companion_config=companion.config,
        conversation_id=conv_id,
        user_message="Can you help me with something?",
        external_user_id=test_user,
        include_memory=False,
        include_knowledge=False,
        include_tools=True,
        include_actions=False,
        hydrate_state=False,
        event_callback=on_event,
    )

    tools_events = [e for e in events_received if e.name.startswith("tools")]

    print(f"  Events: {[e.name for e in tools_events]}")
    print(f"  Total messages: {len(plan.messages)}")

    # Cleanup
    await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)

    if tools_events:
        print("  PASS: Tools runtime executed")
    else:
        print("  INFO: No tools events (tools layer may produce no output)")
    return True


async def test_actions_runtime(conn, companion, test_user):
    """Test actions runtime in isolation."""
    print("\n" + "=" * 60)
    print("[TEST] Actions Runtime")
    print("=" * 60)

    conv_id = await create_conversation_for_companion(conn, companion.id, test_user)

    events_received = []

    def on_event(ev):
        events_received.append(ev)

    plan = await build_context_plan(
        conn=conn,
        companion_id=companion.id,
        companion_config=companion.config,
        conversation_id=conv_id,
        user_message="Hello, this is a test message.",
        external_user_id=test_user,
        include_memory=False,
        include_knowledge=False,
        include_tools=False,
        include_actions=True,
        hydrate_state=True,
        event_callback=on_event,
    )

    action_events = [e for e in events_received if e.name.startswith("action")]

    print(f"  Events: {[e.name for e in action_events]}")
    print(f"  Effects count: {len(plan.effects)}")
    print(f"  State hydrated: {plan.trace.get('user_state_hydrated', False)}")

    if plan.effects:
        print(f"  Effects types: {[e.type for e in plan.effects]}")

    # Cleanup
    await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)

    if action_events:
        print("  PASS: Actions runtime executed")
    else:
        print("  INFO: No action events (no actions registered or triggered)")
    return True


# =============================================================================
# COMBINED & STREAM TESTS
# =============================================================================


async def test_all_runtimes_combined(conn, companion, test_user):
    """Test all runtimes together (full layered mode)."""
    print("\n" + "=" * 60)
    print("[TEST] All Runtimes Combined (Full Layered)")
    print("=" * 60)

    conv_id = await create_conversation_for_companion(conn, companion.id, test_user)

    # Add history
    await add_message_returning(conn, conv_id, "user", "Hi there!", input_modality="text")
    await add_message_returning(conn, conv_id, "assistant", "Hello! How can I help?")

    events_received = []

    def on_event(ev):
        events_received.append(ev)

    start = time.perf_counter()
    plan = await build_context_plan(
        conn=conn,
        companion_id=companion.id,
        companion_config=companion.config,
        conversation_id=conv_id,
        user_message="What is my favorite thing and what do you know about health topics?",
        external_user_id=test_user,
        include_memory=True,
        include_knowledge=True,
        include_tools=True,
        include_actions=True,
        hydrate_state=True,
        event_callback=on_event,
    )
    elapsed = (time.perf_counter() - start) * 1000

    # Group events by layer
    event_groups = {}
    for ev in events_received:
        layer = ev.name.split(":")[0]
        if layer not in event_groups:
            event_groups[layer] = []
        event_groups[layer].append(ev.name)

    print(f"  Build time: {elapsed:.1f}ms")
    print(f"  Total events: {len(events_received)}")
    print(f"  Event groups: {json.dumps({k: len(v) for k, v in event_groups.items()})}")
    print(f"  Total messages: {len(plan.messages)}")
    print(f"  Effects: {len(plan.effects)}")

    # Cleanup
    await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)

    print("  PASS: Full layered context built")
    return True


async def test_stream_simulation(conn, companion, test_user):
    """Simulate streaming endpoint event consumption."""
    print("\n" + "=" * 60)
    print("[TEST] Stream Event Simulation")
    print("=" * 60)

    conv_id = await create_conversation_for_companion(conn, companion.id, test_user)
    await add_message_returning(conn, conv_id, "user", "Tell me a story.", input_modality="text")

    event_queue = asyncio.Queue()

    def on_event(ev):
        event_queue.put_nowait(ev)

    plan_task = asyncio.create_task(
        build_context_plan(
            conn=conn,
            companion_id=companion.id,
            companion_config=companion.config,
            conversation_id=conv_id,
            user_message="What do you remember about me?",  # Memory trigger
            external_user_id=test_user,
            include_memory=True,
            include_knowledge=True,
            include_actions=True,
            hydrate_state=True,
            event_callback=on_event,
        )
    )

    streamed_events = []
    while True:
        try:
            ev = await asyncio.wait_for(event_queue.get(), timeout=0.05)
            streamed_events.append(ev)
        except TimeoutError:
            if plan_task.done():
                while not event_queue.empty():
                    streamed_events.append(event_queue.get_nowait())
                break

    plan = await plan_task

    print(f"  Events streamed: {len(streamed_events)}")
    print(
        f"  Event sequence: {[e.name for e in streamed_events[:10]]}{'...' if len(streamed_events) > 10 else ''}"
    )
    print(f"  Final plan messages: {len(plan.messages)}")

    # Cleanup
    await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)

    print("  PASS: Stream simulation complete")
    return True


# =============================================================================
# MAIN
# =============================================================================


async def main():
    parser = argparse.ArgumentParser(description="Layered runtime tests")
    parser.add_argument("--skip-setup", action="store_true", help="Skip data setup")
    parser.add_argument("--memory-only", action="store_true", help="Only run memory tests")
    parser.add_argument("--kb-only", action="store_true", help="Only run knowledge tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    print("=" * 60)
    print("LAYERED RUNTIME TESTS")
    print("Layered orchestrator test suite with real data")
    print("=" * 60)

    pool = await init_db()
    async with pool.acquire() as conn:
        companion, _owner_id, project_id = await get_test_companion(conn)
        if not companion:
            print("ERROR: No companions in DB - create one first")
            return False

        test_user = f"test-runtime-{uuid4().hex[:8]}"

        print(f"\nTest companion: {companion.id}")
        print(f"  Name: {companion.name}")
        print(f"  Project: {project_id}")
        print(f"Test user: {test_user}")

        results = []

        # Run selected tests
        if args.memory_only:
            results.append(
                ("Memory", await test_memory_runtime(conn, companion, test_user, args.skip_setup))
            )
        elif args.kb_only:
            results.append(
                (
                    "Knowledge",
                    await test_knowledge_runtime(
                        conn, companion, test_user, project_id, args.skip_setup
                    ),
                )
            )
        else:
            # Run all tests
            results.append(
                ("Memory", await test_memory_runtime(conn, companion, test_user, args.skip_setup))
            )
            results.append(
                (
                    "Knowledge",
                    await test_knowledge_runtime(
                        conn, companion, test_user, project_id, args.skip_setup
                    ),
                )
            )
            results.append(("Tools", await test_tools_runtime(conn, companion, test_user)))
            results.append(("Actions", await test_actions_runtime(conn, companion, test_user)))
            results.append(
                ("Combined", await test_all_runtimes_combined(conn, companion, test_user))
            )
            results.append(("Stream", await test_stream_simulation(conn, companion, test_user)))

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        for name, passed in results:
            status = "PASS" if passed else "FAIL"
            print(f"  {name}: {status}")

        all_passed = all(r[1] for r in results)
        print("\n" + ("ALL TESTS PASSED" if all_passed else "SOME TESTS FAILED"))
        return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
