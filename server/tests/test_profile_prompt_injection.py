from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.companion import CompanionConfig
from app.repositories import summary_repository
from app.services import message_processor
from app.utils import profile as profile_utils
from app.utils.profile import (
    build_profile_prompt_block,
    normalize_profile_for_runtime,
    prune_profile_contradicting_history,
    resolve_profile_in_prompt_enabled,
)

SYNCED_PROFILE = {
    "user": {
        "first_name": "Yagmur",
        "birthday": "2000-07-02",
    },
    "cycle": {
        "phase_lengths": {
            "luteal": 12,
            "ovulation": 3,
            "follicular": 8,
            "menstruation": 5,
        },
        "last_period_start": "2026-03-06",
        "avg_cycle_length_days": 28,
    },
    "companion": {
        "name": "Luna",
    },
    "recent_symptoms": [
        {
            "date": "2026-03-09",
            "symptoms": [{"name": "Underslept", "intensity": None}],
        }
    ],
}


def _make_companion(*, include_profile_in_prompt: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        config=CompanionConfig(
            system_prompt={"full_system_prompt": "Base prompt"},
            memory={"enabled": False},
            context={"message_limit": 10},
            include_profile_in_prompt=include_profile_in_prompt,
        ),
    )


def _make_relationship(config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        external_user_id="user-123",
        config=config or {},
        profile=SYNCED_PROFILE,
        context_mode=None,
        message_count=0,
        version=1,
        updated_at=datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
    )


def _cycle_profile(last_period_start: str) -> dict:
    return {
        "cycle": {
            "phase_lengths": {
                "menstruation": 5,
                "follicular": 7,
                "ovulation": 3,
                "luteal": 13,
            },
            "last_period_start": last_period_start,
            "avg_cycle_length_days": 28,
        },
        "health_data": {
            "cycle_data": {
                "average_length": 28,
            },
        },
    }


def test_resolve_profile_in_prompt_enabled_prefers_relationship_override() -> None:
    companion_config = CompanionConfig(include_profile_in_prompt=True)

    assert resolve_profile_in_prompt_enabled({}, companion_config) is True
    assert (
        resolve_profile_in_prompt_enabled(
            {"include_profile_in_prompt": False},
            companion_config,
        )
        is False
    )
    assert (
        resolve_profile_in_prompt_enabled(
            {"include_app_state_in_prompt": True},
            CompanionConfig(),
        )
        is True
    )


def test_build_profile_prompt_block_includes_derived_cycle_facts() -> None:
    block = build_profile_prompt_block(
        SYNCED_PROFILE,
        today=date(2026, 3, 9),
        profile_version=7,
    )

    assert block is not None
    assert "User name: Yagmur" in block
    assert "# CYCLE_STATE" in block
    assert '"profile_version": 7' in block
    assert '"source": "server_derived_from_profile"' in block
    assert "Estimated cycle day on 2026-03-09: 4" in block
    assert "Estimated current cycle phase on 2026-03-09: menstruation" in block
    assert "Estimated next period start: 2026-04-03" in block
    assert "Estimated days until next period from 2026-03-09: 25" in block


def test_normalize_profile_for_runtime_backfills_cycle_profile_aliases() -> None:
    normalized = normalize_profile_for_runtime(
        SYNCED_PROFILE,
        profile_schema={
            "core_identity": {"name": ""},
            "health_data": {
                "cycle_data": {
                    "average_length": 0,
                    "current_phase": "",
                    "phase_day": 0,
                    "symptom_patterns": [],
                }
            },
        },
        today=date(2026, 3, 9),
    )

    assert normalized["core_identity"]["name"] == "Yagmur"
    assert normalized["health_data"]["cycle_data"]["average_length"] == 28
    assert normalized["health_data"]["cycle_data"]["current_phase"] == "menstruation"
    assert normalized["health_data"]["cycle_data"]["phase_day"] == 4
    assert normalized["health_data"]["cycle_data"]["next_period_start"] == "2026-04-03"
    assert normalized["health_data"]["cycle_data"]["days_until_next_period"] == 25
    assert normalized["health_data"]["cycle_data"]["phases"]["days_until_next"] == 25
    assert "Underslept" in normalized["health_data"]["cycle_data"]["symptom_patterns"]


def test_build_profile_prompt_block_includes_days_until_next_period() -> None:
    block = build_profile_prompt_block(
        {
            "cycle": {
                "last_period_start": "2026-04-26",
                "avg_cycle_length_days": 28,
            },
            "health_data": {
                "cycle_data": {
                    "next_period_start": "2026-05-08",
                    "days_until_next_period": 3,
                    "phases": {
                        "days_until_next": 3,
                    },
                },
            },
        },
        today=date(2026, 4, 29),
    )

    assert block is not None
    assert "Estimated next period start: 2026-05-08" in block
    assert "Estimated days until next period from 2026-04-29: 9" in block
    assert "Estimated days until next period from 2026-04-29: 3" not in block


def test_normalize_profile_recomputes_stale_days_until_next_period() -> None:
    normalized = normalize_profile_for_runtime(
        {
            "health_data": {
                "cycle_data": {
                    "next_period_start": "2026-05-08",
                    "days_until_next_period": 3,
                    "phases": {
                        "days_until_next": 3,
                    },
                },
            },
        },
        today=date(2026, 4, 29),
    )

    cycle_data = normalized["health_data"]["cycle_data"]
    assert cycle_data["days_until_next_period"] == 9
    assert cycle_data["phases"]["days_until_next"] == 9


def test_prune_profile_contradicting_history_removes_stale_cycle_unknowns() -> None:
    history = [
        {"role": "user", "content": "Whats my current phase"},
        {
            "role": "assistant",
            "content": "I need to know when your last period started.",
        },
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "Sure, I'm here."},
    ]

    pruned, removed = prune_profile_contradicting_history(
        history,
        "Whats my current phase",
        {
            "health_data": {"cycle_data": {"current_phase": "Menstruation", "phase_day": 3}},
            "cycle": {"last_period_start": "2026-04-26"},
        },
    )

    assert removed == 1
    assert [row["content"] for row in pruned] == [
        "Whats my current phase",
        "ok",
        "Sure, I'm here.",
    ]


def test_prune_profile_contradicting_history_removes_stale_cycle_answers_after_edit() -> None:
    history = [
        {"role": "user", "content": "How many days till my next period"},
        {
            "role": "assistant",
            "content": "You've got 9 days until your next period, with May 8th being the start.",
            "metadata": {"profile_version": 4, "contains_cycle_state": True},
        },
        {"role": "user", "content": "Oh I corrected my period dates. Where am I at my cycle rn?"},
        {
            "role": "assistant",
            "content": "You're in the luteal phase, with about 8 days until your next period.",
            "metadata": {"profile_version": 4, "contains_cycle_state": True},
        },
        {"role": "user", "content": "Yaay"},
        {"role": "assistant", "content": "Glad we got that sorted."},
    ]

    pruned, removed = prune_profile_contradicting_history(
        history,
        "Can u check now?",
        {
            "cycle": {
                "phase_lengths": {
                    "menstruation": 5,
                    "follicular": 7,
                    "ovulation": 3,
                    "luteal": 13,
                },
                "last_period_start": "2026-04-15",
                "avg_cycle_length_days": 28,
            },
        },
        profile_version=5,
    )

    assert removed == 2
    assert [row["content"] for row in pruned] == [
        "How many days till my next period",
        "Oh I corrected my period dates. Where am I at my cycle rn?",
        "Yaay",
        "Glad we got that sorted.",
    ]


def test_prune_profile_contradicting_history_keeps_current_version_cycle_answers() -> None:
    history = [
        {"role": "user", "content": "How many days till my next period"},
        {
            "role": "assistant",
            "content": "You've got 13 days until your next period, with May 13th being the start.",
            "metadata": {"profile_version": 5, "contains_cycle_state": True},
        },
    ]

    pruned, removed = prune_profile_contradicting_history(
        history,
        "How many days till my next period?",
        {
            "cycle": {
                "last_period_start": "2026-04-15",
                "avg_cycle_length_days": 28,
            },
        },
        profile_version=5,
    )

    assert removed == 0
    assert pruned == history


@pytest.mark.asyncio
async def test_build_dialogmachine_fast_messages_includes_profile_from_companion_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=True)
    relationship = _make_relationship()

    async def fake_build_effective_system_prompt(conn, *, companion_id, use_cache=True):
        return "Base prompt", "Base prompt"

    async def fake_get_message_history(*args, **kwargs):
        return []

    monkeypatch.setattr(
        message_processor,
        "build_effective_system_prompt",
        fake_build_effective_system_prompt,
    )
    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_message_history",
        fake_get_message_history,
    )
    monkeypatch.setattr(message_processor, "load_dialogmachine_hot_context", lambda _rid: "")

    messages = await message_processor.build_dialogmachine_fast_messages(
        None,
        companion,
        relationship,
        "What is my name?",
    )

    profile_messages = [
        msg
        for msg in messages
        if msg["role"] == "system" and msg["content"].startswith("# PROFILE")
    ]
    assert len(profile_messages) == 1
    assert "User name: Yagmur" in profile_messages[0]["content"]
    assert messages[-2]["content"].startswith("# PROFILE")
    assert messages[-1] == {"role": "user", "content": "What is my name?"}


@pytest.mark.asyncio
async def test_build_dialogmachine_fast_messages_places_profile_after_stale_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=True)
    relationship = _make_relationship()

    async def fake_build_effective_system_prompt(conn, *, companion_id, use_cache=True):
        return "Base prompt", "Base prompt"

    async def fake_get_message_history(*args, **kwargs):
        return [
            {"role": "user", "content": "Whats my current phase"},
            {
                "role": "assistant",
                "content": "I need to know when your last period started.",
            },
            {"role": "user", "content": "What is my current phase?"},
        ]

    monkeypatch.setattr(
        message_processor,
        "build_effective_system_prompt",
        fake_build_effective_system_prompt,
    )
    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_message_history",
        fake_get_message_history,
    )
    monkeypatch.setattr(message_processor, "load_dialogmachine_hot_context", lambda _rid: "")

    messages = await message_processor.build_dialogmachine_fast_messages(
        None,
        companion,
        relationship,
        "What is my current phase?",
    )

    assert messages[-2]["role"] == "system"
    assert messages[-2]["content"].startswith("# PROFILE")
    assert "Estimated current cycle phase" in messages[-2]["content"]
    assert messages[-1] == {"role": "user", "content": "What is my current phase?"}
    assert all("last period started" not in msg["content"] for msg in messages)


@pytest.mark.asyncio
async def test_build_dialogmachine_fast_messages_omits_intro_before_first_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=False)
    companion.config.intro_message.enabled = True
    companion.config.intro_message.text = "Hi, what's up?"
    relationship = _make_relationship()
    current_user_message = "why do I have blood clots ?"

    async def fake_build_effective_system_prompt(conn, *, companion_id, use_cache=True):
        return "Base prompt", "Base prompt"

    async def fake_get_message_history(*args, **kwargs):
        return [
            {"role": "assistant", "content": "Hi, what's up?", "seq": 1},
            {"role": "user", "content": current_user_message, "seq": 2},
        ]

    monkeypatch.setattr(
        message_processor,
        "build_effective_system_prompt",
        fake_build_effective_system_prompt,
    )
    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_message_history",
        fake_get_message_history,
    )
    monkeypatch.setattr(message_processor, "load_dialogmachine_hot_context", lambda _rid: "")

    messages = await message_processor.build_dialogmachine_fast_messages(
        None,
        companion,
        relationship,
        current_user_message,
    )

    assert messages[-1] == {"role": "user", "content": current_user_message}
    assert all(msg["content"] != "Hi, what's up?" for msg in messages)
    assert sum(1 for msg in messages if msg["content"] == current_user_message) == 1


@pytest.mark.asyncio
async def test_build_dialogmachine_fast_messages_handles_customer_calendar_edit_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=True)
    relationship = _make_relationship()
    relationship.profile = _cycle_profile("2026-04-15")
    relationship.version = 3
    relationship.updated_at = datetime(2026, 4, 30, 7, 31, tzinfo=UTC)
    current_user_message = "Oh no i think i didnt save the correct date. Can u check now?"

    # Freeze "today" so the derived cycle dates in the assertions stay stable.
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 4, 30, 7, 31, tzinfo=tz or UTC)

    monkeypatch.setattr(profile_utils, "datetime", _FrozenDatetime)

    async def fake_build_effective_system_prompt(conn, *, companion_id, use_cache=True):
        return "Base prompt", "Base prompt"

    async def fake_get_message_history(*args, **kwargs):
        return [
            {"role": "user", "content": "How many days till my next period"},
            {
                "role": "assistant",
                "content": "You've got 9 days until your next period, with May 8th being the start.",
                "metadata": {"profile_version": 1, "contains_cycle_state": True},
            },
            {"role": "user", "content": "Yaay thats right"},
            {"role": "assistant", "content": "Glad we got that sorted."},
            {
                "role": "user",
                "content": (
                    "Oh i just realized ive made a mistake ab my period dates. "
                    "Where am i at my cycle rn and how many days till my next period?"
                ),
            },
            {
                "role": "assistant",
                "content": "You're in the luteal phase, with about 8 days until your next period.",
                "metadata": {"profile_version": 2, "contains_cycle_state": True},
            },
            {"role": "user", "content": current_user_message},
        ]

    monkeypatch.setattr(
        message_processor,
        "build_effective_system_prompt",
        fake_build_effective_system_prompt,
    )
    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_message_history",
        fake_get_message_history,
    )
    monkeypatch.setattr(message_processor, "load_dialogmachine_hot_context", lambda _rid: "")

    messages = await message_processor.build_dialogmachine_fast_messages(
        None,
        companion,
        relationship,
        current_user_message,
    )

    joined = "\n".join(msg["content"] for msg in messages)
    assert "May 8th" not in joined
    assert "about 8 days" not in joined
    assert "Glad we got that sorted." in joined
    assert "# CYCLE_STATE" in joined
    assert '"profile_version": 3' in joined
    assert '"next_period_start": "2026-05-13"' in joined
    assert messages[-1] == {"role": "user", "content": current_user_message}
    assert sum(1 for msg in messages if msg["content"] == current_user_message) == 1


@pytest.mark.asyncio
async def test_turn_processor_refreshes_profile_before_prompt_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=True)
    stale_relationship = _make_relationship()
    stale_relationship.profile = _cycle_profile("2026-04-10")
    stale_relationship.version = 1
    stale_relationship.updated_at = datetime(2026, 4, 30, 6, 52, tzinfo=UTC)

    fresh_relationship = _make_relationship()
    fresh_relationship.id = stale_relationship.id
    fresh_relationship.profile = _cycle_profile("2026-04-15")
    fresh_relationship.version = 2
    fresh_relationship.updated_at = datetime(2026, 4, 30, 7, 31, tzinfo=UTC)

    captured: dict[str, object] = {}
    saved_messages: list[dict] = []

    async def fake_get_by_id(conn, relationship_id):
        return fresh_relationship

    async def fake_get_next_seq(conn, relationship_id):
        return 1

    async def fake_save_message(conn, **kwargs):
        saved_messages.append(kwargs)
        return {"id": uuid4(), **kwargs}

    async def fake_build_dialogmachine_fast_messages(
        conn,
        companion_arg,
        relationship_arg,
        user_message,
        **kwargs,
    ):
        captured["relationship_version"] = relationship_arg.version
        captured["relationship_profile"] = relationship_arg.profile
        return [{"role": "system", "content": "context"}]

    async def fake_generate_response_non_streaming(*args, **kwargs):
        return "ok"

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(message_processor, "get_next_seq", fake_get_next_seq)
    monkeypatch.setattr(message_processor, "save_message", fake_save_message)
    monkeypatch.setattr(message_processor, "is_dialogmachine_fast_mode", lambda relationship: True)
    monkeypatch.setattr(message_processor, "extract_dialogmachine_text_model", lambda rel: "test")
    monkeypatch.setattr(
        message_processor,
        "build_dialogmachine_fast_messages",
        fake_build_dialogmachine_fast_messages,
    )
    monkeypatch.setattr(
        message_processor, "generate_response_non_streaming", fake_generate_response_non_streaming
    )
    monkeypatch.setattr(message_processor, "get_max_tokens", lambda companion_arg, model: 64)
    monkeypatch.setattr(message_processor, "finalize_turn", noop)
    monkeypatch.setattr(message_processor, "dispatch_memory_v2_ingestion", noop)
    monkeypatch.setattr(message_processor, "dispatch_summarization_if_needed", noop)

    processor = message_processor.TurnProcessor(
        conn=None,
        companion=companion,
        relationship=stale_relationship,
        emitter=None,
    )

    await processor.process_turn_non_streaming(
        message_processor.TurnInput(content="Can u check now?")
    )

    assert captured["relationship_version"] == 2
    assert captured["relationship_profile"] == fresh_relationship.profile
    assert saved_messages[-1]["metadata"]["profile_version"] == 2
    assert saved_messages[-1]["metadata"]["contains_cycle_state"] is True


@pytest.mark.asyncio
async def test_build_legacy_messages_includes_profile_from_companion_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=True)
    relationship = _make_relationship()

    async def fake_build_effective_system_prompt(conn, *, companion_id, use_cache=True):
        return "Base prompt", "Base prompt"

    async def fake_get_message_history(*args, **kwargs):
        return []

    async def fake_build_transient_memory_block(*args, **kwargs):
        return ""

    async def fake_get_latest_summary(conn, relationship_id):
        return None

    monkeypatch.setattr(
        message_processor,
        "build_effective_system_prompt",
        fake_build_effective_system_prompt,
    )
    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_message_history",
        fake_get_message_history,
    )
    monkeypatch.setattr(
        message_processor,
        "build_transient_memory_block",
        fake_build_transient_memory_block,
    )
    monkeypatch.setattr(
        summary_repository.SummaryRepository,
        "get_latest_summary",
        fake_get_latest_summary,
    )

    messages = await message_processor.build_legacy_messages(
        None,
        companion,
        relationship,
        "What is my name?",
    )

    profile_messages = [
        msg
        for msg in messages
        if msg["role"] == "system" and msg["content"].startswith("# PROFILE")
    ]
    assert len(profile_messages) == 1
    assert "User name: Yagmur" in profile_messages[0]["content"]
    assert messages[-2]["content"].startswith("# PROFILE")
    assert messages[-1] == {"role": "user", "content": "What is my name?"}


@pytest.mark.asyncio
async def test_build_legacy_messages_places_profile_after_stale_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    companion = _make_companion(include_profile_in_prompt=True)
    relationship = _make_relationship()

    async def fake_build_effective_system_prompt(conn, *, companion_id, use_cache=True):
        return "Base prompt", "Base prompt"

    async def fake_get_message_history(*args, **kwargs):
        return [
            {"role": "user", "content": "Whats my current phase"},
            {
                "role": "assistant",
                "content": "I need to know when your last period started.",
            },
            {"role": "user", "content": "What is my current phase?"},
        ]

    async def fake_build_transient_memory_block(*args, **kwargs):
        return ""

    async def fake_get_latest_summary(conn, relationship_id):
        return None

    monkeypatch.setattr(
        message_processor,
        "build_effective_system_prompt",
        fake_build_effective_system_prompt,
    )
    monkeypatch.setattr(
        message_processor.RelationshipRepository,
        "get_message_history",
        fake_get_message_history,
    )
    monkeypatch.setattr(
        message_processor,
        "build_transient_memory_block",
        fake_build_transient_memory_block,
    )
    monkeypatch.setattr(
        summary_repository.SummaryRepository,
        "get_latest_summary",
        fake_get_latest_summary,
    )

    messages = await message_processor.build_legacy_messages(
        None,
        companion,
        relationship,
        "What is my current phase?",
    )

    assert messages[-2]["role"] == "system"
    assert messages[-2]["content"].startswith("# PROFILE")
    assert "Estimated current cycle phase" in messages[-2]["content"]
    assert messages[-1] == {"role": "user", "content": "What is my current phase?"}
    assert all("last period started" not in msg["content"] for msg in messages)
