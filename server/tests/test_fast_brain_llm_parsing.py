"""Unit tests for Fast Brain Pipecat tool-calling setup."""

from app.routers.voice.fast_brain_llm import (
    TOOL_DELEGATE_TO_OPENCLAW,
    TOOL_END_CALL,
    FastBrainConfig,
    build_fast_brain_tools_schema,
)


def _tool_names(schema) -> list[str]:
    if not schema:
        return []
    return [tool.name for tool in schema.standard_tools]


def test_tools_schema_includes_delegate_tool_when_enabled() -> None:
    config = FastBrainConfig(allow_delegation=True, allow_call_termination=False)
    schema = build_fast_brain_tools_schema(config)

    assert schema is not None
    assert _tool_names(schema) == [TOOL_DELEGATE_TO_OPENCLAW]


def test_tools_schema_includes_end_call_when_enabled() -> None:
    config = FastBrainConfig(allow_delegation=False, allow_call_termination=True)
    schema = build_fast_brain_tools_schema(config)

    assert schema is not None
    assert _tool_names(schema) == [TOOL_END_CALL]


def test_tools_schema_includes_both_when_both_enabled() -> None:
    config = FastBrainConfig(allow_delegation=True, allow_call_termination=True)
    schema = build_fast_brain_tools_schema(config)

    assert schema is not None
    assert _tool_names(schema) == [TOOL_DELEGATE_TO_OPENCLAW, TOOL_END_CALL]


def test_tools_schema_returns_none_when_no_tools_enabled() -> None:
    config = FastBrainConfig(allow_delegation=False, allow_call_termination=False)
    schema = build_fast_brain_tools_schema(config)

    assert schema is None
