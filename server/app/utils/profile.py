"""Profile utilities for schema merging and prompt injection."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

_CYCLE_PHASE_QUERY_RE = re.compile(
    r"\b(current\s+phase|cycle\s+phase|menstrual\s+phase|what(?:'s|s| is)?\s+my\s+.*phase|which\s+phase)\b",
    re.IGNORECASE,
)
_CYCLE_INFO_QUERY_RE = re.compile(
    r"\b("
    r"current\s+phase|cycle\s+phase|menstrual\s+phase|which\s+phase|where\s+(?:am\s+i|i\s+am).*cycle|"
    r"next\s+period|period\s+(?:date|start|expected)|how\s+many\s+days|days\s+(?:until|till).*period|"
    r"(?:corrected|updated|changed|fixed|recorrected).*(?:period|date)"
    r")\b",
    re.IGNORECASE,
)
_CHECK_NOW_RE = re.compile(r"\b(check|look|see)\s+(?:it\s+)?(?:again|now)\b", re.IGNORECASE)
_STALE_CYCLE_ASSISTANT_RE = re.compile(
    r"(last\s+period|period\s+start|period\s+date|don't\s+have\s+enough|do\s+not\s+have\s+enough|"
    r"need\s+(?:a\s+little\s+)?(?:more\s+)?(?:info|information|date)|when\s+did\s+your\s+last\s+period)",
    re.IGNORECASE,
)
_CYCLE_ANSWER_ASSISTANT_RE = re.compile(
    r"(next\s+period|period\s+is\s+expected|expected\s+to\s+start|days?\s+(?:until|till)|"
    r"\b\d+\s+days?\b|luteal\s+phase|follicular\s+phase|menstrual\s+phase|menstruation\s+phase|"
    r"ovulation\s+phase|pre-period)",
    re.IGNORECASE,
)


def deep_copy_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Deep copy a dict (simple implementation for JSON-like data).

    Args:
        d: Dictionary to copy

    Returns:
        Deep copy of the dictionary
    """
    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = deep_copy_dict(value)
        elif isinstance(value, list):
            result[key] = list(value)
        else:
            result[key] = value
    return result


def deep_merge_with_schema(profile: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Deep merge profile with schema template.

    Merge behavior:
    - Schema fields missing from profile -> added with default values from schema
    - Profile fields not in schema -> preserved (no data loss)
    - Profile values -> always preserved over schema defaults

    This implements the "lazy migration" pattern for schema evolution.

    Args:
        profile: User's profile data
        schema: Schema template with default values

    Returns:
        Merged profile with schema defaults filled in
    """
    if not schema:
        return profile

    result = {}

    # First, apply schema defaults for missing fields
    for key, default_value in schema.items():
        if key in profile:
            # Profile has this key - recurse if both are dicts, else keep profile value
            if isinstance(default_value, dict) and isinstance(profile[key], dict):
                result[key] = deep_merge_with_schema(profile[key], default_value)
            else:
                result[key] = profile[key]
        # Profile missing this key - use schema default (deep copy for nested dicts)
        elif isinstance(default_value, dict):
            result[key] = deep_copy_dict(default_value)
        elif isinstance(default_value, list):
            result[key] = list(default_value)
        else:
            result[key] = default_value

    # Then, preserve any extra fields from profile not in schema
    for key, value in profile.items():
        if key not in result:
            result[key] = value

    return result


def merge_profile_with_schema(
    profile: dict[str, Any] | None,
    schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a profile merged with schema defaults when available."""
    if not isinstance(profile, dict):
        return {}
    if not isinstance(schema, dict) or not schema:
        return profile
    return deep_merge_with_schema(profile, schema)


def prune_profile_contradicting_history(
    history_rows: list[dict[str, Any]],
    user_message: str,
    profile: dict[str, Any] | None,
    *,
    profile_schema: dict[str, Any] | None = None,
    profile_version: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Remove stale assistant turns that contradict current profile facts.

    This is intentionally narrow. It only applies when the current user asks
    for cycle/period status and the profile already has cycle facts. It removes
    prior assistant replies that either lacked period information or answered
    with stale cycle phase / next-period facts.
    """
    if not history_rows or not _is_cycle_info_request(user_message, history_rows):
        return history_rows, 0

    normalized = normalize_profile_for_runtime(profile, profile_schema=profile_schema)
    cycle_data = (
        normalized.get("health_data", {}).get("cycle_data", {})
        if isinstance(normalized.get("health_data"), dict)
        else {}
    )
    cycle = normalized.get("cycle") if isinstance(normalized.get("cycle"), dict) else {}
    has_cycle_answer = bool(
        str(cycle_data.get("current_phase") or "").strip()
        or _to_int(cycle_data.get("phase_day"))
        or str(cycle_data.get("next_period_start") or "").strip()
        or _to_int(cycle_data.get("days_until_next_period"))
        or str(cycle.get("last_period_start") or cycle.get("last_bleeding_date") or "").strip()
    )
    if not has_cycle_answer:
        return history_rows, 0

    pruned: list[dict[str, Any]] = []
    removed = 0
    for row in history_rows:
        role = row.get("role")
        content = str(row.get("content") or "")
        if role == "assistant" and _should_prune_cycle_assistant_row(
            row,
            content,
            profile_version=profile_version,
        ):
            removed += 1
            continue
        pruned.append(row)
    return pruned, removed


def _is_cycle_info_request(user_message: str, history_rows: list[dict[str, Any]]) -> bool:
    if _CYCLE_INFO_QUERY_RE.search(user_message or "") or _CYCLE_PHASE_QUERY_RE.search(
        user_message or ""
    ):
        return True

    if not _CHECK_NOW_RE.search(user_message or ""):
        return False

    recent_user_messages = [
        str(row.get("content") or "") for row in history_rows[-6:] if row.get("role") == "user"
    ]
    return any(_CYCLE_INFO_QUERY_RE.search(content) for content in recent_user_messages)


def _should_prune_cycle_assistant_row(
    row: dict[str, Any],
    content: str,
    *,
    profile_version: int | None,
) -> bool:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    row_profile_version = _to_int(metadata.get("profile_version"))
    if row_profile_version is not None and profile_version is not None:
        if row_profile_version >= profile_version:
            return False
        return bool(
            metadata.get("contains_cycle_state")
            or _CYCLE_ANSWER_ASSISTANT_RE.search(content)
            or _STALE_CYCLE_ASSISTANT_RE.search(content)
        )

    # Fallback for messages created before profile-version metadata existed.
    return bool(
        _STALE_CYCLE_ASSISTANT_RE.search(content) or _CYCLE_ANSWER_ASSISTANT_RE.search(content)
    )


def resolve_profile_in_prompt_enabled(
    relationship_config: dict[str, Any] | None,
    companion_config: Any | None = None,
) -> bool:
    """Resolve profile-in-prompt from relationship override, then companion default."""
    rel_config = relationship_config if isinstance(relationship_config, dict) else {}

    if "include_profile_in_prompt" in rel_config:
        return bool(rel_config.get("include_profile_in_prompt"))
    if "include_app_state_in_prompt" in rel_config:
        return bool(rel_config.get("include_app_state_in_prompt"))

    if isinstance(companion_config, dict):
        if "include_profile_in_prompt" in companion_config:
            return bool(companion_config.get("include_profile_in_prompt"))
        if "include_app_state_in_prompt" in companion_config:
            return bool(companion_config.get("include_app_state_in_prompt"))

    if companion_config is not None:
        if hasattr(companion_config, "include_profile_in_prompt"):
            return bool(companion_config.include_profile_in_prompt)
        if hasattr(companion_config, "include_app_state_in_prompt"):
            return bool(companion_config.include_app_state_in_prompt)

    return False


def build_profile_prompt_block(
    profile: dict[str, Any] | None,
    *,
    profile_schema: dict[str, Any] | None = None,
    today: date | None = None,
    profile_version: int | None = None,
    profile_updated_at: datetime | None = None,
) -> str | None:
    """Format a profile block for prompt injection."""
    normalized_profile = normalize_profile_for_runtime(
        profile,
        profile_schema=profile_schema,
        today=today,
    )
    if not normalized_profile:
        return None

    lines = [
        "# PROFILE",
        "This is the authoritative, up-to-date record of the user's identity and state.",
        "If a fact here conflicts with anything in MEMORY or history, trust PROFILE — MEMORY may be stale.",
        "If a requested personal detail appears here, answer with it instead of saying you do not know.",
    ]

    facts = _build_profile_facts(normalized_profile, today=today)
    if facts:
        lines.append("")
        lines.append("Key facts:")
        lines.extend(facts)

    cycle_state = _build_cycle_state(
        normalized_profile,
        today=today or datetime.now(UTC).date(),
        profile_version=profile_version,
        profile_updated_at=profile_updated_at,
    )
    if cycle_state:
        lines.append("")
        lines.append("# CYCLE_STATE")
        lines.append(
            "Authoritative server-derived cycle state. Use these exact values for cycle/period questions."
        )
        lines.append(json.dumps(cycle_state, ensure_ascii=False, indent=2))

    lines.append("")
    lines.append("Profile JSON snapshot:")
    lines.append(json.dumps(normalized_profile, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def normalize_profile_for_runtime(
    profile: dict[str, Any] | None,
    *,
    profile_schema: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Return a runtime profile with schema defaults and synced-profile aliases."""
    merged_profile = merge_profile_with_schema(profile, profile_schema)
    if not merged_profile:
        return {}

    normalized = deep_copy_dict(merged_profile)
    _apply_synced_profile_aliases(normalized, today=today or datetime.now(UTC).date())
    return normalized


def _build_profile_facts(profile: dict[str, Any], *, today: date | None = None) -> list[str]:
    today = today or datetime.now(UTC).date()
    facts: list[str] = []

    user = profile.get("user")
    if isinstance(user, dict):
        first_name = str(user.get("first_name") or "").strip()
        last_name = str(user.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if full_name:
            facts.append(f"- User name: {full_name}")
        birthday = str(user.get("birthday") or "").strip()
        if birthday:
            facts.append(f"- User birthday: {birthday}")

    companion = profile.get("companion")
    if isinstance(companion, dict):
        companion_name = str(companion.get("name") or "").strip()
        if companion_name:
            facts.append(f"- Companion name: {companion_name}")

    core_identity = profile.get("core_identity")
    if isinstance(core_identity, dict):
        core_name = str(core_identity.get("name") or "").strip()
        if core_name and not any(line.startswith("- User name:") for line in facts):
            facts.append(f"- User name: {core_name}")
        age = _to_int(core_identity.get("age"))
        if age:
            facts.append(f"- User age: {age}")
        pronouns = str(core_identity.get("pronouns") or "").strip()
        if pronouns:
            facts.append(f"- User pronouns: {pronouns}")
        location = str(core_identity.get("location") or "").strip()
        if location:
            facts.append(f"- User location: {location}")

    health_data = profile.get("health_data")
    if isinstance(health_data, dict):
        cycle_data = health_data.get("cycle_data")
        if isinstance(cycle_data, dict):
            current_phase = str(cycle_data.get("current_phase") or "").strip()
            if current_phase:
                facts.append(f"- Current cycle phase: {current_phase}")
            phase_day = _to_int(cycle_data.get("phase_day"))
            if phase_day:
                facts.append(f"- Current cycle day: {phase_day}")

    cycle_summary = _derive_cycle_summary(profile, today=today)
    if cycle_summary:
        last_period_start = cycle_summary.get("last_period_start")
        if last_period_start:
            facts.append(f"- Last period start: {last_period_start}")
        cycle_day = cycle_summary.get("cycle_day")
        if cycle_day:
            facts.append(f"- Estimated cycle day on {today.isoformat()}: {cycle_day}")
        phase = cycle_summary.get("phase")
        if phase:
            facts.append(f"- Estimated current cycle phase on {today.isoformat()}: {phase}")
        next_period_start = cycle_summary.get("next_period_start")
        if next_period_start:
            facts.append(f"- Estimated next period start: {next_period_start}")
        days_until_next_period = cycle_summary.get("days_until_next_period")
        if days_until_next_period is not None:
            facts.append(
                f"- Estimated days until next period from {today.isoformat()}: "
                f"{days_until_next_period}"
            )

    recent_symptoms = profile.get("recent_symptoms")
    if isinstance(recent_symptoms, list):
        symptom_parts: list[str] = []
        for entry in recent_symptoms[-3:]:
            if not isinstance(entry, dict):
                continue
            symptom_date = str(entry.get("date") or "").strip()
            symptoms = entry.get("symptoms")
            if not isinstance(symptoms, list):
                continue
            names = [
                str(item.get("name") or "").strip()
                for item in symptoms
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            if symptom_date and names:
                symptom_parts.append(f"{symptom_date}: {', '.join(names)}")
        if symptom_parts:
            facts.append(f"- Recent symptoms: {'; '.join(symptom_parts)}")

    return facts


def _build_cycle_state(
    profile: dict[str, Any],
    *,
    today: date,
    profile_version: int | None,
    profile_updated_at: datetime | None,
) -> dict[str, Any]:
    cycle_summary = _derive_cycle_summary(profile, today=today)
    health_data = profile.get("health_data") if isinstance(profile.get("health_data"), dict) else {}
    cycle_data = (
        health_data.get("cycle_data") if isinstance(health_data.get("cycle_data"), dict) else {}
    )
    cycle = profile.get("cycle") if isinstance(profile.get("cycle"), dict) else {}

    has_cycle_state = bool(cycle_summary or cycle_data or cycle)
    if not has_cycle_state:
        return {}

    state: dict[str, Any] = {
        "source": "server_derived_from_profile",
        "as_of_date": today.isoformat(),
    }
    if profile_version is not None:
        state["profile_version"] = profile_version
    if profile_updated_at is not None:
        state["profile_updated_at"] = profile_updated_at.isoformat()

    last_period_start = cycle_summary.get("last_period_start") or _first_present(
        cycle.get("last_period_start"),
        cycle.get("last_bleeding_date"),
        cycle_data.get("last_period_start"),
        cycle_data.get("last_bleeding_date"),
    )
    last_bleeding_date = _first_present(
        cycle.get("last_bleeding_date"),
        cycle_data.get("last_bleeding_date"),
    )
    avg_cycle_length = _to_int(
        _first_present(cycle.get("avg_cycle_length_days"), cycle_data.get("average_length"))
    )
    current_phase = cycle_summary.get("phase") or str(cycle_data.get("current_phase") or "").strip()
    phase_day = cycle_summary.get("cycle_day") or _to_int(cycle_data.get("phase_day"))

    optional_fields = {
        "last_period_start": last_period_start,
        "last_bleeding_date": last_bleeding_date,
        "avg_cycle_length_days": avg_cycle_length,
        "current_phase": current_phase,
        "phase_day": phase_day,
        "next_period_start": cycle_summary.get("next_period_start"),
        "days_until_next_period": cycle_summary.get("days_until_next_period"),
    }
    for key, value in optional_fields.items():
        if value is not None and value != "":
            state[key] = value

    return state


def _derive_cycle_summary(profile: dict[str, Any], *, today: date) -> dict[str, Any]:
    raw_cycle = profile.get("cycle")
    raw_health_data = profile.get("health_data")
    cycle = raw_cycle if isinstance(raw_cycle, dict) else {}
    health_data = raw_health_data if isinstance(raw_health_data, dict) else {}
    cycle_data = (
        health_data.get("cycle_data") if isinstance(health_data.get("cycle_data"), dict) else {}
    )
    phases = cycle_data.get("phases") if isinstance(cycle_data.get("phases"), dict) else {}

    last_period_start = _parse_date(
        _first_present(
            cycle.get("last_period_start"),
            cycle.get("last_bleeding_date"),
            cycle_data.get("last_period_start"),
            cycle_data.get("last_bleeding_date"),
        )
    )
    explicit_next_period_start = _parse_date(
        _first_present(
            cycle.get("next_period_start"),
            cycle.get("next_period_date"),
            cycle.get("predicted_next_period_start"),
            cycle.get("predicted_next_period_date"),
            cycle.get("estimated_next_period_start"),
            cycle.get("estimated_next_period_date"),
            cycle_data.get("next_period_start"),
            cycle_data.get("next_period_date"),
            cycle_data.get("predicted_next_period_start"),
            cycle_data.get("predicted_next_period_date"),
            cycle_data.get("estimated_next_period_start"),
            cycle_data.get("estimated_next_period_date"),
        )
    )
    explicit_days_until_next_period = _to_int(
        _first_present(
            cycle.get("days_until_next_period"),
            cycle.get("days_until_next"),
            cycle_data.get("days_until_next_period"),
            cycle_data.get("days_until_next"),
            phases.get("days_until_next"),
        )
    )
    avg_cycle_length = _to_int(
        _first_present(cycle.get("avg_cycle_length_days"), cycle_data.get("average_length"))
    )
    phase_lengths = cycle.get("phase_lengths")
    if not last_period_start:
        summary: dict[str, Any] = {}
        if explicit_next_period_start:
            summary["next_period_start"] = explicit_next_period_start.isoformat()
            summary["days_until_next_period"] = (explicit_next_period_start - today).days
        elif explicit_days_until_next_period is not None:
            summary["days_until_next_period"] = explicit_days_until_next_period
        return summary

    delta_days = (today - last_period_start).days
    if delta_days < 0:
        return {"last_period_start": last_period_start.isoformat()}

    cycle_length = avg_cycle_length or 0
    cycle_day = delta_days + 1
    normalized_day = cycle_day
    next_period_start = explicit_next_period_start

    if cycle_length > 0:
        normalized_day = (delta_days % cycle_length) + 1
        if not next_period_start:
            next_period_start = last_period_start + timedelta(
                days=((delta_days // cycle_length) + 1) * cycle_length
            )

    phase = None
    if isinstance(phase_lengths, dict):
        menstruation = _to_int(phase_lengths.get("menstruation")) or 0
        follicular = _to_int(phase_lengths.get("follicular")) or 0
        ovulation = _to_int(phase_lengths.get("ovulation")) or 0
        luteal = _to_int(phase_lengths.get("luteal")) or 0
        total = menstruation + follicular + ovulation + luteal
        if total > 0:
            if cycle_length <= 0:
                cycle_length = total
                normalized_day = (delta_days % cycle_length) + 1
                if not next_period_start:
                    next_period_start = last_period_start + timedelta(
                        days=((delta_days // cycle_length) + 1) * cycle_length
                    )
            if normalized_day <= menstruation:
                phase = "menstruation"
            elif normalized_day <= menstruation + follicular:
                phase = "follicular"
            elif normalized_day <= menstruation + follicular + ovulation:
                phase = "ovulation"
            else:
                phase = "luteal"

    return {
        "last_period_start": last_period_start.isoformat(),
        "cycle_day": normalized_day,
        "phase": phase,
        "next_period_start": next_period_start.isoformat() if next_period_start else None,
        "days_until_next_period": (
            (next_period_start - today).days
            if next_period_start
            else explicit_days_until_next_period
        ),
    }


def _apply_synced_profile_aliases(profile: dict[str, Any], *, today: date) -> None:
    user = profile.get("user")
    if isinstance(user, dict):
        first_name = str(user.get("first_name") or "").strip()
        last_name = str(user.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first_name, last_name) if part).strip()
        if full_name:
            core_identity = _ensure_nested_dict(profile, "core_identity")
            if not str(core_identity.get("name") or "").strip():
                core_identity["name"] = full_name

    raw_cycle = profile.get("cycle")
    raw_health_data = profile.get("health_data")
    cycle = raw_cycle if isinstance(raw_cycle, dict) else {}
    has_cycle_data = isinstance(raw_health_data, dict) and isinstance(
        raw_health_data.get("cycle_data"), dict
    )
    if cycle or has_cycle_data:
        cycle_data = _ensure_nested_dict(profile, "health_data", "cycle_data")

        avg_cycle_length = _to_int(cycle.get("avg_cycle_length_days"))
        if avg_cycle_length and not _to_int(cycle_data.get("average_length")):
            cycle_data["average_length"] = avg_cycle_length

        cycle_summary = _derive_cycle_summary(profile, today=today)
        phase = str(cycle_summary.get("phase") or "").strip()
        if phase and not str(cycle_data.get("current_phase") or "").strip():
            cycle_data["current_phase"] = phase

        phase_day = cycle_summary.get("cycle_day")
        if phase_day and not _to_int(cycle_data.get("phase_day")):
            cycle_data["phase_day"] = phase_day

        next_period_start = str(cycle_summary.get("next_period_start") or "").strip()
        if next_period_start and not str(cycle_data.get("next_period_start") or "").strip():
            cycle_data["next_period_start"] = next_period_start

        days_until_next_period = cycle_summary.get("days_until_next_period")
        if days_until_next_period is not None:
            phases = cycle_data.get("phases") if isinstance(cycle_data.get("phases"), dict) else {}
            cycle_data["days_until_next_period"] = days_until_next_period
            phases["days_until_next"] = days_until_next_period
            cycle_data["phases"] = phases

        recent_symptoms = profile.get("recent_symptoms")
        if isinstance(recent_symptoms, list):
            symptom_patterns: list[str] = []
            for entry in recent_symptoms:
                if not isinstance(entry, dict):
                    continue
                symptoms = entry.get("symptoms")
                if not isinstance(symptoms, list):
                    continue
                for item in symptoms:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if name and name not in symptom_patterns:
                        symptom_patterns.append(name)
            if symptom_patterns and not cycle_data.get("symptom_patterns"):
                cycle_data["symptom_patterns"] = symptom_patterns


def _parse_date(value: Any) -> date | None:
    if not value:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any | None:
    for value in values:
        if value is None or value == "":
            continue
        return value
    return None


def _ensure_nested_dict(root: dict[str, Any], *keys: str) -> dict[str, Any]:
    current = root
    for key in keys:
        value = current.get(key)
        if not isinstance(value, dict):
            value = {}
            current[key] = value
        current = value
    return current
