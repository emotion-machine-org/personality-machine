"""Tests for Modal LLM Node - Dedicated LLM function for behaviors.

This test validates the run_llm_node Modal function which provides LLM access
to ALL behaviors (including isolated ones) via Modal function-to-function calls.

Key Design:
- run_llm_node: Dedicated Modal function with network access for LLM calls
- Available to isolated behaviors because restrict_modal_access=False
- Solves the problem of isolated behaviors needing LLM access while being network-blocked

Run with: uv run python tests/test_v2_modal_llm_node.py

Requires:
- Modal deployment: modal deploy --env staging app/context/modal_behavior_executor.py
- Environment variables: OPENROUTER_API_KEY (in Modal secrets)
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add server directory to path for imports
server_dir = Path(__file__).parent.parent
sys.path.insert(0, str(server_dir))

try:
    import modal
except ImportError:
    print("Modal not installed. Run: pip install modal")
    sys.exit(1)


# =============================================================================
# Test Configuration
# =============================================================================

MODAL_APP_NAME = "em-context-behavior-executor"
MODAL_ENVIRONMENT = os.getenv("MODAL_ENVIRONMENT", "staging")

# Test prompts
SIMPLE_PROMPT = "What is 2 + 2? Reply with just the number."
SYSTEM_PROMPT = "You are a helpful math tutor. Be very concise."


def get_modal_function(function_name: str):
    """Get a Modal function with the correct environment."""
    return modal.Function.from_name(
        MODAL_APP_NAME,
        function_name,
        environment_name=MODAL_ENVIRONMENT,
    )


# =============================================================================
# Test 1: Direct LLM Node Call
# =============================================================================


async def test_run_llm_node_direct():
    """Test calling run_llm_node Modal function directly.

    This tests the LLM node function in isolation, verifying:
    - Function is accessible via modal.Function.from_name()
    - Returns a valid response from the LLM
    - Works with default and custom parameters
    """
    print("\n" + "=" * 60)
    print("Test 1: Direct LLM Node Call")
    print("=" * 60)

    # Get the LLM node function
    try:
        llm_fn = get_modal_function("run_llm_node")
    except modal.exception.NotFoundError:
        print("ERROR: run_llm_node function not found. Did you deploy the Modal app?")
        print(
            f"  Run: modal deploy --env {MODAL_ENVIRONMENT} app/context/modal_behavior_executor.py"
        )
        return False

    # Test 1a: Basic call with default parameters
    print("\n1a. Testing basic call with defaults...")
    start = datetime.now()

    try:
        result = await llm_fn.remote.aio(
            prompt=SIMPLE_PROMPT,
        )
        elapsed = (datetime.now() - start).total_seconds()

        print(f"  Response: {result}")
        print(f"  Time: {elapsed:.2f}s")

        if not result or not isinstance(result, str):
            print("  FAIL: Expected non-empty string response")
            return False

        # Should contain "4" somewhere in the response
        if "4" not in result:
            print(f"  WARNING: Expected '4' in response for '2 + 2', got: {result}")

        print("  PASS: Basic LLM call succeeded")

    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False

    # Test 1b: Call with system prompt and custom parameters
    print("\n1b. Testing call with system prompt and custom parameters...")
    start = datetime.now()

    try:
        result = await llm_fn.remote.aio(
            prompt="What is the square root of 16?",
            system=SYSTEM_PROMPT,
            model="google/gemini-2.0-flash-001:google-vertex",
            temperature=0.3,
            max_tokens=50,
        )
        elapsed = (datetime.now() - start).total_seconds()

        print(f"  Response: {result}")
        print(f"  Time: {elapsed:.2f}s")

        if not result or not isinstance(result, str):
            print("  FAIL: Expected non-empty string response")
            return False

        print("  PASS: Custom parameters call succeeded")

    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return False

    return True


# =============================================================================
# Test 2: LLM Node from Trusted Behavior
# =============================================================================


async def test_llm_node_from_trusted_behavior():
    """Test calling LLM from a trusted behavior via ctx.llm.run().

    This tests the flow:
    - execute_behavior_trusted is called with behavior code
    - Behavior code calls ctx.llm.run()
    - LLMNamespace.run() calls run_llm_node Modal function
    - Response is returned to the behavior
    """
    print("\n" + "=" * 60)
    print("Test 2: LLM Node from Trusted Behavior")
    print("=" * 60)

    # Get the trusted execution function
    try:
        trusted_fn = get_modal_function("execute_behavior_trusted")
    except modal.exception.NotFoundError:
        print("ERROR: execute_behavior_trusted function not found.")
        return False

    # Behavior code that uses ctx.llm.run()
    behavior_code = '''
async def execute(ctx):
    """Test behavior that calls LLM."""
    # Call the LLM via the ctx.llm namespace
    response = await ctx.llm.run(
        prompt="What is 3 + 5? Reply with just the number.",
        system="You are a helpful assistant. Be very concise.",
        temperature=0.3,
        max_tokens=50,
    )

    # Store the response in the trace for verification
    ctx.trace["llm_response"] = response

    # Return the response as the prompt block
    return f"LLM said: {response}"
'''

    # Context data for the behavior
    context_data = {
        "message": "test message",
        "companion_id": "test-companion",
        "turn_count": 1,
        "state": {
            "profile": {},
            "session": {},
            "messages": [],
        },
    }

    print("\n2a. Executing trusted behavior with LLM call...")
    start = datetime.now()

    try:
        result_json = await trusted_fn.remote.aio(behavior_code, json.dumps(context_data))
        elapsed = (datetime.now() - start).total_seconds()

        # Parse result
        if isinstance(result_json, str):
            result = json.loads(result_json)
        else:
            result = result_json

        print(f"  Prompt block: {result.get('prompt_block')}")
        print(f"  LLM response: {result.get('trace', {}).get('llm_response')}")
        print(f"  Time: {elapsed:.2f}s")

        # Verify response
        llm_response = result.get("trace", {}).get("llm_response")
        if not llm_response:
            print("  FAIL: No LLM response in trace")
            return False

        # Should contain "8" somewhere in the response
        if "8" not in llm_response:
            print(f"  WARNING: Expected '8' in response for '3 + 5', got: {llm_response}")

        print("  PASS: Trusted behavior LLM call succeeded")

    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


# =============================================================================
# Test 3: LLM Node from Isolated Behavior (Key Test!)
# =============================================================================


async def test_llm_node_from_isolated_behavior():
    """Test calling LLM from an ISOLATED behavior via ctx.llm.run().

    This is the KEY TEST that validates the new design:
    - execute_behavior_isolated has block_network=True (no direct HTTP)
    - But it has restrict_modal_access=False (CAN call other Modal functions)
    - ctx.llm.run() calls run_llm_node Modal function
    - run_llm_node has network access, so it can call OpenRouter

    This enables isolated behaviors to use LLM while remaining network-isolated
    for their own code execution.
    """
    print("\n" + "=" * 60)
    print("Test 3: LLM Node from ISOLATED Behavior (Key Test!)")
    print("=" * 60)

    # Get the isolated execution function
    try:
        isolated_fn = get_modal_function("execute_behavior_isolated")
    except modal.exception.NotFoundError:
        print("ERROR: execute_behavior_isolated function not found.")
        return False

    # Behavior code that uses ctx.llm.run()
    behavior_code = '''
async def execute(ctx):
    """Isolated behavior that calls LLM via Modal function."""
    # This should work even though block_network=True because:
    # 1. restrict_modal_access=False allows calling other Modal functions
    # 2. ctx.llm.run() internally calls run_llm_node Modal function
    # 3. run_llm_node has network access for the actual HTTP call

    response = await ctx.llm.run(
        prompt="What is 10 - 3? Reply with just the number.",
        system="You are a helpful assistant. Be very concise.",
        model="google/gemini-2.0-flash-001:google-vertex",
        temperature=0.3,
        max_tokens=50,
    )

    # Store the response in the trace for verification
    ctx.trace["llm_response"] = response
    ctx.trace["isolated"] = True

    # Return the response as the prompt block
    return f"Isolated behavior LLM response: {response}"
'''

    # Context data for the behavior
    context_data = {
        "message": "test isolated message",
        "companion_id": "test-companion",
        "turn_count": 1,
        "state": {
            "profile": {},
            "session": {},
            "messages": [],
        },
    }

    print("\n3a. Executing ISOLATED behavior with LLM call...")
    print("    (This tests block_network=True + restrict_modal_access=False)")
    start = datetime.now()

    try:
        result_json = await isolated_fn.remote.aio(behavior_code, json.dumps(context_data))
        elapsed = (datetime.now() - start).total_seconds()

        # Parse result
        if isinstance(result_json, str):
            result = json.loads(result_json)
        else:
            result = result_json

        print(f"  Prompt block: {result.get('prompt_block')}")
        print(f"  LLM response: {result.get('trace', {}).get('llm_response')}")
        print(f"  Isolated flag: {result.get('trace', {}).get('isolated')}")
        print(f"  Time: {elapsed:.2f}s")

        # Verify response
        llm_response = result.get("trace", {}).get("llm_response")
        if not llm_response:
            print("  FAIL: No LLM response in trace")
            return False

        # Should contain "7" somewhere in the response
        if "7" not in llm_response:
            print(f"  WARNING: Expected '7' in response for '10 - 3', got: {llm_response}")

        print("  PASS: ISOLATED behavior LLM call succeeded!")
        print("        This proves block_network doesn't block Modal function calls.")

    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


# =============================================================================
# Test 4: Verify Isolated Behavior Cannot Make Direct HTTP Calls
# =============================================================================


async def test_isolated_behavior_no_direct_http():
    """Verify that isolated behaviors cannot make direct HTTP calls.

    This is a sanity check to confirm that block_network=True is working,
    and that only Modal function calls (like run_llm_node) are allowed.
    """
    print("\n" + "=" * 60)
    print("Test 4: Verify Isolated Cannot Make Direct HTTP")
    print("=" * 60)

    # Get the isolated execution function
    try:
        isolated_fn = get_modal_function("execute_behavior_isolated")
    except modal.exception.NotFoundError:
        print("ERROR: execute_behavior_isolated function not found.")
        return False

    # Behavior code that tries to make a direct HTTP call
    behavior_code = '''
async def execute(ctx):
    """Isolated behavior that tries direct HTTP (should fail)."""
    import httpx

    try:
        # This should fail because block_network=True
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("https://httpbin.org/get")
            ctx.trace["http_status"] = response.status_code
            return "Direct HTTP succeeded (unexpected!)"
    except Exception as e:
        ctx.trace["http_error"] = str(e)
        return f"Direct HTTP failed as expected: {type(e).__name__}"
'''

    context_data = {
        "message": "test",
        "companion_id": "test-companion",
        "turn_count": 1,
        "state": {"profile": {}, "session": {}, "messages": []},
    }

    print("\n4a. Testing that direct HTTP is blocked in isolated behaviors...")
    start = datetime.now()

    try:
        result_json = await isolated_fn.remote.aio(behavior_code, json.dumps(context_data))
        elapsed = (datetime.now() - start).total_seconds()

        # Parse result
        if isinstance(result_json, str):
            result = json.loads(result_json)
        else:
            result = result_json

        print(f"  Prompt block: {result.get('prompt_block')}")
        print(f"  HTTP error: {result.get('trace', {}).get('http_error')}")
        print(f"  Time: {elapsed:.2f}s")

        # Should have an error
        http_error = result.get("trace", {}).get("http_error")
        http_status = result.get("trace", {}).get("http_status")

        if http_status:
            print(
                f"  FAIL: Direct HTTP succeeded (status={http_status}), network should be blocked!"
            )
            return False

        if http_error:
            print("  PASS: Direct HTTP blocked as expected")
            print("        LLM access only works via run_llm_node Modal function")
        else:
            print("  WARNING: No error captured, but no success either")

    except Exception as e:
        # The behavior itself failing is also acceptable
        print(f"  Behavior error (expected): {type(e).__name__}: {e}")
        print("  PASS: Network is properly blocked")

    return True


# =============================================================================
# Test 5: LLM with Conversation Context
# =============================================================================


async def test_llm_with_conversation_context():
    """Test LLM call that uses conversation context from the behavior.

    This tests a more realistic scenario where the LLM uses ctx.message
    and ctx.conversation_text() to generate a contextual response.
    """
    print("\n" + "=" * 60)
    print("Test 5: LLM with Conversation Context")
    print("=" * 60)

    # Get the trusted execution function
    try:
        trusted_fn = get_modal_function("execute_behavior_trusted")
    except modal.exception.NotFoundError:
        print("ERROR: execute_behavior_trusted function not found.")
        return False

    # Behavior code that uses conversation context
    behavior_code = '''
async def execute(ctx):
    """Behavior that uses context to generate insights."""
    # Get recent conversation as context
    conversation = ctx.conversation_text
    current_message = ctx.message

    # Build a contextual prompt
    prompt = f"""Based on the user's message: "{current_message}"

Previous conversation:
{conversation}

What single word best describes the user's mood? Reply with just one word."""

    response = await ctx.llm.run(
        prompt=prompt,
        system="You are an emotion analyst. Respond with a single word.",
        temperature=0.5,
        max_tokens=10,
    )

    # Store in profile
    ctx.profile.set("insights.last_mood", response.strip())
    ctx.trace["mood_detected"] = response.strip()

    return f"Detected mood: {response.strip()}"
'''

    # Context with conversation history
    context_data = {
        "message": "I'm feeling overwhelmed with all this work",
        "companion_id": "test-companion",
        "turn_count": 3,
        "state": {
            "profile": {},
            "session": {},
            "messages": [
                {"role": "user", "content": "Hi, how are you?"},
                {"role": "assistant", "content": "I'm doing well! How are you today?"},
                {"role": "user", "content": "Not great, lots of deadlines"},
            ],
        },
    }

    print("\n5a. Executing behavior with conversation context...")
    start = datetime.now()

    try:
        result_json = await trusted_fn.remote.aio(behavior_code, json.dumps(context_data))
        elapsed = (datetime.now() - start).total_seconds()

        # Parse result
        if isinstance(result_json, str):
            result = json.loads(result_json)
        else:
            result = result_json

        print(f"  Prompt block: {result.get('prompt_block')}")
        print(f"  Mood detected: {result.get('trace', {}).get('mood_detected')}")
        print(f"  Effects: {len(result.get('effects', []))} effects generated")
        print(f"  Time: {elapsed:.2f}s")

        # Verify we got a mood
        mood = result.get("trace", {}).get("mood_detected")
        if not mood:
            print("  FAIL: No mood detected")
            return False

        # Should have a state_patch effect
        effects = result.get("effects", [])
        state_patch = next((e for e in effects if e.get("type") == "state_patch"), None)
        if not state_patch:
            print("  FAIL: No state_patch effect for profile update")
            return False

        print(f"  State patch: {state_patch}")
        print("  PASS: Contextual LLM call succeeded")

    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


# =============================================================================
# Main
# =============================================================================


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Modal LLM Node Tests")
    print("=" * 60)
    print(f"Modal App: {MODAL_APP_NAME}")
    print(f"Environment: {MODAL_ENVIRONMENT}")
    print(f"Time: {datetime.now().isoformat()}")

    results = {
        "test_run_llm_node_direct": False,
        "test_llm_node_from_trusted_behavior": False,
        "test_llm_node_from_isolated_behavior": False,
        "test_isolated_behavior_no_direct_http": False,
        "test_llm_with_conversation_context": False,
    }

    # Run tests
    results["test_run_llm_node_direct"] = await test_run_llm_node_direct()
    results["test_llm_node_from_trusted_behavior"] = await test_llm_node_from_trusted_behavior()
    results["test_llm_node_from_isolated_behavior"] = await test_llm_node_from_isolated_behavior()
    results["test_isolated_behavior_no_direct_http"] = await test_isolated_behavior_no_direct_http()
    results["test_llm_with_conversation_context"] = await test_llm_with_conversation_context()

    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed = 0
    failed = 0
    for test_name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  {test_name}: {status}")
        if result:
            passed += 1
        else:
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
