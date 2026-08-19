"""
ToolsRuntime: Executes developer-supplied tools based on conversation context.

## Architecture (v2 - Two-Node Design)

This runtime has two distinct execution phases that can run on separate infrastructure:

┌─────────────────────────────────────────────────────────────────────────────┐
│ NODE 1: Meta-Tool Selection (can run in FastAPI or Modal)                   │
│                                                                              │
│   Input: user_message, conversation_context                                  │
│   │                                                                          │
│   ├─► 1. Hybrid search across companion's registered tools                   │
│   │      - Full-text search (PostgreSQL ts_rank)                             │
│   │      - Semantic search (embeddings) [future]                             │
│   │                                                                          │
│   ├─► 2. Rank and select best tool(s) based on:                              │
│   │      - Query relevance score                                             │
│   │      - Tool priority (developer-configured)                              │
│   │      - Context fit (conversation state)                                  │
│   │                                                                          │
│   └─► Output: selected_tool, tool_spec, execution_params                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ NODE 2: Tool Execution (runs in Modal worker for security/isolation)        │
│                                                                              │
│   Input: tool_spec, execution_params, user_context                           │
│   │                                                                          │
│   ├─► 1. Validate tool spec and permissions                                  │
│   │                                                                          │
│   ├─► 2. Execute tool in sandboxed environment                               │
│   │      - Tools are developer-supplied endpoints (HTTP, functions)          │
│   │      - Execution is isolated for security                                │
│   │      - Timeout and resource limits enforced                              │
│   │                                                                          │
│   ├─► 3. Capture execution trace for debugging                               │
│   │      - Input/output logging                                              │
│   │      - Timing information                                                │
│   │      - Error details if failed                                           │
│   │                                                                          │
│   └─► Output: tool_result, execution_trace                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ POST-TURN: Result Injection                                                  │
│                                                                              │
│   The tool_result and execution_trace are passed to the post-turn executor   │
│   which injects them into the system prompt as a block:                      │
│                                                                              │
│   # TOOL RESULT                                                              │
│   Tool: get_weather                                                          │
│   Result: {"temp": 72, "conditions": "sunny"}                                │
│                                                                              │
│   This context is then available to the LLM for generating the response.     │
└─────────────────────────────────────────────────────────────────────────────┘

## Developer Integration

Tools are supplied by developers as:
- HTTP endpoints (REST APIs)
- Webhook URLs
- Function specs (for Modal execution)

Developers register tools via the companion configuration with:
- name, summary, description
- spec (JSON schema for inputs/outputs)
- endpoint URL or function reference
- priority and enabled flags

## Current Status (v1)

Currently implements a simplified single-node version with placeholder execution.
The two-node architecture will be implemented as we add Modal integration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import traceback
from typing import Any, Dict, List, Tuple
from uuid import UUID

import asyncpg
import modal

from ..repositories.tool_index_repository import ToolIndexRepository
from ..request_context import try_get_request_context
from .layers import ConnectionFactory, EventCallback, LayerOutput, LayerRuntime
from .schemas import ContextEvent, GateResult

logger = logging.getLogger(__name__)

DEFAULT_MODAL_ENV = "main"
worker = modal.Cls.from_name(
    "em-tools", "ToolsWorker", environment_name=os.getenv("MODAL_ENVIRONMENT", DEFAULT_MODAL_ENV)
)


class ToolsRuntime(LayerRuntime):
    key = "tools"

    def __init__(
        self,
        *,
        conn: asyncpg.Connection | None = None,
        conn_factory: ConnectionFactory | None = None,
        companion_id: UUID,
        user_text: str,
        tool_summary: str | None = None,
        params: Dict[str, Any] | None = None,
        relationship_id: UUID | None = None,
    ) -> None:
        if conn is None and conn_factory is None:
            raise ValueError("Either conn or conn_factory must be provided")
        self.conn = conn
        self.conn_factory = conn_factory
        self.companion_id = companion_id
        self.user_text = user_text
        self.tool_summary = tool_summary
        self.params = params or {}
        self.relationship_id = relationship_id

    async def run(self, event_callback: EventCallback | None = None) -> LayerOutput:
        # Yield to event loop to allow parallel layers to start simultaneously
        await asyncio.sleep(0)

        events: List[ContextEvent] = []
        messages: List[Dict[str, str]] = []

        t0 = time.perf_counter()
        events.append(
            ContextEvent(name="tools:retrieving", phase="start", meta={}, ts_ms=_elapsed_ms(t0))
        )

        if not (self.user_text or "").strip():
            events.append(
                ContextEvent(
                    name="tools:retrieving",
                    phase="end",
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)

        """
        gate_result = self._should_run_gate()
        if not gate_result.run:
            logger.info(
                "[tools_runtime] Gate blocked execution | reason=%s",
                gate_result.reason,
            )
            events.append(
                ContextEvent(
                    name="tools:retrieving",
                    phase="end",
                    meta={"skipped": True, "gate": gate_result.model_dump()},
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)
        """

        # Resolve project + latest spec for this companion
        spec_row: Dict[str, Any] | None = None
        project_id: UUID | None = None
        try:
            if self.conn_factory:
                async with self.conn_factory() as conn:
                    project_id = await _fetch_project_id(conn, self.companion_id)
                    spec_row = await ToolIndexRepository.get_latest_spec_for_companion(
                        conn, companion_id=self.companion_id
                    )
            else:
                project_id = await _fetch_project_id(self.conn, self.companion_id)
                spec_row = await ToolIndexRepository.get_latest_spec_for_companion(
                    self.conn, companion_id=self.companion_id
                )
        except Exception as exc:
            logger.error(
                "[tools_runtime] Failed to fetch project/spec | companion_id=%s error=%s\n%s",
                self.companion_id,
                str(exc),
                traceback.format_exc(),
            )
            events.append(
                ContextEvent(
                    name="tools:retrieving",
                    phase="error",
                    meta={
                        "error": str(exc),
                        "error_type": "db_fetch_error",
                        "traceback": traceback.format_exc(),
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)

        if not spec_row or not project_id:
            logger.info(
                "[tools_runtime] No spec found for companion | companion_id=%s project_id=%s spec_row=%s",
                self.companion_id,
                project_id,
                "found" if spec_row else "not_found",
            )
            events.append(
                ContextEvent(
                    name="tools:retrieving",
                    phase="end",
                    meta={
                        "results": 0,
                        "reason": "no_spec",
                        "project_id": str(project_id) if project_id else None,
                    },
                    ts_ms=_elapsed_ms(t0),
                )
            )
            return LayerOutput(messages=messages, events=events)

        logger.info(
            "[tools_runtime] Found spec | companion_id=%s project_id=%s spec_id=%s",
            self.companion_id,
            project_id,
            spec_row["id"],
        )

        base_api_url = self.params.get("base_api_url") or spec_row.get("base_url")
        api_key = self.params.get("api_key")
        spec_json = spec_row.get("json_content")
        inline_spec_json: Dict[str, Any] | None = None
        if isinstance(spec_json, dict):
            try:
                spec_size = len(json.dumps(spec_json))
                if spec_size <= 200_000:
                    inline_spec_json = spec_json
                else:
                    logger.info(
                        "[tools_runtime] Skipping inline spec payload for large spec | companion_id=%s spec_id=%s size=%d",
                        self.companion_id,
                        spec_row["id"],
                        spec_size,
                    )
            except Exception:
                inline_spec_json = None

        events.append(
            ContextEvent(
                name="tools:retrieving",
                phase="info",
                meta={"spec_id": str(spec_row["id"])},
                ts_ms=_elapsed_ms(t0),
            )
        )

        # Step 1: retrieve and parametrize in one shot
        best_tool, params_obj, retrieve_meta = await _choose_and_parametrize_tool(
            project_id=spec_row["project_id"],
            spec_id=spec_row["id"],
            query=self.user_text,
            relationship_id=self.relationship_id,
            spec_json=inline_spec_json,
        )
        events.append(
            ContextEvent(
                name="tools:retrieving", phase="end", meta=retrieve_meta, ts_ms=_elapsed_ms(t0)
            )
        )
        if not best_tool:
            logger.warning(
                "[tools_runtime] No tool selected | companion_id=%s retrieve_meta=%s",
                self.companion_id,
                json.dumps(retrieve_meta)[:500],
            )
            return LayerOutput(messages=messages, events=events)

        # Get operation metadata (method/api_key)
        op_meta: Dict[str, Any] | None = None
        try:
            if self.conn_factory:
                async with self.conn_factory() as conn:
                    op_meta = await ToolIndexRepository.get_operation_by_name(
                        conn, project_id=project_id, spec_id=spec_row["id"], name=best_tool
                    )
            else:
                op_meta = await ToolIndexRepository.get_operation_by_name(
                    self.conn, project_id=project_id, spec_id=spec_row["id"], name=best_tool
                )
        except Exception as op_exc:
            logger.warning(
                "[tools_runtime] Failed to fetch operation metadata | tool=%s error=%s\n%s",
                best_tool,
                str(op_exc),
                traceback.format_exc(),
            )
            op_meta = None

        # Parameters were produced during selection
        params_obj = params_obj or {}
        params_text = json.dumps(params_obj or {})

        logger.info(
            "[tools_runtime] Executing tool | tool=%s params=%s has_op_meta=%s",
            best_tool,
            params_text[:200],
            op_meta is not None,
        )

        # Step 3: execute
        events.append(
            ContextEvent(
                name="tools:executing",
                phase="start",
                meta={"tool": best_tool},
                ts_ms=_elapsed_ms(t0),
            )
        )
        exec_result, exec_meta = await _execute_tool(
            project_id=spec_row["project_id"],
            spec_id=spec_row["id"],
            base_url=base_api_url,
            api_key=op_meta.get("api_key") if op_meta and op_meta.get("api_key") else api_key,
            tool_name=best_tool,
            parameters=params_obj,
            relationship_id=self.relationship_id,
            spec_json=inline_spec_json,
        )
        events.append(
            ContextEvent(name="tools:executing", phase="end", meta=exec_meta, ts_ms=_elapsed_ms(t0))
        )

        # Emit summary + trace + result blocks
        if self.tool_summary:
            messages.append({"role": "system", "content": f"# TOOLS SUMMARY\n{self.tool_summary}"})

        trace_lines = [
            "# TOOL TRACE",
            f"- selected: {best_tool}",
            f"- params: {params_text}",
            f"- success: {exec_meta.get('success', False)}",
        ]
        messages.append({"role": "system", "content": "\n".join(trace_lines)})

        result_text = exec_result if exec_result is not None else "No result returned."
        messages.append(
            {
                "role": "system",
                "content": f"# TOOL RESULT\nTool: {best_tool}\nResult: {result_text}",
            }
        )

        # Include result in event meta for frontend consumption (e.g., companion_id from onboarding)
        result_meta = {"tool": best_tool, "success": exec_meta.get("success", False)}
        if exec_result and isinstance(exec_result, str):
            try:
                result_meta["result"] = json.loads(exec_result)
            except (json.JSONDecodeError, TypeError):
                pass  # Not JSON, skip
        result_event = ContextEvent(
            name="tools:result",
            phase="end",
            meta=result_meta,
            ts_ms=_elapsed_ms(t0),
        )
        events.append(result_event)
        # Emit via callback so it can be forwarded to WebSocket (for onboarding redirect)
        if event_callback:
            try:
                event_callback(result_event)
            except Exception:
                pass  # Non-fatal, don't break the flow

        elapsed_total = _elapsed_ms(t0)
        if exec_meta.get("success"):
            logger.info(
                "[tools_runtime] run() completed successfully | companion_id=%s tool=%s elapsed_ms=%.1f result_size=%d",
                self.companion_id,
                best_tool,
                elapsed_total,
                len(exec_result) if exec_result else 0,
            )
        else:
            logger.warning(
                "[tools_runtime] run() completed with failure | companion_id=%s tool=%s elapsed_ms=%.1f error=%s error_type=%s",
                self.companion_id,
                best_tool,
                elapsed_total,
                exec_meta.get("error"),
                exec_meta.get("error_type"),
            )

        return LayerOutput(messages=messages, events=events)

    def _should_run_gate(self) -> GateResult:
        """Evaluate gate and return structured result for debugging."""
        strategy = str(self.params.get("gate_strategy", "keyword")).lower()
        text = (self.user_text or "").lower()
        keywords = self.params.get("keywords") or []
        inputs = {
            "strategy": strategy,
            "text_length": len(text),
            "has_question_mark": "?" in text,
            "keywords_configured": len(keywords),
        }

        if strategy in ("none", "no_gate", "always"):
            return GateResult(run=True, reason="gate_disabled", inputs=inputs)

        if strategy in ("llm", "gemini"):
            ok = _llm_gate(self.user_text)
            if ok is not None:
                return GateResult(
                    run=ok, reason="llm_gate_" + ("passed" if ok else "failed"), inputs=inputs
                )

        # keyword fallback
        if len(text) > 48:
            return GateResult(run=True, reason="text_length_heuristic", inputs=inputs)
        if "?" in text:
            return GateResult(run=True, reason="question_mark_heuristic", inputs=inputs)
        if keywords:
            toks = set(text.split())
            matched = [k for k in keywords if k.lower() in toks]
            if matched:
                return GateResult(run=True, reason=f"keyword_match:{matched[:3]}", inputs=inputs)
            return GateResult(run=False, reason="no_keyword_match", inputs=inputs)
        return GateResult(run=False, reason="heuristic_failed", inputs=inputs)


async def _fetch_project_id(conn: asyncpg.Connection, companion_id: UUID) -> UUID | None:
    """Fetch project_id for a companion.

    First checks RequestContext for cached project_id (set during API key auth).
    Falls back to database query if context unavailable (e.g., background tasks).

    NOTE: The cached project_id from auth is trusted because the auth layer
    validates that the companion belongs to the authenticated project.
    """
    # Try to get project_id from request context (avoids DB query)
    ctx = try_get_request_context()
    if ctx and ctx.project_id:
        return ctx.project_id

    # Fallback to DB query (for background tasks or when context unavailable)
    row = await conn.fetchrow("SELECT project_id FROM companions WHERE id = $1", companion_id)
    if not row:
        return None
    return row["project_id"]


def _llm_gate(user_text: str) -> bool | None:
    try:
        import os

        import google.generativeai as genai

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        prompt = (
            "Decide if tool use is needed for the user's message. Respond YES or NO only.\n\n"
            f"User: {user_text}\n"
        )
        resp = genai.GenerativeModel("gemini-2.5-flash").generate_content(prompt)
        txt = (resp.text or "").strip().lower()
        if "yes" in txt and "no" not in txt:
            return True
        if "no" in txt and "yes" not in txt:
            return False
    except Exception:
        return None
    return None


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


async def _choose_and_parametrize_tool(
    project_id: UUID,
    spec_id: UUID,
    query: str,
    relationship_id: UUID | None = None,
    spec_json: Dict[str, Any] | None = None,
) -> Tuple[str | None, Dict[str, Any] | None, Dict[str, Any]]:
    payload = {
        "request_id": str(UUID(int=int(time.time() * 1e6) % (1 << 128))),
        "project_id": str(project_id),
        "spec_id": str(spec_id),
        "query": query,
        "relationship_id": str(relationship_id) if relationship_id else None,
        "spec_json": spec_json,
    }
    meta: Dict[str, Any] = {"request_id": payload["request_id"]}
    try:
        resp = await worker().choose_and_parametrize_tool.remote.aio(payload)
    except Exception as exc:
        error_msg = f"Modal worker call failed: {type(exc).__name__}: {exc}"
        logger.error(
            "[tools_runtime] choose_and_parametrize_tool Modal call FAILED | request_id=%s error=%s\n%s",
            payload["request_id"],
            error_msg,
            traceback.format_exc(),
        )
        meta["error"] = error_msg
        meta["error_type"] = "modal_call_exception"
        meta["traceback"] = traceback.format_exc()
        return None, None, meta

    # Log raw response for debugging
    logger.info(
        "[tools_runtime] choose_and_parametrize_tool response | request_id=%s status=%s response=%s",
        payload["request_id"],
        resp.get("status") if isinstance(resp, dict) else "INVALID_RESPONSE_TYPE",
        json.dumps(resp)[:500] if isinstance(resp, dict) else str(resp)[:500],
    )

    if not isinstance(resp, dict):
        error_msg = f"Invalid response type from Modal worker: {type(resp).__name__}"
        logger.error("[tools_runtime] %s | response=%r", error_msg, resp)
        meta["error"] = error_msg
        meta["error_type"] = "invalid_response_type"
        return None, None, meta

    if resp.get("status") == "error":
        logger.warning(
            "[tools_runtime] choose_and_parametrize_tool returned error | request_id=%s message=%s",
            payload["request_id"],
            resp.get("message"),
        )
        meta["error"] = resp.get("message")
        meta["error_type"] = "worker_error"
        return None, None, meta

    if resp.get("status") != "success":
        logger.warning(
            "[tools_runtime] choose_and_parametrize_tool unexpected status | request_id=%s status=%s response=%s",
            payload["request_id"],
            resp.get("status"),
            json.dumps(resp)[:500],
        )
        meta["error"] = f"Unexpected status: {resp.get('status')}"
        meta["error_type"] = "unexpected_status"
        meta["raw_response"] = resp
        return None, None, meta

    best = resp.get("best_tool")
    params_obj = resp.get("parameters") or {}

    if isinstance(params_obj, str):
        try:
            params_obj = json.loads(params_obj)
        except Exception as parse_exc:
            logger.warning(
                "[tools_runtime] Failed to parse parameters JSON | request_id=%s params=%r error=%s",
                payload["request_id"],
                params_obj[:200],
                str(parse_exc),
            )
            params_obj = {}

    if not best:
        logger.warning(
            "[tools_runtime] No tool selected | request_id=%s response=%s",
            payload["request_id"],
            json.dumps(resp)[:500],
        )
        meta["error"] = "No tool was selected by the worker"
        meta["error_type"] = "no_tool_selected"
        return None, None, meta

    logger.info(
        "[tools_runtime] Tool selected successfully | request_id=%s tool=%s params=%s",
        payload["request_id"],
        best,
        json.dumps(params_obj)[:200],
    )
    meta["tool"] = best
    meta["parameters"] = params_obj
    return best, params_obj, meta


async def _execute_tool(
    project_id: UUID,
    spec_id: UUID,
    base_url: str | None,
    api_key: str | None,
    tool_name: str,
    parameters: Dict[str, Any] | str,
    relationship_id: UUID | None = None,
    spec_json: Dict[str, Any] | None = None,
) -> Tuple[str | None, Dict[str, Any]]:
    payload = {
        "request_id": str(UUID(int=int(time.time() * 1e6) % (1 << 128))),
        "project_id": str(project_id),
        "spec_id": str(spec_id),
        "base_url": base_url,
        "api_key": api_key,
        "tool_name": tool_name,
        "parameters": json.loads(parameters) if isinstance(parameters, str) else parameters,
        "relationship_id": str(relationship_id) if relationship_id else None,
        "spec_json": spec_json,
    }
    meta: Dict[str, Any] = {"request_id": payload["request_id"], "tool": tool_name}

    try:
        resp = await worker().use_api_tool.remote.aio(payload)
    except Exception as exc:
        error_msg = f"Modal worker call failed: {type(exc).__name__}: {exc}"
        logger.error(
            "[tools_runtime] execute_tool Modal call FAILED | request_id=%s tool=%s error=%s\n%s",
            payload["request_id"],
            tool_name,
            error_msg,
            traceback.format_exc(),
        )
        meta["error"] = error_msg
        meta["error_type"] = "modal_call_exception"
        meta["traceback"] = traceback.format_exc()
        meta["success"] = False
        return None, meta

    # Log raw response for debugging
    logger.info(
        "[tools_runtime] execute_tool response | request_id=%s tool=%s status=%s response=%s",
        payload["request_id"],
        tool_name,
        resp.get("status") if isinstance(resp, dict) else "INVALID_RESPONSE_TYPE",
        json.dumps(resp)[:500] if isinstance(resp, dict) else str(resp)[:500],
    )

    if resp.get("status") == "error":
        logger.warning(
            "[tools_runtime] execute_tool returned error | request_id=%s tool=%s message=%s",
            payload["request_id"],
            tool_name,
            resp.get("message"),
        )
        meta["error"] = resp.get("message")
        meta["error_type"] = "worker_error"
        meta["success"] = False
        return meta["error"], meta

    if resp.get("status") != "success":
        logger.warning(
            "[tools_runtime] execute_tool unexpected status | request_id=%s tool=%s status=%s response=%s",
            payload["request_id"],
            tool_name,
            resp.get("status"),
            json.dumps(resp)[:500],
        )
        meta["error"] = f"Unexpected status: {resp.get('status')}"
        meta["error_type"] = "unexpected_status"
        meta["raw_response"] = resp
        meta["success"] = False
        return None, meta

    api_response = resp.get("api_response")
    meta["success"] = True
    return json.dumps(api_response) if api_response is not None else None, meta


__all__ = ["ToolsRuntime"]
