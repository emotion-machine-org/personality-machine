"""Modal Behavior Executor: Executes behaviors in Modal Functions and Sandboxes.

This module provides:
1. execute_behavior_trusted() - Fast path for trusted behaviors (warm containers)
2. execute_behavior_isolated() - Secure path for untrusted behaviors (fresh containers)
3. run_llm_node() - Dedicated LLM function accessible by ALL behaviors (including isolated)
4. BehaviorExecutor class - For async job processing via polling
5. poll_behavior_jobs() - Scheduled polling for pending async jobs

Architecture:
- Priority behaviors use execute_behavior_trusted/isolated (called from FastAPI)
- Async behaviors use BehaviorExecutor via poll_behavior_jobs (background processing)
- Developer code is stored in DB and injected at runtime via exec()
- LLM access: ALL behaviors can call ctx.llm.run() which internally calls run_llm_node
  - This works even for isolated behaviors because restrict_modal_access=False
  - run_llm_node has network access and secrets, so it can call external APIs

Performance:
- Trusted path: ~100-300ms (warm containers with min_containers=1)
- Isolated path: ~300-500ms (fresh container per request)
- LLM node: ~1-10s depending on model (warm containers with min_containers=1)

Usage:
    # Deploy the executor to staging
    modal deploy --env staging app/context/modal_behavior_executor.py

    # Deploy to production
    modal deploy app/context/modal_behavior_executor.py

    # Priority behaviors are called directly from FastAPI via Function.from_name()
    # Async jobs are picked up by poll_behavior_jobs every 10 seconds
    # LLM calls from behaviors automatically route through run_llm_node
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List

import modal

# Modal app setup
# The app name is the same across environments - Modal environments handle separation
# Deploy to staging: modal deploy --env staging app/context/modal_behavior_executor.py
# Deploy to main: modal deploy app/context/modal_behavior_executor.py
app = modal.App("em-context-behavior-executor")

# Base image for the executor (not for behavior sandboxes)
executor_image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "asyncpg", "httpx", "croniter"
)

# Read behavior_sdk source for injection into sandboxes
BEHAVIOR_SDK_SOURCE = """
# Behavior SDK - injected into sandbox
from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class BehaviorEffect:
    effect_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.effect_type, **self.payload}

@dataclass
class BehaviorOutput:
    prompt_block: Optional[str] = None
    effects: List[BehaviorEffect] = field(default_factory=list)
    trace: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_block": self.prompt_block,
            "effects": [e.to_dict() for e in self.effects],
            "trace": self.trace,
        }

class ProfileNamespace:
    def __init__(self, ctx: "BehaviorContext"):
        self._ctx = ctx
    def get(self, key: str = "", default: Any = None) -> Any:
        if not key:
            return self._ctx._profile
        parts = key.split(".")
        current = self._ctx._profile
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current
    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        patch = {}
        curr = patch
        for p in parts[:-1]:
            curr[p] = {}
            curr = curr[p]
        curr[parts[-1]] = value
        self._ctx._effects.append(BehaviorEffect(effect_type="state_patch",
            payload={"target": "profile", "key": key, "value": value, "patch_data": patch}))
    def delete(self, key: str) -> None:
        parts = key.split(".")
        patch = {}
        curr = patch
        for p in parts[:-1]:
            curr[p] = {}
            curr = curr[p]
        curr[parts[-1]] = None
        self._ctx._effects.append(BehaviorEffect(effect_type="state_patch",
            payload={"target": "profile", "key": key, "patch_data": patch, "operation": "delete"}))

class SessionNamespace:
    def __init__(self, ctx: "BehaviorContext"):
        self._ctx = ctx
    def get(self, key: str = "", default: Any = None) -> Any:
        if not self._ctx._session_state:
            return default
        if not key:
            return self._ctx._session_state
        return self._ctx._session_state.get(key, default)
    def set(self, key: str, value: Any) -> None:
        if not self._ctx._session_id:
            return
        self._ctx._effects.append(BehaviorEffect(effect_type="state_patch",
            payload={"target": "session", "session_id": self._ctx._session_id, "key": key, "value": value}))
    def delete(self, key: str) -> None:
        if not self._ctx._session_id:
            return
        self._ctx._effects.append(BehaviorEffect(effect_type="state_patch",
            payload={"target": "session", "session_id": self._ctx._session_id, "key": key, "operation": "delete"}))

class MemoryNamespace:
    def __init__(self, ctx: "BehaviorContext"):
        self._ctx = ctx
    def add(self, content: str, type: Optional[str] = None) -> None:
        self._ctx._effects.append(BehaviorEffect(effect_type="memory_write",
            payload={"operation": "add", "content": content, "type": type}))

class LLMNamespace:
    \"\"\"LLM access for behaviors via dedicated Modal function.

    Available to ALL behaviors (including isolated ones) because it calls a separate
    Modal function that has network access. This design allows isolated behaviors
    to use LLM while still being network-isolated for their own code execution.
    \"\"\"
    def __init__(self, ctx: "BehaviorContext"):
        self._ctx = ctx

    async def run(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = "google/gemini-2.0-flash-001:google-vertex",
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        \"\"\"Call an LLM and return the response text.

        This method calls a dedicated Modal function (run_llm_node) that has
        network access. Since isolated behaviors have restrict_modal_access=False,
        they can call other Modal functions even when their own network is blocked.

        Args:
            prompt: The user prompt to send
            system: Optional system message
            model: Model to use (default: google/gemini-2.0-flash-001 via OpenRouter)
                   Other options: google/gemini-2.5-flash, gpt-4o-mini, claude-3-5-sonnet, etc.
            temperature: Sampling temperature (default: 0.7)
            max_tokens: Maximum tokens in response (default: 1000)

        Returns:
            The LLM response text

        Raises:
            Exception: If LLM API call fails
        \"\"\"
        import modal

        # Call the dedicated LLM node Modal function
        # This works even from isolated behaviors because restrict_modal_access=False
        llm_fn = modal.Function.from_name("em-context-behavior-executor", "run_llm_node")
        result = await llm_fn.remote.aio(
            prompt=prompt,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result

class BehaviorContext:
    def __init__(self, data: Dict[str, Any]):
        self.message: str = data.get("message", "")
        self.companion_id: str = data.get("companion_id", "")
        self.conversation_id: Optional[str] = data.get("conversation_id")
        self.relationship_id: Optional[str] = data.get("relationship_id")
        self.external_user_id: Optional[str] = data.get("external_user_id")
        self.turn_count: int = data.get("turn_count", 0)
        self.trigger_source: Optional[str] = data.get("trigger_source")
        self.trigger_details: Optional[str] = data.get("trigger_details")
        self.behavior_params: Dict[str, Any] = data.get("behavior_params", {})
        self.extra_context: Optional[Dict[str, Any]] = data.get("extra_context")
        self._trace: Dict[str, Any] = {}
        state = data.get("state", {})
        self._profile: Dict[str, Any] = state.get("profile", {})
        self._session_id: Optional[str] = data.get("session_id")
        self._session_state: Dict[str, Any] = state.get("session", {})
        # Recent messages for context (useful for idle/api triggered behaviors)
        self.messages: List[Dict[str, Any]] = state.get("messages", [])
        self._effects: List[BehaviorEffect] = []
        # Initialize namespaces
        # Note: LLM is available to ALL behaviors (including isolated ones) via Modal function
        self.profile = ProfileNamespace(self)
        self.session = SessionNamespace(self)
        self.memory = MemoryNamespace(self)
        self.llm = LLMNamespace(self)

    @property
    def last_user_message(self) -> Optional[str]:
        \"\"\"Get the last user message from conversation history.\"\"\"
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                return msg.get("content")
        return self.message if self.message else None

    @property
    def conversation_text(self) -> str:
        \"\"\"Get the recent conversation as formatted text.\"\"\"
        lines = []
        for msg in self.messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return chr(10).join(lines)

    # --- Webhooks ---
    def notify_webhook(self, event_type: str, data: Dict[str, Any]) -> None:
        self._effects.append(BehaviorEffect(effect_type="webhook",
            payload={"event_type": event_type, "data": data}))

    # --- Proactive Messaging (Phase 7) ---
    def send_message(self, content: str, expires_in_hours: int = 24) -> None:
        \"\"\"Send a proactive message to the user.\"\"\"
        self._effects.append(BehaviorEffect(effect_type="proactive_message",
            payload={"content": content, "expires_in_hours": expires_in_hours}))

    # --- Trace (for debugging) ---
    @property
    def trace(self) -> Dict[str, Any]:
        return self._trace

    # --- Internal ---
    def _get_effects(self) -> List[BehaviorEffect]:
        return self._effects
    def _build_output(self, prompt_block: Optional[str] = None) -> BehaviorOutput:
        return BehaviorOutput(prompt_block=prompt_block, effects=self._effects, trace=self._trace)
"""


# =============================================================================
# FAST BEHAVIOR EXECUTION FUNCTIONS
# =============================================================================
# These functions are called directly from FastAPI for priority behavior execution.
# They replace the slow Sandbox approach with Modal Functions.

# Image with common dependencies for behavior execution
behavior_executor_image = (
    modal.Image.debian_slim(python_version="3.11").pip_install(
        "pydantic", "httpx"
    )  # Common deps available to all behaviors
)


def _execute_behavior_code(source_code: str, context_json: str) -> str:
    """Execute behavior code and return JSON result.

    This is the core execution logic shared by both trusted and isolated paths.
    Uses exec() to run developer code with the Behavior SDK injected.

    LLM access is now available to ALL behaviors (including isolated ones) via
    the dedicated run_llm_node Modal function. This works because isolated behaviors
    have restrict_modal_access=False, allowing them to call other Modal functions.

    Args:
        source_code: The behavior code to execute
        context_json: JSON string with context data
    """
    import asyncio
    import json

    context_data = json.loads(context_json)

    # Create execution namespace with SDK classes
    exec_globals: dict = {}
    exec(BEHAVIOR_SDK_SOURCE, exec_globals)

    # Execute developer's behavior code (defines async execute(ctx) function)
    exec(source_code, exec_globals)

    # Get the execute function (support both 'execute' and 'run' names)
    execute_fn = exec_globals.get("execute") or exec_globals.get("run")
    if not execute_fn:
        raise ValueError(
            "Behavior code must define an 'async def execute(ctx)' or 'async def run(ctx)' function"
        )

    # Run the behavior
    async def _run():
        ctx = exec_globals["BehaviorContext"](context_data)
        result = await execute_fn(ctx)

        # Normalize return value
        if result is None:
            output = ctx._build_output()
        elif isinstance(result, str):
            output = ctx._build_output(prompt_block=result)
        elif isinstance(result, exec_globals["BehaviorOutput"]):
            output = result
        else:
            output = ctx._build_output(prompt_block=str(result))

        return output.to_dict()

    result = asyncio.run(_run())
    return json.dumps(result)


@app.function(
    image=behavior_executor_image,
    min_containers=1,  # Keep 1 container warm at all times
    max_containers=10,  # Scale up under load
    scaledown_window=300,  # Keep extra containers warm for 5 min
    enable_memory_snapshot=True,  # Faster cold starts if container dies
    timeout=60,
    secrets=[modal.Secret.from_name("em-service-secrets")],
)
def execute_behavior_trusted(source_code: str, context_json: str) -> str:
    """Fast path for trusted behaviors.

    - Containers are reused across requests (warm, fast!)
    - Has access to secrets (can be extended for DB access if needed)
    - Has LLM access via ctx.llm.run() (calls run_llm_node Modal function)
    - Use for: internal behaviors, verified developer behaviors

    Args:
        source_code: Python code defining 'async def execute(ctx)' function
        context_json: JSON string with behavior context data

    Returns:
        JSON string with behavior result (prompt_block, effects, trace)
    """
    return _execute_behavior_code(source_code, context_json)


@app.function(
    image=behavior_executor_image,
    restrict_modal_access=False,  # CAN call other Modal resources (like run_llm_node)
    block_network=True,  # No direct outbound network requests
    max_inputs=1,  # Fresh container per request (no cross-request leakage)
    timeout=30,  # Shorter timeout for untrusted code
    # NO secrets - isolated code cannot access our infrastructure directly
)
def execute_behavior_isolated(source_code: str, context_json: str) -> str:
    """Isolated path for untrusted behaviors.

    - Fresh container for each request (no state leakage between requests)
    - No direct network access (block_network=True)
    - CAN call other Modal functions (restrict_modal_access=False)
    - LLM access IS available via ctx.llm.run() (calls run_llm_node Modal function)
    - Use for: user-provided code, unverified behaviors

    Args:
        source_code: Python code defining 'async def execute(ctx)' function
        context_json: JSON string with behavior context data

    Returns:
        JSON string with behavior result (prompt_block, effects, trace)
    """
    return _execute_behavior_code(source_code, context_json)


# =============================================================================
# ASYNC JOB EXECUTION (Background job processing)
# =============================================================================
# The BehaviorExecutor class below is used for async (non-priority) behaviors
# that are processed in the background via poll_behavior_jobs().
# It uses the same Modal Functions as priority behaviors for fast execution.


@app.cls(
    image=executor_image,
    timeout=600,  # 10 min max per execution cycle
    scaledown_window=60 * 5,
    secrets=[modal.Secret.from_name("em-service-secrets")],
    min_containers=0,
    max_containers=2,
)
class BehaviorExecutor:
    """Executes async behaviors via Modal Functions."""

    @modal.enter()
    async def _setup(self):
        # Initialize instance attribute - pool is created lazily in _ensure_pool()
        self._pool = None

    async def _setup_jsonb_codec(self, conn) -> None:
        """Register JSONB codec to automatically encode/decode Python dicts."""
        import json

        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    async def _ensure_pool(self):
        if self._pool is None:
            import asyncpg

            dsn = os.getenv("DATABASE_DSN")
            assert dsn, "DATABASE_DSN missing"
            self._pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=4,
                statement_cache_size=0,
                init=self._setup_jsonb_codec,
            )

    @modal.method()
    async def execute_job(self, job_id: str) -> Dict[str, Any]:
        """Execute a single behavior job via Modal Functions."""
        await self._ensure_pool()

        # 1. Fetch job details
        job = await self._get_job(job_id)
        if not job:
            return {"status": "not_found", "job_id": job_id}

        try:
            # 2. Load behavior definition
            behavior = await self._get_behavior(
                job["params"].get("behavior_key"),
                job["companion_id"],
            )
            if not behavior:
                raise ValueError(f"Behavior not found: {job['params'].get('behavior_key')}")

            # 3. Build context to pass into function
            context_data = await self._build_context_data(job, behavior)

            # 4. Execute via Modal Function (fast path)
            # LLM is available to ALL behaviors via run_llm_node Modal function
            is_isolated = behavior.get("isolated", False)
            result = await self._execute_via_function(
                source_code=behavior["source_code"],
                context_data=context_data,
                isolated=is_isolated,
            )

            # 5. Process effects
            effects = result.get("effects", [])
            await self._process_effects(effects, job)

            # 6. Call webhook if configured
            if behavior.get("webhook_url"):
                await self._call_webhook(behavior, job, result)

            # 7. Complete job
            await self._complete_job(job_id, result)

            return {"status": "completed", "job_id": job_id, "result": result}

        except Exception as e:
            await self._fail_job(job_id, str(e))
            return {"status": "failed", "job_id": job_id, "error": str(e)}

    async def _get_job(self, job_id: str) -> Dict[str, Any] | None:
        """Fetch job from database."""
        from uuid import UUID

        # Convert string job_id to UUID for query
        job_uuid = UUID(job_id) if isinstance(job_id, str) else job_id

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, job_type, companion_id, conversation_id,
                       external_user_id, params, status
                FROM jobs WHERE id = $1
                """,
                job_uuid,
            )
            if not row:
                return None
            # Note: params is already decoded by JSONB codec
            params = row["params"] if row["params"] else {}
            if isinstance(params, str):
                params = json.loads(params)
            return {
                "id": str(row["id"]),
                "job_type": row["job_type"],
                "companion_id": str(row["companion_id"]) if row["companion_id"] else None,
                "conversation_id": str(row["conversation_id"]) if row["conversation_id"] else None,
                "external_user_id": row["external_user_id"],
                "params": params,
                "status": row["status"],
            }

    async def _get_behavior(self, behavior_key: str, companion_id: str) -> Dict[str, Any] | None:
        """Fetch behavior definition from database."""
        from uuid import UUID

        # Convert string companion_id to UUID for query
        companion_uuid = UUID(companion_id) if isinstance(companion_id, str) else companion_id

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT b.id, b.key, b.name, b.source_code, b.dependencies,
                       b.block_network, b.timeout_seconds,
                       cbl.webhook_url, cbl.params AS link_params, cbl.isolated
                FROM behaviors b
                JOIN companion_behavior_links cbl ON cbl.behavior_id = b.id
                WHERE b.key = $1 AND cbl.companion_id = $2 AND cbl.enabled = TRUE
                """,
                behavior_key,
                companion_uuid,
            )
            if not row:
                return None
            # Note: JSONB fields are already decoded by codec
            dependencies = row["dependencies"] if row["dependencies"] else []
            if isinstance(dependencies, str):
                dependencies = json.loads(dependencies)
            link_params = row["link_params"] if row["link_params"] else {}
            if isinstance(link_params, str):
                link_params = json.loads(link_params)
            return {
                "id": str(row["id"]),
                "key": row["key"],
                "name": row["name"],
                "source_code": row["source_code"],
                "dependencies": dependencies,
                "block_network": row["block_network"],
                "timeout_seconds": row["timeout_seconds"],
                "webhook_url": row["webhook_url"],
                "params": link_params,
                "isolated": row["isolated"],
            }

    async def _build_context_data(
        self, job: Dict[str, Any], behavior: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build context data to pass into sandbox."""
        # Fetch fresh state from DB (including messages for idle/api triggers)
        relationship_id = job["params"].get("relationship_id")
        state = await self._get_all_state(
            job["companion_id"],
            job.get("external_user_id"),
            job.get("conversation_id"),
            relationship_id=relationship_id,
        )

        return {
            "message": job["params"].get("user_message", ""),
            "companion_id": job["companion_id"],
            "conversation_id": job.get("conversation_id"),
            "external_user_id": job.get("external_user_id"),
            "relationship_id": relationship_id,
            "turn_count": job["params"].get("turn_count", 0),
            "trigger_source": job["params"].get("trigger_source"),
            "trigger_details": job["params"].get("trigger_details"),
            "extra_context": job["params"].get("extra_context"),
            "behavior_params": behavior.get("params", {}),
            "state": state,
        }

    def _safe_json(self, value: Any, default: Any = None) -> Any:
        """Safely handle JSONB values that may already be decoded by codec."""
        if value is None:
            return default if default is not None else {}
        if isinstance(value, str):
            return json.loads(value)
        return value  # Already decoded by JSONB codec

    async def _get_all_state(
        self,
        companion_id: str,
        external_user_id: str | None,
        conversation_id: str | None,
        relationship_id: str | None = None,
    ) -> Dict[str, Any]:
        """Fetch all state for context including recent messages."""
        from uuid import UUID

        result = {
            "profile": {},
            "session": {},
            "messages": [],
        }

        # Convert string UUIDs back to UUID objects for database queries
        companion_uuid = UUID(companion_id) if isinstance(companion_id, str) else companion_id

        async with self._pool.acquire() as conn:
            # Query relationships table for profile
            if external_user_id:
                row = await conn.fetchrow(
                    """
                    SELECT id, profile
                    FROM relationships
                    WHERE companion_id = $1 AND external_user_id = $2
                    """,
                    companion_uuid,
                    external_user_id,
                )
                if row:
                    result["profile"] = self._safe_json(row["profile"])
                    # Use found relationship_id if not provided
                    if not relationship_id:
                        relationship_id = str(row["id"])

            # Fetch last N messages for context (useful for idle/api triggers)
            if relationship_id:
                try:
                    from uuid import UUID

                    rel_uuid = (
                        UUID(relationship_id)
                        if isinstance(relationship_id, str)
                        else relationship_id
                    )
                    message_rows = await conn.fetch(
                        """
                        SELECT role, content, created_at
                        FROM messages
                        WHERE relationship_id = $1
                        ORDER BY created_at DESC
                        LIMIT 10
                        """,
                        rel_uuid,
                    )
                    # Reverse to get chronological order
                    result["messages"] = [
                        {
                            "role": row["role"],
                            "content": row["content"],
                            "created_at": row["created_at"].isoformat()
                            if row["created_at"]
                            else None,
                        }
                        for row in reversed(message_rows)
                    ]
                except Exception as e:
                    print(f"Error fetching messages for context: {e}")

        return result

    async def _execute_via_function(
        self,
        source_code: str,
        context_data: Dict[str, Any],
        isolated: bool = False,
    ) -> Dict[str, Any]:
        """Execute behavior code via Modal Functions.

        Uses the same functions as priority behaviors:
        - execute_behavior_trusted: Warm containers, fast (~100-300ms)
        - execute_behavior_isolated: Fresh container, secure (~300-500ms)

        LLM access is available to ALL behaviors via the run_llm_node Modal function.
        """
        context_json = json.dumps(context_data)

        # Choose execution path based on isolation setting
        if isolated:
            fn = modal.Function.from_name(
                "em-context-behavior-executor", "execute_behavior_isolated"
            )
        else:
            fn = modal.Function.from_name(
                "em-context-behavior-executor", "execute_behavior_trusted"
            )

        # Call the Modal function
        result_json = await fn.remote.aio(source_code, context_json)
        # Handle case where Modal might auto-deserialize the JSON string
        if isinstance(result_json, dict):
            return result_json
        return json.loads(result_json)

    async def _process_effects(self, effects: List[Dict[str, Any]], job: Dict[str, Any]) -> None:
        """Process effects returned by the behavior."""
        from uuid import UUID

        # Convert string UUIDs back to UUID objects for database queries
        companion_uuid = (
            UUID(job["companion_id"])
            if isinstance(job["companion_id"], str)
            else job["companion_id"]
        )
        external_user_id = job.get("external_user_id")

        async with self._pool.acquire() as conn:
            for effect in effects:
                effect_type = effect.get("type")

                if effect_type == "state_patch":
                    target = effect.get("target")
                    operation = effect.get("operation", "set")

                    if target == "profile":
                        key = effect.get("key", "")
                        value = effect.get("value")

                        if operation == "delete":
                            # For delete, remove the key from profile
                            path_parts = key.split(".")
                            if len(path_parts) == 1:
                                # Top-level key: use - operator
                                await conn.execute(
                                    """
                                    UPDATE relationships
                                    SET profile = profile - $3, updated_at = NOW(), version = version + 1
                                    WHERE companion_id = $1 AND external_user_id = $2
                                    """,
                                    companion_uuid,
                                    job.get("external_user_id"),
                                    key,
                                )
                            else:
                                # Nested key: use #- operator with path
                                await conn.execute(
                                    """
                                    UPDATE relationships
                                    SET profile = profile #- $3, updated_at = NOW(), version = version + 1
                                    WHERE companion_id = $1 AND external_user_id = $2
                                    """,
                                    companion_uuid,
                                    job.get("external_user_id"),
                                    path_parts,
                                )
                        else:
                            # For nested keys, we need to ensure intermediate objects exist
                            # Use jsonb_set iteratively through each path level
                            path_parts = key.split(".")

                            if len(path_parts) == 1:
                                # Simple top-level key: use direct jsonb_set
                                # Pass value directly - JSONB codec handles encoding
                                await conn.execute(
                                    """
                                    UPDATE relationships
                                    SET profile = jsonb_set(
                                        COALESCE(profile, '{}'::jsonb),
                                        $3::text[],
                                        $4,
                                        true
                                    ),
                                    updated_at = NOW(), version = version + 1
                                    WHERE companion_id = $1 AND external_user_id = $2
                                    """,
                                    companion_uuid,
                                    external_user_id,
                                    path_parts,
                                    value,  # Don't json.dumps - codec handles it
                                )
                            else:
                                # For nested paths, first ensure parent objects exist, then set value
                                # Example: for "test.sub.key", ensure "test" and "test.sub" exist
                                for i in range(1, len(path_parts)):
                                    parent_path = path_parts[:i]
                                    await conn.execute(
                                        """
                                        UPDATE relationships
                                        SET profile = jsonb_set(
                                            COALESCE(profile, '{}'::jsonb),
                                            $3::text[],
                                            COALESCE(profile #> $3::text[], '{}'::jsonb),
                                            true
                                        )
                                        WHERE companion_id = $1 AND external_user_id = $2
                                          AND (profile #> $3::text[]) IS NULL
                                        """,
                                        companion_uuid,
                                        external_user_id,
                                        parent_path,
                                    )
                                # Now set the actual value
                                # Pass value directly - JSONB codec handles encoding
                                await conn.execute(
                                    """
                                    UPDATE relationships
                                    SET profile = jsonb_set(
                                        COALESCE(profile, '{}'::jsonb),
                                        $3::text[],
                                        $4,
                                        true
                                    ),
                                    updated_at = NOW(), version = version + 1
                                    WHERE companion_id = $1 AND external_user_id = $2
                                    """,
                                    companion_uuid,
                                    external_user_id,
                                    path_parts,
                                    value,  # Don't json.dumps - codec handles it
                                )

                    elif target == "session":
                        session_id = effect.get("session_id")
                        key = effect.get("key")
                        value = effect.get("value")
                        if session_id:
                            if operation == "delete":
                                await conn.execute(
                                    """
                                    UPDATE v2_sessions
                                    SET state = state - $2
                                    WHERE id = $1 AND isolated = FALSE
                                    """,
                                    session_id,
                                    key,
                                )
                            else:
                                patch_data = {key: value}
                                await conn.execute(
                                    """
                                    UPDATE v2_sessions
                                    SET state = COALESCE(state, '{}'::jsonb) || $2::jsonb
                                    WHERE id = $1 AND isolated = FALSE
                                    """,
                                    session_id,
                                    patch_data,
                                )

                elif effect_type == "memory_write":
                    await conn.execute(
                        """
                        INSERT INTO memories (
                            companion_id, content, importance,
                            external_user_id, conversation_id,
                            modality, sender_type
                        ) VALUES ($1, $2, $3, $4, $5, 'text', 'behavior')
                        """,
                        companion_uuid,
                        effect.get("content"),
                        float(effect.get("importance", 0.5)),
                        job.get("external_user_id"),
                        job.get("conversation_id"),
                    )

                elif effect_type == "proactive_message":
                    # Phase 7: Create proactive message for user inbox
                    content = effect.get("content")
                    if content:
                        from datetime import timedelta
                        from uuid import uuid4

                        expires_in_hours = effect.get("expires_in_hours", 24)
                        expires_at = datetime.utcnow() + timedelta(hours=expires_in_hours)
                        behavior_key = job["params"].get("behavior_key")

                        # Get relationship_id from job params or lookup
                        relationship_id = job["params"].get("relationship_id")
                        if not relationship_id and job.get("external_user_id"):
                            rel_row = await conn.fetchrow(
                                """
                                SELECT id FROM relationships
                                WHERE companion_id = $1 AND external_user_id = $2
                                """,
                                companion_uuid,
                                job["external_user_id"],
                            )
                            if rel_row:
                                relationship_id = rel_row["id"]

                        if relationship_id:
                            # Convert relationship_id to UUID if it's a string
                            rel_uuid = (
                                UUID(relationship_id)
                                if isinstance(relationship_id, str)
                                else relationship_id
                            )

                            # Get next sequence number
                            seq_row = await conn.fetchrow(
                                "SELECT next_relationship_message_seq($1) as seq",
                                rel_uuid,
                            )
                            seq = seq_row["seq"] if seq_row else None

                            # Insert proactive message
                            message_id = uuid4()
                            await conn.execute(
                                """
                                INSERT INTO messages (
                                    id, relationship_id, role, content, seq,
                                    is_proactive, delivery_status, expires_at, source_behavior_key
                                )
                                VALUES ($1, $2, 'assistant', $3, $4, TRUE, 'pending', $5, $6)
                                """,
                                message_id,
                                rel_uuid,
                                content,
                                seq,
                                expires_at,
                                behavior_key,
                            )
                            print(
                                f"Created proactive message: {message_id} for relationship {rel_uuid}"
                            )

    async def _call_webhook(
        self,
        behavior: Dict[str, Any],
        job: Dict[str, Any],
        result: Dict[str, Any],
    ) -> None:
        """Call developer's webhook with behavior result and fresh state."""
        import httpx

        # Fetch fresh state AFTER effects were applied
        fresh_state = await self._get_all_state(
            job["companion_id"],
            job.get("external_user_id"),
            job.get("conversation_id"),
        )

        payload = {
            "event": "behavior_completed",
            "behavior_key": behavior["key"],
            "job_id": job["id"],
            "status": "success",
            "result": result.get("trace", {}),
            "prompt_block": result.get("prompt_block"),
            "state": fresh_state,
            "context": {
                "companion_id": job["companion_id"],
                "conversation_id": job.get("conversation_id"),
                "external_user_id": job.get("external_user_id"),
                "turn_count": job["params"].get("turn_count", 0),
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    behavior["webhook_url"],
                    json=payload,
                    timeout=10,
                    headers={"Content-Type": "application/json"},
                )
        except Exception as e:
            # Log but don't fail the job
            print(f"Webhook call failed: {e}")

    async def _complete_job(self, job_id: str, result: Dict[str, Any]) -> None:
        """Mark job as completed."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs
                SET status = 'completed', completed_at = NOW(),
                    updated_at = NOW(), result = $2
                WHERE id = $1
                """,
                job_id,
                result,  # Don't json.dumps - JSONB codec handles encoding
            )

    async def _fail_job(self, job_id: str, error: str) -> None:
        """Mark job as failed.

        TODO: Enable retry logic when ready. The jobs table has:
        - attempts: current attempt count
        - max_attempts: default 3
        """
        async with self._pool.acquire() as conn:
            # --- RETRY LOGIC (commented out for now) ---
            # Uncomment to enable automatic retries with exponential backoff
            #
            # row = await conn.fetchrow(
            #     "SELECT attempts, max_attempts FROM jobs WHERE id = $1",
            #     job_id,
            # )
            #
            # attempts = (row["attempts"] or 0) + 1
            # max_attempts = row["max_attempts"] or 3
            #
            # if attempts < max_attempts:
            #     # Retry with exponential backoff: 30s, 60s, 120s...
            #     backoff_seconds = 30 * (2 ** (attempts - 1))
            #     await conn.execute(
            #         """
            #         UPDATE jobs
            #         SET status = 'pending',
            #             attempts = $2,
            #             error = $3,
            #             run_at = NOW() + INTERVAL '1 second' * $4,
            #             updated_at = NOW()
            #         WHERE id = $1
            #         """,
            #         job_id, attempts, error, backoff_seconds,
            #     )
            #     print(f"Job {job_id} scheduled for retry {attempts}/{max_attempts} in {backoff_seconds}s")
            #     return
            #
            # # Max retries exhausted - mark as failed
            # --- END RETRY LOGIC ---

            await conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', completed_at = NOW(),
                    updated_at = NOW(), error = $2
                WHERE id = $1
                """,
                job_id,
                error,
            )


# Polling function - runs every 10 seconds
@app.function(
    image=executor_image,
    schedule=modal.Period(seconds=10),
    timeout=300,
    secrets=[modal.Secret.from_name("em-service-secrets")],
)
async def poll_behavior_jobs():
    """Poll jobs table for pending behavior jobs and evaluate cron triggers."""
    import json

    import asyncpg

    async def _setup_jsonb_codec(conn) -> None:
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    dsn = os.getenv("DATABASE_DSN")
    if not dsn:
        return {"error": "DATABASE_DSN not set"}

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, statement_cache_size=0, init=_setup_jsonb_codec
    )
    cron_jobs_created = 0

    try:
        async with pool.acquire() as conn:
            # Phase 7: Evaluate cron triggers every minute (skip if less than 55s since last check)
            # Only run cron evaluation roughly every minute to avoid duplicate jobs
            cron_check = await conn.fetchval(
                """
                SELECT 1 FROM jobs
                WHERE job_type = 'cron_check'
                  AND created_at > NOW() - INTERVAL '55 seconds'
                LIMIT 1
                """
            )

            if not cron_check:
                # Create marker job for this cron check
                await conn.execute(
                    """
                    INSERT INTO jobs (id, job_type, status, created_at)
                    VALUES (gen_random_uuid(), 'cron_check', 'completed', NOW())
                    """
                )
                cron_jobs_created = await _evaluate_cron_triggers(conn)

                # Also evaluate idle triggers (runs on same schedule as cron)
                idle_jobs_created = await _evaluate_idle_triggers(conn)
                cron_jobs_created += idle_jobs_created

            # Claim up to 5 jobs atomically
            rows = await conn.fetch(
                """
                UPDATE jobs
                SET status = 'running',
                    worker_id = $1,
                    started_at = NOW(),
                    updated_at = NOW()
                WHERE id IN (
                    SELECT id FROM jobs
                    WHERE job_type = 'behavior_execution'
                      AND status = 'pending'
                      AND (run_at IS NULL OR run_at <= NOW())
                    ORDER BY priority DESC, created_at
                    LIMIT 5
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id
                """,
                f"modal-{modal.current_function_call_id() or 'local'}",
            )

            if not rows and cron_jobs_created == 0:
                return {"processed": 0, "cron_jobs_created": 0}

            # Execute jobs
            executor = BehaviorExecutor()
            results = []
            for row in rows:
                result = await executor.execute_job.remote.aio(str(row["id"]))
                results.append(result)

            return {
                "processed": len(results),
                "results": results,
                "cron_jobs_created": cron_jobs_created,
            }

    finally:
        await pool.close()


async def _evaluate_cron_triggers(conn) -> int:
    """Evaluate cron triggers and create jobs for due behaviors.

    Handles two types of cron schedules:
    1. Companion-level (relationship_id IS NULL): runs for ALL relationships
    2. Relationship-level (relationship_id IS NOT NULL): runs for that specific relationship

    Returns the number of jobs created.
    """
    try:
        from croniter import croniter
    except ImportError:
        print("croniter not installed, skipping cron evaluation")
        return 0

    now = datetime.utcnow()
    jobs_created = 0

    # -------------------------------------------------------------------------
    # 1. Companion-level cron triggers (run for all relationships)
    # -------------------------------------------------------------------------
    companion_rows = await conn.fetch(
        """
        SELECT DISTINCT
            b.id as behavior_id,
            b.key as behavior_key,
            b.source_code,
            cbl.companion_id,
            cbl.triggers,
            cbl.isolated,
            cbl.params
        FROM behaviors b
        JOIN companion_behavior_links cbl ON cbl.behavior_id = b.id
        WHERE cbl.enabled = TRUE
          AND cbl.relationship_id IS NULL
          AND b.source_code IS NOT NULL
        """
    )

    for row in companion_rows:
        triggers = row["triggers"] if row["triggers"] else []

        for trigger in triggers:
            if isinstance(trigger, dict) and trigger.get("type") == "cron":
                expression = trigger.get("expression")
                if not expression:
                    continue

                try:
                    # Check if cron should run now
                    cron = croniter(expression, now)
                    prev_time = cron.get_prev(datetime)
                    time_diff = (now - prev_time).total_seconds()

                    # Within 60-second window of scheduled time
                    if time_diff < 60:
                        # Check we haven't already created a job for this trigger recently
                        existing = await conn.fetchval(
                            """
                            SELECT 1 FROM jobs
                            WHERE job_type = 'behavior_execution'
                              AND companion_id = $1
                              AND params->>'behavior_key' = $2
                              AND params->>'trigger_source' = 'cron'
                              AND params->>'relationship_id' IS NULL
                              AND created_at > NOW() - INTERVAL '55 seconds'
                            LIMIT 1
                            """,
                            row["companion_id"],
                            row["behavior_key"],
                        )

                        if not existing:
                            # Get all active relationships for this companion
                            relationships = await conn.fetch(
                                """
                                SELECT id, external_user_id
                                FROM relationships
                                WHERE companion_id = $1
                                ORDER BY last_interaction_at DESC NULLS LAST
                                LIMIT 50
                                """,
                                row["companion_id"],
                            )

                            # Create a job for each relationship
                            for rel in relationships:
                                from uuid import uuid4

                                job_id = uuid4()
                                await conn.execute(
                                    """
                                    INSERT INTO jobs (
                                        id, job_type, companion_id, external_user_id,
                                        params, status, created_at
                                    ) VALUES ($1, 'behavior_execution', $2, $3, $4, 'pending', NOW())
                                    """,
                                    job_id,
                                    row["companion_id"],
                                    rel["external_user_id"],
                                    {
                                        "behavior_key": row["behavior_key"],
                                        "relationship_id": str(rel["id"]),
                                        "trigger_source": "cron",
                                        "trigger_details": f"cron:{expression}",
                                        "turn_count": 0,
                                    },
                                )
                                jobs_created += 1

                            if relationships:
                                print(
                                    f"Created {len(relationships)} cron jobs for "
                                    f"behavior {row['behavior_key']}"
                                )

                except Exception as e:
                    print(f"Error evaluating cron trigger '{expression}': {e}")

    # -------------------------------------------------------------------------
    # 2. Relationship-level cron triggers (run for specific relationship only)
    # -------------------------------------------------------------------------
    relationship_rows = await conn.fetch(
        """
        SELECT DISTINCT
            b.id as behavior_id,
            b.key as behavior_key,
            b.source_code,
            cbl.companion_id,
            cbl.relationship_id,
            cbl.triggers,
            cbl.isolated,
            cbl.params,
            r.external_user_id
        FROM behaviors b
        JOIN companion_behavior_links cbl ON cbl.behavior_id = b.id
        JOIN relationships r ON r.id = cbl.relationship_id
        WHERE cbl.enabled = TRUE
          AND cbl.relationship_id IS NOT NULL
          AND b.source_code IS NOT NULL
        """
    )

    for row in relationship_rows:
        triggers = row["triggers"] if row["triggers"] else []

        for trigger in triggers:
            if isinstance(trigger, dict) and trigger.get("type") == "cron":
                expression = trigger.get("expression")
                if not expression:
                    continue

                try:
                    from croniter import croniter

                    # Check if cron should run now
                    cron = croniter(expression, now)
                    prev_time = cron.get_prev(datetime)
                    time_diff = (now - prev_time).total_seconds()

                    # Within 60-second window of scheduled time
                    if time_diff < 60:
                        # Check we haven't already created a job for this specific relationship
                        existing = await conn.fetchval(
                            """
                            SELECT 1 FROM jobs
                            WHERE job_type = 'behavior_execution'
                              AND companion_id = $1
                              AND params->>'behavior_key' = $2
                              AND params->>'relationship_id' = $3
                              AND params->>'trigger_source' = 'cron'
                              AND created_at > NOW() - INTERVAL '55 seconds'
                            LIMIT 1
                            """,
                            row["companion_id"],
                            row["behavior_key"],
                            str(row["relationship_id"]),
                        )

                        if not existing:
                            from uuid import uuid4

                            job_id = uuid4()
                            await conn.execute(
                                """
                                INSERT INTO jobs (
                                    id, job_type, companion_id, external_user_id,
                                    params, status, created_at
                                ) VALUES ($1, 'behavior_execution', $2, $3, $4, 'pending', NOW())
                                """,
                                job_id,
                                row["companion_id"],
                                row["external_user_id"],
                                {
                                    "behavior_key": row["behavior_key"],
                                    "relationship_id": str(row["relationship_id"]),
                                    "trigger_source": "cron",
                                    "trigger_details": f"cron:{expression}",
                                    "turn_count": 0,
                                },
                            )
                            jobs_created += 1
                            print(
                                f"Created relationship-specific cron job for "
                                f"behavior {row['behavior_key']} / relationship {row['relationship_id']}"
                            )

                except Exception as e:
                    print(f"Error evaluating relationship cron trigger '{expression}': {e}")

    return jobs_created


async def _evaluate_idle_triggers(conn) -> int:
    """Evaluate idle triggers and create jobs for relationships that have been idle.

    Idle triggers fire when a relationship has been inactive for X minutes.
    This allows behaviors to run during "in-between" moments (e.g., post-conversation synthesis).

    Trigger format: {"type": "idle", "minutes": 30}
    Or string format: "idle:30"

    Returns the number of jobs created.
    """
    from uuid import uuid4

    jobs_created = 0

    # Find all behaviors with idle triggers
    behavior_rows = await conn.fetch(
        """
        SELECT DISTINCT
            b.id as behavior_id,
            b.key as behavior_key,
            b.source_code,
            cbl.id as link_id,
            cbl.companion_id,
            cbl.relationship_id,
            cbl.triggers,
            cbl.isolated,
            cbl.params
        FROM behaviors b
        JOIN companion_behavior_links cbl ON cbl.behavior_id = b.id
        WHERE cbl.enabled = TRUE
          AND b.source_code IS NOT NULL
          AND (
              cbl.triggers::text LIKE '%idle:%'
              OR cbl.triggers::text LIKE '%"type":"idle"%'
              OR cbl.triggers::text LIKE '%"type": "idle"%'
          )
        """
    )

    for row in behavior_rows:
        triggers = row["triggers"] if row["triggers"] else []

        # Parse idle trigger to get minutes threshold
        idle_minutes = None
        for trigger in triggers:
            if isinstance(trigger, dict) and trigger.get("type") == "idle":
                idle_minutes = trigger.get("minutes")
                break
            elif isinstance(trigger, str) and trigger.startswith("idle:"):
                try:
                    idle_minutes = int(trigger.split(":")[1])
                except (ValueError, IndexError):
                    continue
                break

        if not idle_minutes:
            continue

        try:
            # Determine which relationships to check
            if row["relationship_id"]:
                # Relationship-specific behavior link
                relationships = await conn.fetch(
                    f"""
                    SELECT
                        r.id as relationship_id,
                        r.companion_id,
                        r.external_user_id,
                        MAX(m.created_at) as last_message_at
                    FROM relationships r
                    LEFT JOIN messages m ON m.relationship_id = r.id
                    WHERE r.id = $1
                    GROUP BY r.id
                    HAVING MAX(m.created_at) IS NOT NULL
                       AND MAX(m.created_at) < NOW() - INTERVAL '{idle_minutes} minutes'
                    """,
                    row["relationship_id"],
                )
            else:
                # Companion-level behavior link - check all relationships for this companion
                relationships = await conn.fetch(
                    f"""
                    SELECT
                        r.id as relationship_id,
                        r.companion_id,
                        r.external_user_id,
                        MAX(m.created_at) as last_message_at
                    FROM relationships r
                    LEFT JOIN messages m ON m.relationship_id = r.id
                    WHERE r.companion_id = $1
                    GROUP BY r.id
                    HAVING MAX(m.created_at) IS NOT NULL
                       AND MAX(m.created_at) < NOW() - INTERVAL '{idle_minutes} minutes'
                    LIMIT 100
                    """,
                    row["companion_id"],
                )

            # For each idle relationship, check if we already triggered
            for rel in relationships:
                # Check if we already created an idle job for this relationship+behavior
                # since their last message
                existing = await conn.fetchval(
                    """
                    SELECT 1 FROM jobs
                    WHERE job_type = 'behavior_execution'
                      AND params->>'relationship_id' = $1
                      AND params->>'behavior_key' = $2
                      AND params->>'trigger_source' = 'idle'
                      AND created_at > $3
                    LIMIT 1
                    """,
                    str(rel["relationship_id"]),
                    row["behavior_key"],
                    rel["last_message_at"],
                )

                if not existing:
                    job_id = uuid4()
                    await conn.execute(
                        """
                        INSERT INTO jobs (
                            id, job_type, companion_id, external_user_id,
                            params, status, priority, created_at
                        ) VALUES ($1, 'behavior_execution', $2, $3, $4, 'pending', $5, NOW())
                        """,
                        job_id,
                        rel["companion_id"],
                        rel["external_user_id"],
                        {
                            "behavior_key": row["behavior_key"],
                            "relationship_id": str(rel["relationship_id"]),
                            "trigger_source": "idle",
                            "trigger_details": f"idle:{idle_minutes}",
                            "idle_since": rel["last_message_at"].isoformat()
                            if rel["last_message_at"]
                            else None,
                            "turn_count": 0,
                        },
                        0,  # Default priority for idle triggers
                    )
                    jobs_created += 1

            if jobs_created > 0:
                print(
                    f"Created {jobs_created} idle jobs for behavior {row['behavior_key']} "
                    f"(idle threshold: {idle_minutes} min)"
                )

        except Exception as e:
            print(f"Error evaluating idle trigger for behavior {row['behavior_key']}: {e}")

    return jobs_created


# =============================================================================
# LLM NODE - Dedicated Modal function for LLM calls
# =============================================================================
# This function provides LLM access to ALL behaviors (including isolated ones).
# Even when a behavior has block_network=True, it can call other Modal functions
# via restrict_modal_access=False. This allows isolated behaviors to use LLM
# while still being network-isolated for their own code execution.


@app.function(
    image=behavior_executor_image.pip_install("httpx"),
    timeout=120,  # LLM calls can take time
    secrets=[modal.Secret.from_name("em-service-secrets")],
    min_containers=1,  # Keep warm for fast response
    max_containers=10,
    scaledown_window=300,
)
async def run_llm_node(
    prompt: str,
    system: str = "",
    model: str = "google/gemini-2.0-flash-001:google-vertex",
    temperature: float = 0.7,
    max_tokens: int = 1000,
) -> str:
    """LLM Node - Execute LLM calls with network access.

    This Modal function is designed to be called from both trusted and isolated
    behaviors. Since isolated behaviors have restrict_modal_access=False, they
    can call this function even when their own network is blocked.

    Args:
        prompt: The user prompt to send
        system: Optional system message
        model: Model to use (default: google/gemini-2.0-flash-001 via OpenRouter)
               Other options: google/gemini-2.5-flash, gpt-4o-mini, claude-3-5-sonnet, etc.
        temperature: Sampling temperature (default: 0.7)
        max_tokens: Maximum tokens in response (default: 1000)

    Returns:
        The LLM response text

    Raises:
        RuntimeError: If OPENROUTER_API_KEY is not configured
        Exception: If LLM API call fails
    """
    import httpx

    # Use OpenRouter for multi-model access
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not available in Modal secrets")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


# Manual trigger for testing
@app.function(
    image=executor_image,
    timeout=120,
    secrets=[modal.Secret.from_name("em-service-secrets")],
)
async def execute_behavior_job(job_id: str) -> Dict[str, Any]:
    """Manually execute a specific behavior job (for testing)."""
    executor = BehaviorExecutor()
    return await executor.execute_job.remote.aio(job_id)
