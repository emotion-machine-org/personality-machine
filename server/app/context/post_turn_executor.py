"""PostTurnExecutor: Applies TurnEffects after LLM response generation.

This module handles side effects that layers emit during context building.
Effects are collected during orchestration and applied post-turn to keep
the layer produce() methods pure.

Supported effect types:
- state_patch: Update relationships.profile or v2_sessions.state
- schedule: Enqueue a scheduled behavior for future execution
- memory_write: Write to memory (placeholder)
- job: Enqueue a generic background job (placeholder)
- webhook: Send notification to developer webhook
- proactive_message: Send a proactive message to the user (Phase 7)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List
from uuid import UUID, uuid4

import asyncpg
import httpx

from ..models.memory import MemoryCreate
from ..repositories.job_repository import JobRepository
from ..repositories.relationship_repository import RelationshipRepository
from ..repositories.state_repository import StateRepository
from ..services.memory_service import MemoryService
from .context_hydrator import HydratedContext
from .schemas import ContextPlan, TurnContext, TurnEffect

logger = logging.getLogger(__name__)


class PostTurnExecutor:
    """Executes TurnEffects after the LLM response is generated.

    Effects are applied in order. Errors in one effect don't block others.
    All errors are logged but don't fail the overall response.
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        turn_context: TurnContext,
        hydrated_context: HydratedContext | None = None,
        webhook_configs: Dict[str, Dict[str, Any]] | None = None,
        memory_evaluation_prompt: str | None = None,
    ):
        self.conn = conn
        self.turn_context = turn_context
        self.hydrated = hydrated_context
        self.webhook_configs = webhook_configs or {}
        self.memory_evaluation_prompt = memory_evaluation_prompt or ""
        self._results: List[Dict[str, Any]] = []

    async def execute(self, effects: List[TurnEffect]) -> List[Dict[str, Any]]:
        """Execute all effects and return results.

        Args:
            effects: List of TurnEffect objects to execute

        Returns:
            List of execution results with success/error status
        """
        for effect in effects:
            result = await self._execute_one(effect)
            self._results.append(result)
        return self._results

    async def _execute_one(self, effect: TurnEffect) -> Dict[str, Any]:
        """Execute a single effect and return result."""
        try:
            match effect.effect_type:
                case "state_patch":
                    return await self._apply_state_patch(effect.payload)
                case "schedule":
                    return await self._schedule_action(effect.payload)
                case "memory_write":
                    return await self._write_memory(effect.payload)
                case "memory_v2_write":
                    return await self._write_memory_v2(effect.payload)
                case "job":
                    return await self._enqueue_job(effect.payload)
                case "webhook":
                    return await self._send_webhook(effect.payload)
                case "proactive_message":
                    return await self._send_proactive_message(effect.payload)
                case _:
                    return {"effect_type": effect.effect_type, "status": "unknown_type"}
        except Exception as e:
            logger.error(f"Effect execution failed: {effect.effect_type} - {e}")
            return {
                "effect_type": effect.effect_type,
                "status": "error",
                "error": str(e),
            }

    async def _apply_state_patch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a state patch to relationships or sessions.

        Targets:
        - profile: Update relationships.profile
        - session: Update v2_sessions.state
        - topic_state, metadata: Update conversation_states (legacy)

        Payload format:
        {
            "target": "profile" | "session" | "topic_state" | "metadata",
            "key": "dotted.key.path",
            "value": <any>,
            "operation": "set" | "delete" (default: "set"),
            "patch_data": {nested dict for JSON merge patch} (optional),
            "session_id": "uuid string" (required for session target)
        }
        """
        target = payload.get("target", "profile")
        key = payload.get("key", "")
        value = payload.get("value")
        operation = payload.get("operation", "set")

        # Handle profile target
        if target == "profile":
            if not self.turn_context.relationship_id:
                return {
                    "effect_type": "state_patch",
                    "status": "skipped",
                    "reason": "no relationship_id",
                }

            # Phase 8: Skip profile writes when session is isolated
            if self.turn_context.session_isolated:
                logger.debug("Skipping profile patch in isolated session")
                return {
                    "effect_type": "state_patch",
                    "status": "skipped",
                    "reason": "session is isolated",
                    "target": "profile",
                }

            # Use pre-built patch_data if available, otherwise build from key/value
            if "patch_data" in payload:
                patch_data = payload["patch_data"]
            elif operation == "delete":
                patch_data = self._build_nested_dict(key, None) if key else {}
            else:
                patch_data = self._build_nested_dict(key, value) if key else {}

            if not patch_data:
                return {"effect_type": "state_patch", "status": "error", "error": "empty patch"}

            updated = await RelationshipRepository.patch_profile(
                self.conn,
                self.turn_context.relationship_id,
                patch_data,
            )

            if updated:
                return {
                    "effect_type": "state_patch",
                    "status": "success",
                    "target": "profile",
                    "key": key,
                    "operation": operation,
                    "new_version": updated.version,
                }
            return {
                "effect_type": "state_patch",
                "status": "not_found",
                "reason": "relationship not found",
            }

        # Handle session target (Phase 6)
        elif target == "session":
            session_id_str = payload.get("session_id")
            if not session_id_str:
                return {
                    "effect_type": "state_patch",
                    "status": "skipped",
                    "reason": "no session_id in payload",
                }

            try:
                session_id = UUID(session_id_str)
            except ValueError:
                return {
                    "effect_type": "state_patch",
                    "status": "error",
                    "error": f"invalid session_id: {session_id_str}",
                }

            if operation == "delete":
                result = await RelationshipRepository.delete_session_state_key(
                    self.conn, session_id, key
                )
            else:
                result = await RelationshipRepository.patch_session_state(
                    self.conn, session_id, {key: value}
                )

            if result is not None:
                return {
                    "effect_type": "state_patch",
                    "status": "success",
                    "target": "session",
                    "key": key,
                    "operation": operation,
                }
            return {
                "effect_type": "state_patch",
                "status": "skipped",
                "reason": "session not found or isolated",
            }

        # Legacy targets (topic_state, metadata) - still supported for v1 compatibility
        elif target in ("topic_state", "metadata"):
            if not self.turn_context.conversation_id:
                return {
                    "effect_type": "state_patch",
                    "status": "skipped",
                    "reason": "no conversation_id",
                }

            patches = [{"target": target, "key": key, "value": value, "operation": operation}]
            updated = await StateRepository.patch_conversation_state(
                self.conn,
                self.turn_context.conversation_id,
                patches=patches,
            )
            if updated:
                # Note: StateRepository.patch_conversation_state handles cache invalidation internally
                return {
                    "effect_type": "state_patch",
                    "status": "success",
                    "target": target,
                    "key": key,
                }
            return {"effect_type": "state_patch", "status": "not_found"}

        # Deprecated targets (user_state, companion_state) - log warning and skip
        elif target in ("user_state", "companion_state"):
            logger.warning(
                f"Deprecated state target '{target}' - user_state and companion_state are no longer used in Phase 6"
            )
            return {
                "effect_type": "state_patch",
                "status": "skipped",
                "reason": f"deprecated target: {target}",
            }

        return {
            "effect_type": "state_patch",
            "status": "error",
            "error": f"unknown target: {target}",
        }

    def _build_nested_dict(self, path: str, value: Any) -> Dict[str, Any]:
        """Build a nested dict from a dot notation path and value."""
        if not path:
            return {} if value is None else ({} if not isinstance(value, dict) else value)

        parts = path.split(".")
        result: Dict[str, Any] = {}
        current = result
        for i, part in enumerate(parts[:-1]):
            current[part] = {}
            current = current[part]
        current[parts[-1]] = value
        return result

    async def _schedule_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Schedule a behavior for future execution.

        Payload format:
        {
            "behavior_key": "behavior_identifier",  # Phase 6
            "action_key": "action_identifier",  # Legacy alias
            "run_at": "ISO datetime string",
            "params": {optional params},
            "priority": 50 (optional),
            "cancel": true (optional, to cancel existing scheduled behavior)
        }
        """
        # Support both behavior_key (new) and action_key (legacy)
        behavior_key = payload.get("behavior_key") or payload.get("action_key")
        if not behavior_key:
            return {"effect_type": "schedule", "status": "error", "error": "missing behavior_key"}

        # Check if this is a cancellation
        if payload.get("cancel"):
            # Find and cancel existing scheduled behavior
            existing = await JobRepository.find_active_job(
                self.conn,
                job_type="behavior_execution",
                companion_id=self.turn_context.companion_id,
                behavior_key=behavior_key,
            )
            if not existing:
                # Try legacy action_execution type
                existing = await JobRepository.find_active_job(
                    self.conn,
                    job_type="action_execution",
                    companion_id=self.turn_context.companion_id,
                    action_key=behavior_key,
                )
            if existing:
                cancelled = await JobRepository.cancel_job(self.conn, existing.id)
                return {
                    "effect_type": "schedule",
                    "status": "cancelled" if cancelled else "not_found",
                    "behavior_key": behavior_key,
                }
            return {"effect_type": "schedule", "status": "not_found", "behavior_key": behavior_key}

        # Parse run_at
        run_at_str = payload.get("run_at")
        run_at: datetime | None = None
        if run_at_str:
            try:
                run_at = datetime.fromisoformat(run_at_str.replace("Z", "+00:00"))
            except ValueError as e:
                return {
                    "effect_type": "schedule",
                    "status": "error",
                    "error": f"invalid run_at: {e}",
                }

        # Enqueue the scheduled behavior
        job = await JobRepository.enqueue(
            self.conn,
            job_type="behavior_execution",
            params=payload.get("params", {}),
            companion_id=self.turn_context.companion_id,
            conversation_id=self.turn_context.conversation_id,
            external_user_id=self.turn_context.external_user_id,
            behavior_key=behavior_key,
            run_at=run_at,
            priority=payload.get("priority", 50),
        )

        return {
            "effect_type": "schedule",
            "status": "success",
            "job_id": str(job.id),
            "behavior_key": behavior_key,
            "run_at": run_at.isoformat() if run_at else None,
        }

    async def _write_memory(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Write to memory via the Modal worker pipeline.

        Payload format:
        {
            "content": "memory content",
            "importance": 0.5 (optional hint, will be re-evaluated by LLM),
            "tags": ["optional", "tags"],
            "storage": "long_term" | "working",
            "is_core": false (optional)
        }

        The memory is enqueued to the memory_ingest Modal worker which:
        1. Generates embedding for the content
        2. Evaluates importance using LLM with memory_evaluation_prompt
        3. Persists to memories table if importance threshold met
        """
        # Phase 8: Skip memory writes when session is isolated
        if self.turn_context.session_isolated:
            logger.debug("Skipping memory write in isolated session")
            return {
                "effect_type": "memory_write",
                "status": "skipped",
                "reason": "session is isolated",
            }

        content = payload.get("content")
        if not content:
            return {"effect_type": "memory_write", "status": "error", "error": "missing content"}

        # Determine if this is a core memory (always stored, no importance threshold)
        is_core = payload.get("is_core", False)

        # Storage hint - "long_term" memories could be weighted higher
        storage = payload.get("storage", "working")
        weight_user = 1.0 if storage == "long_term" else 0.8

        try:
            # Build the MemoryCreate payload
            memory_payload = MemoryCreate(
                content=content,
                external_user_id=self.turn_context.external_user_id,
                conversation_id=self.turn_context.conversation_id,
                sender_type="assistant",  # Actions write memories on behalf of the companion
                weight_user=weight_user,
                modality="text",
                is_core=is_core,
            )

            # Enqueue via MemoryService (goes to Modal worker)
            # Uses the companion's memory_evaluation_prompt for importance scoring
            ack_id = await MemoryService.create(
                self.conn,
                companion_id=self.turn_context.companion_id,
                payload=memory_payload,
                importance_guidance=self.memory_evaluation_prompt,
            )

            logger.info(f"Memory write enqueued: {content[:50]}... (ack={ack_id})")

            return {
                "effect_type": "memory_write",
                "status": "enqueued",
                "ack_id": ack_id,
                "content_length": len(content),
                "is_core": is_core,
                "storage": storage,
            }

        except Exception as e:
            logger.error(f"Memory write failed: {e}")
            return {
                "effect_type": "memory_write",
                "status": "error",
                "error": str(e),
            }

    async def _write_memory_v2(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Memory V2 write operations (add/update/delete).

        These are triggered by behaviors using ctx.memory.add/update/delete.

        Payload format:
        {
            "operation": "add" | "update" | "delete",
            "content": "memory content" (for add/update),
            "type": "identity" | "preference" | etc (optional),
            "memory_id": "uuid" (for update/delete)
        }
        """
        # Skip when session is isolated
        if self.turn_context.session_isolated:
            logger.debug("Skipping memory_v2 write in isolated session")
            return {
                "effect_type": "memory_v2_write",
                "status": "skipped",
                "reason": "session is isolated",
            }

        operation = payload.get("operation")
        relationship_id = self.turn_context.relationship_id

        if not relationship_id:
            return {
                "effect_type": "memory_v2_write",
                "status": "error",
                "error": "no relationship_id",
            }

        from ..repositories.memory_v2_repository import MemoryV2Repository

        try:
            if operation == "add":
                content = payload.get("content")
                if not content:
                    return {
                        "effect_type": "memory_v2_write",
                        "status": "error",
                        "error": "missing content for add",
                    }
                entry = await MemoryV2Repository.create_entry(
                    self.conn,
                    relationship_id,
                    content,
                    payload.get("type"),
                )
                logger.info(f"Memory V2 entry created: {content[:50]}...")
                return {
                    "effect_type": "memory_v2_write",
                    "status": "success",
                    "operation": "add",
                    "entry_id": str(entry["id"]),
                }

            elif operation == "update":
                memory_id = payload.get("memory_id")
                content = payload.get("content")
                if not memory_id or not content:
                    return {
                        "effect_type": "memory_v2_write",
                        "status": "error",
                        "error": "missing memory_id or content for update",
                    }
                entry = await MemoryV2Repository.update_entry(
                    self.conn,
                    UUID(memory_id),
                    relationship_id,
                    content,
                    payload.get("type"),
                )
                if entry:
                    logger.info(f"Memory V2 entry updated: {memory_id}")
                    return {
                        "effect_type": "memory_v2_write",
                        "status": "success",
                        "operation": "update",
                        "entry_id": memory_id,
                    }
                return {
                    "effect_type": "memory_v2_write",
                    "status": "not_found",
                    "operation": "update",
                    "entry_id": memory_id,
                }

            elif operation == "delete":
                memory_id = payload.get("memory_id")
                if not memory_id:
                    return {
                        "effect_type": "memory_v2_write",
                        "status": "error",
                        "error": "missing memory_id for delete",
                    }
                deleted = await MemoryV2Repository.delete_entry(
                    self.conn,
                    UUID(memory_id),
                    relationship_id,
                )
                if deleted:
                    logger.info(f"Memory V2 entry deleted: {memory_id}")
                    return {
                        "effect_type": "memory_v2_write",
                        "status": "success",
                        "operation": "delete",
                        "entry_id": memory_id,
                    }
                return {
                    "effect_type": "memory_v2_write",
                    "status": "not_found",
                    "operation": "delete",
                    "entry_id": memory_id,
                }

            return {
                "effect_type": "memory_v2_write",
                "status": "error",
                "error": f"unknown operation: {operation}",
            }

        except Exception as e:
            logger.error(f"Memory V2 write failed: {e}")
            return {
                "effect_type": "memory_v2_write",
                "status": "error",
                "error": str(e),
            }

    async def _enqueue_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Enqueue a generic background job (placeholder implementation).

        Payload format:
        {
            "job_type": "type_identifier",
            "params": {job params},
            "priority": 0 (optional)
        }
        """
        job_type = payload.get("job_type")
        if not job_type:
            return {"effect_type": "job", "status": "error", "error": "missing job_type"}

        # Placeholder - basic job enqueueing
        job = await JobRepository.enqueue(
            self.conn,
            job_type=job_type,
            params=payload.get("params", {}),
            companion_id=self.turn_context.companion_id,
            conversation_id=self.turn_context.conversation_id,
            external_user_id=self.turn_context.external_user_id,
            priority=payload.get("priority", 0),
        )

        return {
            "effect_type": "job",
            "status": "success",
            "job_id": str(job.id),
            "job_type": job_type,
        }

    async def _send_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send data to a developer-configured webhook.

        Payload format:
        {
            "webhook_key": "configured_webhook_name",
            "event_type": "action_triggered",
            "data": {payload to send}
        }
        """
        webhook_key = payload.get("webhook_key")
        if not webhook_key:
            return {"effect_type": "webhook", "status": "error", "error": "missing webhook_key"}

        # Look up webhook configuration
        config = self.webhook_configs.get(webhook_key)
        if not config:
            logger.debug(f"Webhook {webhook_key} not configured")
            return {"effect_type": "webhook", "status": "skipped", "reason": "not_configured"}

        if not config.get("enabled", True):
            return {"effect_type": "webhook", "status": "skipped", "reason": "disabled"}

        url = config.get("url")
        if not url:
            return {"effect_type": "webhook", "status": "error", "error": "missing url in config"}

        # Build webhook payload
        webhook_payload = {
            "event_type": payload.get("event_type", "action_triggered"),
            "data": payload.get("data", {}),
            "timestamp": datetime.utcnow().isoformat(),
            "companion_id": str(self.turn_context.companion_id),
            "external_user_id": self.turn_context.external_user_id,
        }

        # Send webhook asynchronously
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=webhook_payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Secret": config.get("secret", ""),
                    },
                    timeout=5.0,
                )
                response.raise_for_status()

            return {
                "effect_type": "webhook",
                "status": "success",
                "webhook_key": webhook_key,
                "status_code": response.status_code,
            }
        except httpx.HTTPError as e:
            logger.error(f"Webhook {webhook_key} failed: {e}")
            return {
                "effect_type": "webhook",
                "status": "error",
                "webhook_key": webhook_key,
                "error": str(e),
            }

    async def _send_proactive_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a proactive message to the user (Phase 7).

        Payload format:
        {
            "content": "message content",
            "expires_in_hours": 24 (optional, default 24),
            "source_behavior_key": "behavior_key" (optional, auto-set by runtime)
        }

        The message is:
        1. Inserted into messages table with is_proactive=true, delivery_status='pending'
        2. If WebSocket connected: pushed immediately, status set to 'delivered'
        3. If not connected: stays 'pending' for inbox retrieval
        """
        content = payload.get("content")
        if not content:
            return {
                "effect_type": "proactive_message",
                "status": "error",
                "error": "missing content",
            }

        if not self.turn_context.relationship_id:
            return {
                "effect_type": "proactive_message",
                "status": "skipped",
                "reason": "no relationship_id",
            }

        # Calculate expiration time
        expires_in_hours = payload.get("expires_in_hours", 24)
        expires_at = datetime.now(UTC) + timedelta(hours=expires_in_hours)

        # Source behavior key (set by the runtime when executing behaviors)
        source_behavior_key = payload.get("source_behavior_key")

        # Get next sequence number for this relationship
        seq_row = await self.conn.fetchrow(
            "SELECT next_relationship_message_seq($1) as seq",
            self.turn_context.relationship_id,
        )
        seq = seq_row["seq"] if seq_row else None

        # Insert proactive message
        message_id = uuid4()
        await self.conn.fetchrow(
            """
            INSERT INTO messages (
                id, relationship_id, role, content, seq,
                is_proactive, delivery_status, expires_at, source_behavior_key
            )
            VALUES ($1, $2, 'assistant', $3, $4, TRUE, 'pending', $5, $6)
            RETURNING id, seq, created_at
            """,
            message_id,
            self.turn_context.relationship_id,
            content,
            seq,
            expires_at,
            source_behavior_key,
        )

        logger.info(
            f"Proactive message created: id={message_id}, "
            f"relationship={self.turn_context.relationship_id}, "
            f"seq={seq}, expires_at={expires_at}"
        )

        # Try to deliver via WebSocket if connected
        delivered = False
        try:
            # Import here to avoid circular imports
            from ..routers.v2.websockets import connection_manager

            sent_count = await connection_manager.send_proactive_message(
                relationship_id=self.turn_context.relationship_id,
                message_id=message_id,
                content=content,
                seq=seq,
                source_behavior_key=source_behavior_key,
            )

            if sent_count > 0:
                # Update delivery status to 'delivered'
                await self.conn.execute(
                    """
                    UPDATE messages
                    SET delivery_status = 'delivered'
                    WHERE id = $1
                    """,
                    message_id,
                )
                delivered = True
                logger.info(f"Proactive message delivered via WebSocket: id={message_id}")

        except Exception as e:
            logger.warning(f"Failed to deliver proactive message via WebSocket: {e}")

        return {
            "effect_type": "proactive_message",
            "status": "success",
            "message_id": str(message_id),
            "seq": seq,
            "delivered": delivered,
            "delivery_status": "delivered" if delivered else "pending",
        }


async def execute_post_turn_effects(
    conn: asyncpg.Connection,
    turn_context: TurnContext,
    effects: List[TurnEffect],
    hydrated_context: HydratedContext | None = None,
    webhook_configs: Dict[str, Dict[str, Any]] | None = None,
    memory_evaluation_prompt: str | None = None,
) -> List[Dict[str, Any]]:
    """Convenience function to execute post-turn effects.

    Args:
        conn: Database connection
        turn_context: Current turn context
        effects: List of effects to execute
        hydrated_context: Optional hydrated context for state version checking
        webhook_configs: Optional webhook configurations
        memory_evaluation_prompt: Optional guidance for memory importance scoring

    Returns:
        List of execution results
    """
    if not effects:
        return []

    executor = PostTurnExecutor(
        conn=conn,
        turn_context=turn_context,
        hydrated_context=hydrated_context,
        webhook_configs=webhook_configs,
        memory_evaluation_prompt=memory_evaluation_prompt,
    )
    return await executor.execute(effects)


async def persist_turn_context(
    conn: asyncpg.Connection,
    *,
    plan: ContextPlan,
    turn_context: TurnContext,
    message_id: UUID | None = None,
    llm_ms: int | None = None,
) -> UUID | None:
    """Persist turn context snapshot to database.

    This should be called asynchronously after the LLM response is sent
    to avoid blocking the response path.

    Args:
        conn: Database connection
        plan: The context plan built for this turn
        turn_context: Current turn context with companion/conversation info
        message_id: Optional assistant message ID to link to
        llm_ms: Optional LLM response time in milliseconds

    Returns:
        UUID of the created turn_context record, or None on failure
    """
    try:
        # Extract execution summary and other trace data
        trace = plan.trace
        execution_summary = trace.get("execution_summary")
        classifier_trace = trace.get("classifier")

        # Determine context mode and classifier usage
        context_mode = (
            "layered" if execution_summary and not execution_summary.get("raw_mode") else "raw"
        )
        classifier_used = (
            execution_summary.get("classifier_used", False) if execution_summary else False
        )

        # Extract the full crafted system prompt (concatenate all system messages)
        system_parts = []
        for msg in plan.messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if content:
                    system_parts.append(content)
        system_prompt = "\n\n".join(system_parts) if system_parts else None

        # Get timing data
        build_ms = int(trace.get("build_ms", 0))
        classifier_ms = int(classifier_trace.get("duration_ms", 0)) if classifier_trace else None

        # Build layer details for debugging
        layer_details: Dict[str, Any] = {}
        if trace.get("memory"):
            layer_details["memory"] = trace.get("memory")
        if trace.get("knowledge"):
            layer_details["knowledge"] = trace.get("knowledge")
        if classifier_trace:
            layer_details["classifier"] = {
                "model": classifier_trace.get("model"),
                "success": classifier_trace.get("success"),
            }

        # Get turn number (increment if not provided)
        turn_number = 1
        if turn_context.conversation_id:
            turn_number = await StateRepository.increment_turn_count(
                conn, turn_context.conversation_id
            )

        # Save to database
        turn_context_id = await StateRepository.save_turn_context(
            conn,
            conversation_id=turn_context.conversation_id,
            companion_id=turn_context.companion_id,
            turn_number=turn_number,
            message_id=message_id,
            context_mode=context_mode,
            classifier_used=classifier_used,
            system_prompt=system_prompt,
            system_prompt_tokens=None,  # Skip token counting for now
            execution_summary=execution_summary,
            token_usage=plan.token_usage,
            build_ms=build_ms,
            classifier_ms=classifier_ms,
            llm_ms=llm_ms,
            layer_details=layer_details if layer_details else None,
        )

        logger.debug(
            f"Persisted turn context: conv={turn_context.conversation_id}, "
            f"turn={turn_number}, mode={context_mode}"
        )
        return turn_context_id

    except Exception as e:
        logger.error(f"Failed to persist turn context: {e}")
        return None
