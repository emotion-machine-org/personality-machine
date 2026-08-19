# server/app/routers/voice/voice_workspace.py
"""S3-backed shared workspace for Fast Brain / Slow Brain collaboration.

Both brains can read/write files in a per-relationship workspace:
  s3://bucket/voice-workspace/{relationship_id}/
    ├── hot_context.md      # Task state (append-only log)
    ├── scratchpad.md       # Notes between brains
    └── ...

Fast Brain (Python): Uses boto3 directly
Slow Brain (OpenClaw): Calls EM API which proxies to S3
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# Configuration - reuse existing bucket
_S3_BUCKET = os.getenv("KNOWLEDGE_S3_BUCKET")
_S3_REGION = os.getenv("KNOWLEDGE_S3_REGION") or os.getenv("AWS_REGION")
_S3_PREFIX = "voice-workspace"  # Hardcoded prefix

_s3_client = None


def _get_s3_client():
    """Get or create S3 client."""
    global _s3_client
    if _s3_client is None:
        kwargs = {}
        if _S3_REGION:
            kwargs["region_name"] = _S3_REGION
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _get_key(relationship_id: str | UUID, path: str) -> str:
    """Build S3 key for a workspace file."""
    rel_id = str(relationship_id)
    # Sanitize path - no leading slashes, no ..
    clean_path = path.lstrip("/").replace("..", "")
    return f"{_S3_PREFIX}/{rel_id}/{clean_path}"


class VoiceWorkspace:
    """S3-backed workspace for a specific relationship."""

    def __init__(self, relationship_id: str | UUID):
        self.relationship_id = str(relationship_id)
        self._client = _get_s3_client()
        self._bucket = _S3_BUCKET

        if not self._bucket:
            raise RuntimeError("KNOWLEDGE_S3_BUCKET not configured")

    def _key(self, path: str) -> str:
        return _get_key(self.relationship_id, path)

    def read(self, path: str) -> str | None:
        """Read a file from the workspace. Returns None if not found."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(path))
            return response["Body"].read().decode("utf-8")
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            # Missing objects may surface as NoSuchKey/NotFound/404, or AccessDenied
            # when ListBucket/GetObject permissions are restricted.
            if error_code in {"NoSuchKey", "NotFound", "404"}:
                return None
            if error_code == "AccessDenied":
                logger.debug(f"[VOICE_WORKSPACE] Treating AccessDenied as missing file for {path}")
                return None
            logger.error(f"[VOICE_WORKSPACE] Read error: {e}")
            raise
        except BotoCoreError as e:
            logger.error(f"[VOICE_WORKSPACE] Read error: {e}")
            raise

    def write(self, path: str, content: str) -> None:
        """Write/overwrite a file in the workspace."""
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=self._key(path),
                Body=content.encode("utf-8"),
                ContentType="text/plain; charset=utf-8",
            )
            logger.debug(f"[VOICE_WORKSPACE] Wrote {path} for {self.relationship_id}")
        except (BotoCoreError, ClientError) as e:
            logger.error(f"[VOICE_WORKSPACE] Write error: {e}")
            raise

    def append(self, path: str, content: str) -> None:
        """Append to a file (read + write, not atomic but good enough for logs)."""
        existing = self.read(path) or ""
        self.write(path, existing + content)

    def delete(self, path: str) -> None:
        """Delete a file from the workspace."""
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._key(path))
        except (BotoCoreError, ClientError) as e:
            logger.error(f"[VOICE_WORKSPACE] Delete error: {e}")
            raise

    def exists(self, path: str) -> bool:
        """Check if a file exists."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._key(path))
            return True
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey"):
                return False
            # AccessDenied with ListBucket = file doesn't exist
            if error_code == "AccessDenied":
                return False
            raise

    def list_files(self, prefix: str = "") -> list[str]:
        """List files in the workspace (optionally under a prefix)."""
        try:
            base_key = self._key(prefix)
            response = self._client.list_objects_v2(Bucket=self._bucket, Prefix=base_key)
            files = []
            for obj in response.get("Contents", []):
                # Strip the base prefix to get relative path
                rel_path = obj["Key"][len(f"{_S3_PREFIX}/{self.relationship_id}/") :]
                files.append(rel_path)
            return files
        except (BotoCoreError, ClientError) as e:
            logger.error(f"[VOICE_WORKSPACE] List error: {e}")
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Hot Context (Task State) - Specialized interface
# ─────────────────────────────────────────────────────────────────────────────

HOT_CONTEXT_FILE = "hot_context.md"


class HotContextS3:
    """Hot context backed by S3 - tracks voice task state."""

    _RECENT_SECONDS = 6 * 60 * 60  # 6 hours

    def __init__(self, relationship_id: str | UUID):
        self.workspace = VoiceWorkspace(relationship_id)
        self.relationship_id = str(relationship_id)

    def _format_event(self, event_type: str, task_id: str, data: str = "") -> str:
        """Format a single event line."""
        ts = datetime.now(UTC).isoformat()
        return f"{event_type}|{task_id}|{ts}|{data}\n"

    def log_start(self, task_id: str, task: str) -> None:
        """Log task started."""
        line = self._format_event("task_started", task_id, task[:200])
        self.workspace.append(HOT_CONTEXT_FILE, line)
        logger.info(f"[HOT_CONTEXT_S3] Task started: {task_id}")

    def log_ack(self, task_id: str, message: str = "") -> None:
        """Log task acknowledged."""
        line = self._format_event("task_acked", task_id, message[:200])
        self.workspace.append(HOT_CONTEXT_FILE, line)

    def log_done(self, task_id: str, result: str) -> None:
        """Log task completed."""
        line = self._format_event("task_done", task_id, result[:500])
        self.workspace.append(HOT_CONTEXT_FILE, line)
        logger.info(f"[HOT_CONTEXT_S3] Task done: {task_id}")

    def log_fail(self, task_id: str, error: str) -> None:
        """Log task failed."""
        line = self._format_event("task_failed", task_id, error[:200])
        self.workspace.append(HOT_CONTEXT_FILE, line)
        logger.warning(f"[HOT_CONTEXT_S3] Task failed: {task_id}")

    def get_task_result(self, task_id: str) -> tuple[str, str] | None:
        """Get task result. Returns (status, result/error) or None if not found."""
        content = self.workspace.read(HOT_CONTEXT_FILE)
        if not content:
            return None

        # Parse lines in reverse to get latest status
        for line in reversed(content.strip().split("\n")):
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) >= 3:
                event_type, tid, _ts = parts[0], parts[1], parts[2]
                data = parts[3] if len(parts) > 3 else ""

                if tid == task_id:
                    if event_type == "task_done":  # noqa: SIM116
                        return ("done", data)
                    elif event_type == "task_failed":
                        return ("failed", data)
                    elif event_type == "task_acked":
                        return ("acked", data)
                    elif event_type == "task_started":
                        return ("started", data)

        return None

    def _parse_events(self) -> list[dict[str, str]]:
        """Parse all events from the hot_context log."""
        content = self.workspace.read(HOT_CONTEXT_FILE)
        if not content:
            return []

        events: list[dict[str, str]] = []
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) < 3:
                continue
            event_type, task_id, ts = parts[0], parts[1], parts[2]
            data = parts[3] if len(parts) > 3 else ""
            events.append(
                {
                    "type": event_type,
                    "task_id": task_id,
                    "ts": ts,
                    "data": data,
                }
            )
        return events

    def _summarize_tasks(self) -> dict[str, list[tuple[str, dict[str, str]]]]:
        """Summarize tasks by status for rendering."""
        events = self._parse_events()
        tasks: dict[str, dict[str, str]] = {}

        for event in events:
            task_id = event["task_id"]
            task = tasks.setdefault(
                task_id,
                {"status": "unknown", "query": "", "result": "", "error": "", "ts": ""},
            )
            task["ts"] = event.get("ts", "")

            event_type = event.get("type", "")
            data = event.get("data", "")

            if event_type == "task_started":
                task["status"] = "pending"
                task["query"] = data
            elif event_type == "task_acked":
                if task["status"] not in ("done", "failed"):
                    task["status"] = "acked"
            elif event_type == "task_done":
                task["status"] = "done"
                task["result"] = data
            elif event_type == "task_failed":
                task["status"] = "failed"
                task["error"] = data

        def sort_key(item: tuple[str, dict[str, str]]) -> str:
            return item[1].get("ts", "")

        pending = sorted(
            [(tid, t) for tid, t in tasks.items() if t.get("status") in ("pending", "acked")],
            key=sort_key,
            reverse=True,
        )
        done = sorted(
            [(tid, t) for tid, t in tasks.items() if t.get("status") == "done"],
            key=sort_key,
            reverse=True,
        )
        failed = sorted(
            [(tid, t) for tid, t in tasks.items() if t.get("status") == "failed"],
            key=sort_key,
            reverse=True,
        )

        return {"pending": pending, "done": done, "failed": failed}

    def _parse_ts(self, ts: str) -> datetime | None:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None

    def _is_recent(self, ts: str) -> bool:
        parsed = self._parse_ts(ts)
        if not parsed:
            return False
        cutoff = datetime.now(UTC) - timedelta(seconds=self._RECENT_SECONDS)
        return parsed >= cutoff

    def render(
        self,
        max_pending: int = 5,
        max_done: int = 3,
        max_failed: int = 3,
        max_chars: int = 1200,
    ) -> str:
        """Render hot context as markdown for system prompt injection."""
        summary = self._summarize_tasks()
        pending = [(tid, t) for tid, t in summary["pending"] if self._is_recent(t.get("ts", ""))]
        done = [(tid, t) for tid, t in summary["done"] if self._is_recent(t.get("ts", ""))]
        failed = [(tid, t) for tid, t in summary["failed"] if self._is_recent(t.get("ts", ""))]

        lines: list[str] = ["## Voice Event Context"]

        if pending:
            lines.append("### Ongoing Events")
            for task_id, task in pending[:max_pending]:
                label = task.get("query") or "In progress"
                lines.append(f"- **{task_id[:8]}**: {label}")

        if done:
            lines.append("### Recent Events")
            for task_id, task in done[:max_done]:
                result = task.get("result") or "Completed"
                lines.append(f"- **{task_id[:8]}**: ✅ {result}")

        if failed:
            lines.append("### Failed Events")
            for task_id, task in failed[:max_failed]:
                error = task.get("error") or "Failed"
                lines.append(f"- **{task_id[:8]}**: ❌ {error}")

        if not pending and not done and not failed:
            lines.append("_No recent events in the last 6 hours._")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        return text

    def render_spoken(
        self,
        max_pending: int = 3,
        max_done: int = 3,
        max_failed: int = 2,
    ) -> str:
        """Render a concise, spoken-friendly summary."""
        summary = self._summarize_tasks()
        pending = [(tid, t) for tid, t in summary["pending"] if self._is_recent(t.get("ts", ""))]
        done = [(tid, t) for tid, t in summary["done"] if self._is_recent(t.get("ts", ""))]
        failed = [(tid, t) for tid, t in summary["failed"] if self._is_recent(t.get("ts", ""))]

        if not pending and not done and not failed:
            return "No recent events in the last 6 hours."

        parts: list[str] = []

        if pending:
            items = []
            for task_id, task in pending[:max_pending]:
                label = task.get("query") or "in progress"
                items.append(f"{task_id[:6]} {label}")
            parts.append(f"Ongoing: {', '.join(items)}.")

        if done:
            items = []
            for task_id, task in done[:max_done]:
                result = task.get("result") or "completed"
                items.append(f"{task_id[:6]} {result}")
            parts.append(f"Recent events: {', '.join(items)}.")

        if failed:
            items = []
            for task_id, task in failed[:max_failed]:
                error = task.get("error") or "failed"
                items.append(f"{task_id[:6]} {error}")
            parts.append(f"Failed events: {', '.join(items)}.")

        return " ".join(parts)

    def clear_old_tasks(self, keep_minutes: int = 30) -> None:
        """Clear tasks older than keep_minutes (for cleanup)."""
        content = self.workspace.read(HOT_CONTEXT_FILE)
        if not content:
            return

        cutoff = datetime.now(UTC).timestamp() - (keep_minutes * 60)
        new_lines = []

        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) >= 3:
                try:
                    ts = datetime.fromisoformat(parts[2]).timestamp()
                    if ts > cutoff:
                        new_lines.append(line)
                except ValueError:
                    new_lines.append(line)  # Keep malformed lines

        if new_lines:
            self.workspace.write(HOT_CONTEXT_FILE, "\n".join(new_lines) + "\n")
        else:
            self.workspace.delete(HOT_CONTEXT_FILE)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience functions
# ─────────────────────────────────────────────────────────────────────────────


def get_workspace(relationship_id: str | UUID) -> VoiceWorkspace:
    """Get a workspace for a relationship."""
    return VoiceWorkspace(relationship_id)


def get_hot_context(relationship_id: str | UUID) -> HotContextS3:
    """Get hot context for a relationship."""
    return HotContextS3(relationship_id)
