"""
Tests for hot_context.py — Append-only event log for Fast/Slow Brain coordination.
"""

import os

# Direct import (avoid package dependencies)
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app" / "routers" / "voice"))
from hot_context import HotContext, TaskEvent, TaskStatus


class TestTaskEvent:
    """Test event serialization/parsing."""

    def test_round_trip(self):
        """Event should serialize and deserialize correctly."""
        event = TaskEvent(
            timestamp=datetime(2026, 2, 5, 23, 4, 41),
            event_type="START",
            task_id="abc123",
            content="query=write a cat script",
        )

        line = event.to_line()
        parsed = TaskEvent.from_line(line)

        assert parsed is not None
        assert parsed.task_id == "abc123"
        assert parsed.event_type == "START"
        assert parsed.content == "query=write a cat script"

    def test_newline_escaping(self):
        """Newlines in content should be escaped."""
        event = TaskEvent(
            timestamp=datetime.now(),
            event_type="DONE",
            task_id="xyz",
            content="result=Line 1\nLine 2\nLine 3",
        )

        line = event.to_line()
        assert "\n" not in line.rstrip("\n")  # No raw newlines except trailing

        parsed = TaskEvent.from_line(line)
        assert parsed is not None
        assert parsed.content == "result=Line 1\nLine 2\nLine 3"


class TestHotContext:
    """Test the main HotContext class."""

    @pytest.fixture
    def ctx(self, tmp_path):
        """Create a HotContext with a temp file."""
        return HotContext(str(tmp_path / "hot_context.md"))

    def test_log_start_creates_event(self, ctx):
        """log_start should create a START event."""
        ctx.log_start("task1", "write a poem")

        status = ctx.get_task_status("task1")
        assert status.status == "pending"
        assert status.query == "write a poem"

    def test_task_lifecycle(self, ctx):
        """Task should progress through states correctly."""
        ctx.log_start("task1", "calculate pi")
        assert ctx.get_task_status("task1").status == "pending"

        ctx.log_ack("task1", "On it!")
        assert ctx.get_task_status("task1").status == "acked"

        ctx.log_progress("task1", "50% done")
        assert ctx.get_task_status("task1").status == "processing"

        ctx.log_done("task1", "3.14159265359")
        status = ctx.get_task_status("task1")
        assert status.status == "done"
        assert status.result == "3.14159265359"

    def test_failed_task(self, ctx):
        """Failed tasks should have error captured."""
        ctx.log_start("task1", "divide by zero")
        ctx.log_fail("task1", "ZeroDivisionError: oops")

        status = ctx.get_task_status("task1")
        assert status.status == "failed"
        assert "ZeroDivisionError" in status.error

    def test_multiple_tasks(self, ctx):
        """Multiple tasks should be tracked independently."""
        ctx.log_start("task1", "job 1")
        ctx.log_start("task2", "job 2")
        ctx.log_done("task1", "done 1")

        assert ctx.get_task_status("task1").status == "done"
        assert ctx.get_task_status("task2").status == "pending"

    def test_get_pending_tasks(self, ctx):
        """get_pending_tasks should return only incomplete tasks."""
        ctx.log_start("task1", "pending")
        ctx.log_start("task2", "done soon")
        ctx.log_done("task2", "finished")
        ctx.log_start("task3", "failed soon")
        ctx.log_fail("task3", "error")

        pending = ctx.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0].task_id == "task1"

    def test_render_for_fast_brain(self, ctx):
        """render_for_fast_brain should produce readable output."""
        ctx.log_start("abc12345", "write a cat script")
        ctx.log_start("def67890", "check weather")
        ctx.log_done("def67890", "Sunny, 72°F")

        output = ctx.render_for_fast_brain()

        assert "## Current Tasks" in output
        assert "abc12345" in output
        assert "write a cat script" in output
        assert "Sunny" in output


class TestHotContextCleanup:
    """Test cleanup/maintenance methods."""

    @pytest.fixture
    def ctx(self, tmp_path):
        return HotContext(str(tmp_path / "hot_context.md"))

    def test_cleanup_by_count(self, ctx):
        """cleanup should keep only last N events."""
        for i in range(100):
            ctx.log_start(f"task{i}", f"job {i}")

        removed = ctx.cleanup(keep_last_n=40)

        assert removed == 60
        events = ctx._read_events()
        assert len(events) == 40
        # Should keep the most recent
        assert events[-1].task_id == "task99"


class TestHotContextConcurrency:
    """Test thread safety."""

    def test_concurrent_writes(self, tmp_path):
        """Multiple threads writing should not corrupt the log."""
        ctx = HotContext(str(tmp_path / "hot_context.md"))
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    ctx.log_start(f"t{thread_id}-{i}", f"job {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        events = ctx._read_events()
        assert len(events) == 250  # 5 threads × 50 events

    def test_concurrent_read_write(self, tmp_path):
        """Reads during writes should not crash."""
        ctx = HotContext(str(tmp_path / "hot_context.md"))
        stop = threading.Event()
        errors = []

        def writer():
            try:
                i = 0
                while not stop.is_set():
                    ctx.log_start(f"task{i}", "job")
                    i += 1
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    ctx.get_all_tasks()
                    ctx.render_for_fast_brain()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()

        time.sleep(0.5)  # Let them run
        stop.set()

        for t in threads:
            t.join()

        assert len(errors) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
