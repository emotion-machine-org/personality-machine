from __future__ import annotations

import importlib.util
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Load voice_workspace directly to avoid importing the full FastAPI package tree.
_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "routers" / "voice" / "voice_workspace.py"
)
_SPEC = importlib.util.spec_from_file_location("voice_workspace", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Failed to load voice_workspace module for tests")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *args, **kwargs: None))
_botocore_exceptions = types.SimpleNamespace(
    BotoCoreError=Exception,
    ClientError=Exception,
)
sys.modules.setdefault("botocore.exceptions", _botocore_exceptions)
_SPEC.loader.exec_module(_MODULE)  # type: ignore[assignment]

HotContextS3 = _MODULE.HotContextS3
_MODULE._S3_BUCKET = "test-bucket"


class _StubWorkspace:
    def __init__(self, content: str):
        self._content = content

    def read(self, path: str):
        return self._content


def _line(event: str, task_id: str, ts: datetime, data: str) -> str:
    return f"{event}|{task_id}|{ts.isoformat()}|{data}\n"


def test_render_filters_by_recency() -> None:
    now = datetime.now(UTC)
    old_ts = now - timedelta(hours=8)
    new_ts = now - timedelta(hours=2)

    content = (
        _line("task_started", "old1", old_ts, "Old query")
        + _line("task_done", "old1", old_ts + timedelta(minutes=1), "Old done")
        + _line("task_started", "new1", new_ts, "New query")
        + _line("task_done", "new1", new_ts + timedelta(minutes=1), "New done")
    )

    ctx = HotContextS3("rel-test")
    ctx.workspace = _StubWorkspace(content)

    rendered = ctx.render()

    assert "Voice Event Context" in rendered
    assert "Recent Events" in rendered
    assert "New done" in rendered
    assert "Old done" not in rendered

    spoken = ctx.render_spoken()
    assert "Recent events:" in spoken
    assert "New done" in spoken
    assert "Old done" not in spoken


def test_render_when_no_recent_events() -> None:
    now = datetime.now(UTC)
    old_ts = now - timedelta(hours=12)

    content = _line("task_done", "old2", old_ts, "Very old")

    ctx = HotContextS3("rel-test")
    ctx.workspace = _StubWorkspace(content)

    rendered = ctx.render()
    assert "No recent events in the last 6 hours" in rendered

    spoken = ctx.render_spoken()
    assert "No recent events in the last 6 hours" in spoken


if __name__ == "__main__":
    test_render_filters_by_recency()
    test_render_when_no_recent_events()
    print("ok")
