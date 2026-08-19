from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ASSET_ROOT = (Path(__file__).resolve().parents[1] / "data" / "knowledge").resolve()
_KNOWN_INGESTION_KEYS: dict[str, tuple[str, str | None]] = {
    "cycle_companion_reference_v1": (
        "cycle_companion_reference_v1.json",
        "Cycle Companion Reference v1",
    ),
}


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _render_json(value: Any, lines: list[str], indent: int = 0) -> None:
    prefix = "  " * indent
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_scalar(child):
                lines.append(f"{prefix}- {key}: {child}")
            else:
                lines.append(f"{prefix}- {key}:")
                _render_json(child, lines, indent + 1)
    elif isinstance(value, list):
        for child in value:
            if _is_scalar(child):
                lines.append(f"{prefix}- {child}")
            else:
                _render_json(child, lines, indent + 1)
    else:
        lines.append(f"{prefix}{value}")


def _json_payload_to_text(payload: Any, label: str | None) -> str:
    lines: list[str] = []
    if label:
        lines.append(f"# {label}")
        lines.append("")
    _render_json(payload, lines)
    return "\n".join(line for line in lines if line).strip()


def load_known_ingestion_asset(key: str) -> str | None:
    entry = _KNOWN_INGESTION_KEYS.get(key)
    if not entry:
        return None

    filename, label = entry
    asset_path = _ASSET_ROOT / filename
    try:
        raw = asset_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Ingestion asset %s missing at %s", key, asset_path)
        return None

    if asset_path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ingestion asset %s contains invalid JSON", key)
            return None
        return _json_payload_to_text(payload, label)

    return raw.strip()


__all__ = ["load_known_ingestion_asset"]
