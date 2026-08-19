"""
hot_context.py — Append-Only Event Log for Fast/Slow Brain Coordination

Race-condition-proof shared state using atomic file appends.
Inspired by Cursor's self-driving codebase learnings:
"Accept some turbulence and let the system naturally converge."

Usage:
    ctx = HotContext("/path/to/hot_context.md")

    # Slow brain writes events
    ctx.log_start("abc123", "write a cat script")
    ctx.log_done("abc123", "Created random_cat.py with 15 expressions")

    # Fast brain reads state
    status, result = ctx.get_task_status("abc123")
    # → ("done", "Created random_cat.py with 15 expressions")
"""

import fcntl
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

EventType = Literal["START", "ACK", "PROGRESS", "DONE", "FAIL"]


@dataclass
class TaskEvent:
    """A single event in the log."""

    timestamp: datetime
    event_type: EventType
    task_id: str
    content: str

    def to_line(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%dT%H:%M:%S")
        # Escape newlines in content
        safe_content = self.content.replace("\n", "\\n")
        return f"[{ts}] {self.event_type} task={self.task_id} {safe_content}\n"

    @classmethod
    def from_line(cls, line: str) -> Optional["TaskEvent"]:
        """Parse a log line back into a TaskEvent."""
        pattern = r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\] (START|ACK|PROGRESS|DONE|FAIL) task=(\S+) (.+)"
        match = re.match(pattern, line.strip())
        if not match:
            return None

        ts_str, event_type, task_id, content = match.groups()
        timestamp = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
        # Unescape newlines
        content = content.replace("\\n", "\n")
        return cls(timestamp, event_type, task_id, content)  # type: ignore


@dataclass
class TaskStatus:
    """Current status of a task, derived from events."""

    task_id: str
    status: Literal["pending", "acked", "processing", "done", "failed", "unknown"]
    query: str | None = None  # Original query (from START)
    result: str | None = None  # Final result (from DONE)
    error: str | None = None  # Error message (from FAIL)
    last_update: datetime | None = None
    started_at: datetime | None = None


class HotContext:
    """
    Append-only event log for fast/slow brain coordination.

    Thread-safe. Race-condition-proof via atomic appends.
    """

    def __init__(self, path: str = "hot_context.md", max_age_minutes: int = 5):
        # Expand ~ to home directory
        expanded_path = os.path.expanduser(path) if path.startswith("~") else path
        self.path = Path(expanded_path)
        self.max_age = timedelta(minutes=max_age_minutes)
        self._write_lock = threading.Lock()  # Local thread safety only

        # Ensure parent directory and file exist
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    # ─────────────────────────────────────────────────────────────
    # WRITE METHODS (Slow Brain uses these)
    # ─────────────────────────────────────────────────────────────

    def _append(self, event: TaskEvent) -> None:
        """Atomically append an event to the log."""
        line = event.to_line()

        with self._write_lock, open(self.path, "a") as f:
            # Advisory lock for cross-process safety
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def log_start(self, task_id: str, query: str) -> None:
        """Log that a task has started processing."""
        event = TaskEvent(datetime.now(), "START", task_id, f"query={query}")
        self._append(event)

    def log_ack(self, task_id: str, message: str) -> None:
        """Log fast brain's acknowledgment message."""
        event = TaskEvent(datetime.now(), "ACK", task_id, f"msg={message}")
        self._append(event)

    def log_progress(self, task_id: str, update: str) -> None:
        """Log a progress update (optional, for long tasks)."""
        event = TaskEvent(datetime.now(), "PROGRESS", task_id, update)
        self._append(event)

    def log_done(self, task_id: str, result: str) -> None:
        """Log successful task completion."""
        event = TaskEvent(datetime.now(), "DONE", task_id, f"result={result}")
        self._append(event)

    def log_fail(self, task_id: str, error: str) -> None:
        """Log task failure."""
        event = TaskEvent(datetime.now(), "FAIL", task_id, f"error={error}")
        self._append(event)

    # ─────────────────────────────────────────────────────────────
    # READ METHODS (Fast Brain uses these)
    # ─────────────────────────────────────────────────────────────

    def _read_events(self) -> list[TaskEvent]:
        """Read and parse all events from the log."""
        if not self.path.exists():
            return []

        events = []
        with open(self.path) as f:
            for line in f:
                event = TaskEvent.from_line(line)
                if event:
                    events.append(event)
        return events

    def get_task_status(self, task_id: str) -> TaskStatus:
        """
        Get the current status of a specific task.

        Scans from newest to oldest to find the latest state.
        """
        events = self._read_events()

        status = TaskStatus(task_id=task_id, status="unknown")

        # Process events oldest-to-newest to build up state
        for event in events:
            if event.task_id != task_id:
                continue

            status.last_update = event.timestamp

            if event.event_type == "START":
                status.status = "pending"
                status.started_at = event.timestamp
                # Extract query
                if event.content.startswith("query="):
                    status.query = event.content[6:]

            elif event.event_type == "ACK":
                status.status = "acked"

            elif event.event_type == "PROGRESS":
                status.status = "processing"

            elif event.event_type == "DONE":
                status.status = "done"
                if event.content.startswith("result="):
                    status.result = event.content[7:]

            elif event.event_type == "FAIL":
                status.status = "failed"
                if event.content.startswith("error="):
                    status.error = event.content[6:]

        return status

    def get_all_tasks(self) -> list[TaskStatus]:
        """Get status of all tasks in the log."""
        events = self._read_events()

        # Collect unique task IDs
        task_ids = {e.task_id for e in events}

        return [self.get_task_status(tid) for tid in task_ids]

    def get_pending_tasks(self) -> list[TaskStatus]:
        """Get tasks that are still in progress (not done/failed)."""
        return [t for t in self.get_all_tasks() if t.status in ("pending", "acked", "processing")]

    def get_recent_completions(self, minutes: int = 5) -> list[TaskStatus]:
        """Get tasks completed in the last N minutes."""
        cutoff = datetime.now() - timedelta(minutes=minutes)
        return [
            t
            for t in self.get_all_tasks()
            if t.status == "done" and t.last_update and t.last_update > cutoff
        ]

    # ─────────────────────────────────────────────────────────────
    # For Fast Brain: Render context as readable text
    # ─────────────────────────────────────────────────────────────

    def render_for_fast_brain(self) -> str:
        """
        Render current state as human-readable text for fast brain context.

        Returns a concise summary suitable for injection into LLM context.
        """
        pending = self.get_pending_tasks()
        recent = self.get_recent_completions(minutes=5)

        lines = ["## Current Tasks"]

        if pending:
            lines.append("\n### In Progress")
            for t in pending:
                age = ""
                if t.started_at:
                    elapsed = datetime.now() - t.started_at
                    age = f" ({int(elapsed.total_seconds())}s ago)"
                lines.append(f"- **{t.task_id[:8]}**: {t.query or 'unknown'}{age}")

        if recent:
            lines.append("\n### Recently Completed")
            for t in recent:
                result_preview = (t.result or "")[:100]
                if len(t.result or "") > 100:
                    result_preview += "..."
                lines.append(f"- **{t.task_id[:8]}**: ✅ {result_preview}")

        if not pending and not recent:
            lines.append("\n_No active or recent tasks._")

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────
    # MAINTENANCE (Called periodically)
    # ─────────────────────────────────────────────────────────────

    def cleanup(self, keep_last_n: int = 40) -> int:
        """
        Remove old events, keeping only the most recent N.

        Returns number of events removed.

        Note: This is the only non-append write operation.
        Should be called during quiet periods (e.g., slow brain idle).
        """
        events = self._read_events()

        if len(events) <= keep_last_n:
            return 0

        # Keep only the last N events
        to_keep = events[-keep_last_n:]
        removed = len(events) - len(to_keep)

        # Atomic rewrite via temp file
        tmp_path = self.path.with_suffix(".tmp")

        with self._write_lock:
            with open(tmp_path, "w") as f:
                for event in to_keep:
                    f.write(event.to_line())
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            tmp_path.rename(self.path)

        return removed

    def cleanup_by_age(self) -> int:
        """Remove events older than max_age. Returns count removed."""
        events = self._read_events()
        cutoff = datetime.now() - self.max_age

        to_keep = [e for e in events if e.timestamp > cutoff]
        removed = len(events) - len(to_keep)

        if removed == 0:
            return 0

        # Atomic rewrite
        tmp_path = self.path.with_suffix(".tmp")

        with self._write_lock:
            with open(tmp_path, "w") as f:
                for event in to_keep:
                    f.write(event.to_line())
                f.flush()
                os.fsync(f.fileno())

            tmp_path.rename(self.path)

        return removed


# ─────────────────────────────────────────────────────────────────
# Convenience: Global instance for simple usage
# ─────────────────────────────────────────────────────────────────

_default_ctx: HotContext | None = None


def get_context(path: str = "hot_context.md") -> HotContext:
    """Get or create the default HotContext instance."""
    global _default_ctx
    if _default_ctx is None or str(_default_ctx.path) != path:
        _default_ctx = HotContext(path)
    return _default_ctx


# ─────────────────────────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uuid

    ctx = HotContext("/tmp/test_hot_context.md")

    # Simulate slow brain
    task_id = uuid.uuid4().hex[:8]
    print(f"Creating task {task_id}...")

    ctx.log_start(task_id, "write a cat script with random expressions")
    ctx.log_ack(task_id, "Got it, writing cat script now")

    # Check status mid-flight
    status = ctx.get_task_status(task_id)
    print(f"Status: {status.status}")

    # Complete the task
    ctx.log_done(task_id, "Created random_cat.py with 15 eye expressions and 14 mouth styles")

    # Check final status
    status = ctx.get_task_status(task_id)
    print(f"Final status: {status.status}")
    print(f"Result: {status.result}")

    # Render for fast brain
    print("\n" + ctx.render_for_fast_brain())

    # Show raw log
    print("\n--- Raw Log ---")
    print(Path("/tmp/test_hot_context.md").read_text())
