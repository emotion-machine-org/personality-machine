"""Test script for chat_helpers.py - hybrid mode selection and turn context building.

Run from server directory: uv run python tests/test_chat_helpers.py
"""

import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.context.chat_helpers import (
    ModeResolution,
    TurnContextResult,
    build_turn_context,
    resolve_context_mode,
)


def test_resolve_context_mode():
    """Test resolve_context_mode() hybrid selection logic."""
    print("\n=== Testing resolve_context_mode ===")
    all_passed = True

    # Test 1: Request override 'layered' takes priority
    result = resolve_context_mode(request_override="layered", companion_config=None)
    if result.use_layered and result.source == "request_override":
        print("✓ Request override 'layered' works")
    else:
        print("✗ Request override 'layered' failed")
        all_passed = False

    # Test 2: Request override 'v2' is treated as layered
    result = resolve_context_mode(request_override="v2", companion_config=None)
    if result.use_layered and result.source == "request_override":
        print("✓ Request override 'v2' treated as layered")
    else:
        print("✗ Request override 'v2' failed")
        all_passed = False

    # Test 3: Request override 'legacy' works
    result = resolve_context_mode(request_override="legacy", companion_config=None)
    if not result.use_layered and result.source == "request_override":
        print("✓ Request override 'legacy' works")
    else:
        print("✗ Request override 'legacy' failed")
        all_passed = False

    # Test 4: Case insensitive
    result = resolve_context_mode(request_override="LAYERED", companion_config=None)
    if result.use_layered:
        print("✓ Request override is case-insensitive")
    else:
        print("✗ Case sensitivity failed")
        all_passed = False

    # Test 5: Companion config 'layered'
    config = MagicMock()
    config.context_mode = "layered"
    result = resolve_context_mode(request_override=None, companion_config=config)
    if result.use_layered and result.source == "companion_config":
        print("✓ Companion config 'layered' works")
    else:
        print("✗ Companion config 'layered' failed")
        all_passed = False

    # Test 6: Companion config 'legacy'
    config = MagicMock()
    config.context_mode = "legacy"
    result = resolve_context_mode(request_override=None, companion_config=config)
    if not result.use_layered and result.source == "companion_config":
        print("✓ Companion config 'legacy' works")
    else:
        print("✗ Companion config 'legacy' failed")
        all_passed = False

    # Test 7: No context_mode attribute defaults correctly
    config = MagicMock(spec=[])  # No context_mode
    result = resolve_context_mode(request_override=None, companion_config=config)
    if not result.use_layered and result.source == "default":
        print("✓ Missing context_mode defaults to legacy")
    else:
        print("✗ Missing context_mode handling failed")
        all_passed = False

    # Test 8: No config defaults to legacy
    result = resolve_context_mode(request_override=None, companion_config=None)
    if not result.use_layered and result.source == "default":
        print("✓ No config defaults to legacy")
    else:
        print("✗ No config default failed")
        all_passed = False

    # Test 9: Request override beats companion config
    config = MagicMock()
    config.context_mode = "layered"
    result = resolve_context_mode(request_override="legacy", companion_config=config)
    if not result.use_layered and result.source == "request_override":
        print("✓ Request override beats companion config")
    else:
        print("✗ Priority order failed")
        all_passed = False

    return all_passed


async def test_build_turn_context_legacy():
    """Test build_turn_context() in legacy mode."""
    print("\n=== Testing build_turn_context (legacy mode) ===")
    all_passed = True

    mock_conn = AsyncMock()
    companion_id = uuid4()

    # Test 1: Legacy mode calls build_transient_memory_block
    with patch(
        "app.context.chat_helpers._build_legacy_turn_context",
        new_callable=AsyncMock,
    ) as mock_legacy:
        mock_legacy.return_value = TurnContextResult(
            messages=[{"role": "system", "content": "# MEMORIES\n- Test"}],
            events=[],
            effects=[],
            plan=None,
        )

        result = await build_turn_context(
            mock_conn,
            companion_id=companion_id,
            companion_config=None,
            conversation_id=None,
            user_message="Hello",
            external_user_id="user-123",
            use_layered=False,
        )

        if mock_legacy.called:
            print("✓ Legacy mode calls _build_legacy_turn_context")
        else:
            print("✗ Legacy mode didn't call correct function")
            all_passed = False

        if len(result.messages) == 1 and result.messages[0]["role"] == "system":
            print("✓ Legacy mode returns system message")
        else:
            print("✗ Legacy mode message format wrong")
            all_passed = False

        if result.events == [] and result.effects == [] and result.plan is None:
            print("✓ Legacy mode has no events/effects/plan")
        else:
            print("✗ Legacy mode should not have events/effects/plan")
            all_passed = False

    return all_passed


async def test_build_turn_context_layered():
    """Test build_turn_context() in layered mode."""
    print("\n=== Testing build_turn_context (layered mode) ===")
    all_passed = True

    mock_conn = AsyncMock()
    companion_id = uuid4()
    conversation_id = uuid4()

    # Test: Layered mode calls build_context_plan
    mock_plan = MagicMock()
    mock_plan.messages = [
        {"role": "system", "content": "# MEMORIES\n- Memory 1"},
        {"role": "system", "content": "# KNOWLEDGE\n- Knowledge 1"},
        {"role": "user", "content": "Should be filtered"},
    ]
    mock_plan.events = [MagicMock()]
    mock_plan.effects = [MagicMock()]

    with patch(
        "app.context.chat_helpers.build_context_plan",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_build.return_value = mock_plan

        result = await build_turn_context(
            mock_conn,
            companion_id=companion_id,
            companion_config=MagicMock(),
            conversation_id=conversation_id,
            user_message="Tell me about X",
            external_user_id="user-123",
            use_layered=True,
            include_knowledge=True,
        )

        if mock_build.called:
            print("✓ Layered mode calls build_context_plan")
        else:
            print("✗ Layered mode didn't call build_context_plan")
            all_passed = False

        # Check call arguments
        call_kwargs = mock_build.call_args.kwargs
        if call_kwargs.get("include_memory") is True:
            print("✓ Layered mode enables memory")
        else:
            print("✗ Layered mode should enable memory")
            all_passed = False

        if call_kwargs.get("context_mode_override") == "layered":
            print("✓ Layered mode sets context_mode_override")
        else:
            print("✗ Layered mode should set context_mode_override")
            all_passed = False

        # Check result filtering (only system messages)
        if len(result.messages) == 2:  # user message filtered out
            print("✓ Layered mode filters non-system messages")
        else:
            print(f"✗ Expected 2 messages, got {len(result.messages)}")
            all_passed = False

        if len(result.events) == 1 and len(result.effects) == 1:
            print("✓ Layered mode returns events and effects")
        else:
            print("✗ Layered mode should return events and effects")
            all_passed = False

        if result.plan == mock_plan:
            print("✓ Layered mode returns plan")
        else:
            print("✗ Layered mode should return plan")
            all_passed = False

    return all_passed


async def test_build_turn_context_emit_events():
    """Test emit_events parameter controls event callback."""
    print("\n=== Testing emit_events parameter ===")
    all_passed = True

    mock_conn = AsyncMock()
    mock_plan = MagicMock()
    mock_plan.messages = []
    mock_plan.events = []
    mock_plan.effects = []

    with patch(
        "app.context.chat_helpers.build_context_plan",
        new_callable=AsyncMock,
    ) as mock_build:
        mock_build.return_value = mock_plan

        # Test with emit_events=False (voice mode)
        await build_turn_context(
            mock_conn,
            companion_id=uuid4(),
            companion_config=MagicMock(),
            conversation_id=None,
            user_message="Hello",
            use_layered=True,
            emit_events=False,
        )

        call_kwargs = mock_build.call_args.kwargs
        if call_kwargs.get("event_callback") is None:
            print("✓ emit_events=False passes None callback")
        else:
            print("✗ emit_events=False should pass None callback")
            all_passed = False

    return all_passed


async def main():
    print("=" * 60)
    print("Testing chat_helpers.py - Hybrid Mode Selection")
    print("=" * 60)

    results = []

    # Sync tests
    results.append(("resolve_context_mode", test_resolve_context_mode()))

    # Async tests
    results.append(("build_turn_context (legacy)", await test_build_turn_context_legacy()))
    results.append(("build_turn_context (layered)", await test_build_turn_context_layered()))
    results.append(("emit_events parameter", await test_build_turn_context_emit_events()))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\n✓ All tests passed!")
        return 0
    else:
        print("\n✗ Some tests failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
