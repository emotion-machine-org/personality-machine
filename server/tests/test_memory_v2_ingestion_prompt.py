"""Tests for memory v2 ingestion prompt building logic.

Verifies that the guidelines/suffix split works correctly:
- Default guidelines + suffix when no custom prompt
- Custom guidelines + suffix when custom prompt provided
- Placeholders are properly substituted
"""

import pytest

from app.modals.workers.memory_v2_ingest import (
    DEFAULT_INGESTION_GUIDELINES,
    DEFAULT_INGESTION_PROMPT,
    INGESTION_PROMPT_SUFFIX,
    _format_entries_for_llm,
    _sanitize_custom_guidelines,
)


class TestPromptConstants:
    """Test that prompt constants are properly defined."""

    def test_default_guidelines_exists(self):
        """Default guidelines should be a non-empty string."""
        assert isinstance(DEFAULT_INGESTION_GUIDELINES, str)
        assert len(DEFAULT_INGESTION_GUIDELINES) > 100
        assert "memory manager" in DEFAULT_INGESTION_GUIDELINES.lower()

    def test_suffix_has_required_placeholders(self):
        """Suffix must contain all required placeholders."""
        assert "{current_memories}" in INGESTION_PROMPT_SUFFIX
        assert "{user_message}" in INGESTION_PROMPT_SUFFIX
        assert "{assistant_response}" in INGESTION_PROMPT_SUFFIX

    def test_suffix_has_json_format(self):
        """Suffix must specify the JSON response format."""
        assert '"operations"' in INGESTION_PROMPT_SUFFIX
        assert '"action"' in INGESTION_PROMPT_SUFFIX
        assert '"add"' in INGESTION_PROMPT_SUFFIX
        assert '"update"' in INGESTION_PROMPT_SUFFIX
        assert '"delete"' in INGESTION_PROMPT_SUFFIX

    def test_default_prompt_is_guidelines_plus_suffix(self):
        """Default prompt should be the concatenation of guidelines and suffix."""
        expected = DEFAULT_INGESTION_GUIDELINES + INGESTION_PROMPT_SUFFIX
        assert expected == DEFAULT_INGESTION_PROMPT

    def test_guidelines_does_not_contain_placeholders(self):
        """Guidelines should not contain system placeholders - those belong in suffix."""
        assert "{current_memories}" not in DEFAULT_INGESTION_GUIDELINES
        assert "{user_message}" not in DEFAULT_INGESTION_GUIDELINES
        assert "{assistant_response}" not in DEFAULT_INGESTION_GUIDELINES


class TestSanitization:
    """Test the custom guidelines sanitization function."""

    def test_no_braces_unchanged(self):
        """Guidelines without braces should be unchanged."""
        guidelines = "Remember only important facts about the user."
        assert _sanitize_custom_guidelines(guidelines) == guidelines

    def test_single_open_brace_escaped(self):
        """Single open brace should be escaped."""
        assert _sanitize_custom_guidelines("Test {") == "Test {{"

    def test_single_close_brace_escaped(self):
        """Single close brace should be escaped."""
        assert _sanitize_custom_guidelines("Test }") == "Test }}"

    def test_placeholder_pattern_escaped(self):
        """Placeholder-like patterns should be escaped."""
        guidelines = "Remember {name} and {age}."
        expected = "Remember {{name}} and {{age}}."
        assert _sanitize_custom_guidelines(guidelines) == expected

    def test_nested_braces_escaped(self):
        """Nested braces should all be escaped."""
        guidelines = "Format: {{nested}}"
        expected = "Format: {{{{nested}}}}"
        assert _sanitize_custom_guidelines(guidelines) == expected

    def test_empty_string(self):
        """Empty string should return empty string."""
        assert _sanitize_custom_guidelines("") == ""

    def test_json_example_escaped(self):
        """JSON examples in guidelines should be escaped."""
        guidelines = 'Example: {"key": "value"}'
        expected = 'Example: {{"key": "value"}}'
        assert _sanitize_custom_guidelines(guidelines) == expected


class TestPromptBuilding:
    """Test prompt building logic that combines guidelines with suffix."""

    def build_prompt(self, custom_guidelines: str | None) -> str:
        """Replicate the prompt building logic from ingest_memory_v2."""
        if custom_guidelines:
            sanitized = _sanitize_custom_guidelines(custom_guidelines)
            return sanitized + INGESTION_PROMPT_SUFFIX
        return DEFAULT_INGESTION_PROMPT

    def test_no_custom_guidelines_uses_default(self):
        """When no custom guidelines, use full default prompt."""
        prompt = self.build_prompt(None)
        assert prompt == DEFAULT_INGESTION_PROMPT
        assert DEFAULT_INGESTION_GUIDELINES in prompt
        assert INGESTION_PROMPT_SUFFIX in prompt

    def test_empty_string_guidelines_uses_default(self):
        """Empty string should be treated as no custom guidelines."""
        # Empty string is falsy, so should use default
        prompt = self.build_prompt("")
        assert prompt == DEFAULT_INGESTION_PROMPT

    def test_custom_guidelines_appends_suffix(self):
        """Custom guidelines should have suffix appended."""
        custom = "Remember only the user's favorite colors and nothing else."
        prompt = self.build_prompt(custom)

        assert prompt.startswith(custom)
        assert prompt.endswith(INGESTION_PROMPT_SUFFIX.rstrip())
        assert custom + INGESTION_PROMPT_SUFFIX == prompt

    def test_custom_guidelines_has_all_placeholders(self):
        """Custom guidelines + suffix should still have all placeholders."""
        custom = "Custom memory rules here."
        prompt = self.build_prompt(custom)

        assert "{current_memories}" in prompt
        assert "{user_message}" in prompt
        assert "{assistant_response}" in prompt

    def test_custom_guidelines_replaces_default_guidelines(self):
        """Custom guidelines should completely replace default guidelines."""
        custom = "Only remember food preferences."
        prompt = self.build_prompt(custom)

        # Custom is present
        assert custom in prompt
        # Default guidelines are NOT present (they were replaced)
        assert "memory manager for an AI companion" not in prompt


class TestPlaceholderSubstitution:
    """Test that placeholders are correctly substituted."""

    def test_format_entries_empty(self):
        """Empty entries list should return placeholder text."""
        result = _format_entries_for_llm([])
        assert result == "(No memories stored yet)"

    def test_format_entries_single(self):
        """Single entry should be formatted correctly."""
        entries = [{"id": "mem_123", "content": "User likes pizza", "type": "preference"}]
        result = _format_entries_for_llm(entries)
        assert "[mem_123]" in result
        assert "(preference)" in result
        assert "User likes pizza" in result

    def test_format_entries_multiple(self):
        """Multiple entries should be formatted on separate lines."""
        entries = [
            {"id": "mem_1", "content": "User is named Alice", "type": "identity"},
            {"id": "mem_2", "content": "User likes hiking", "type": "preference"},
        ]
        result = _format_entries_for_llm(entries)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "[mem_1]" in lines[0]
        assert "[mem_2]" in lines[1]

    def test_full_prompt_substitution(self):
        """Test complete prompt with all placeholders substituted."""
        custom_guidelines = "Remember important facts only."
        prompt_template = custom_guidelines + INGESTION_PROMPT_SUFFIX

        entries = [{"id": "mem_1", "content": "User is Bob", "type": "identity"}]
        formatted_memories = _format_entries_for_llm(entries)

        prompt = prompt_template.format(
            current_memories=formatted_memories,
            user_message="Hello, my name is Bob!",
            assistant_response="Nice to meet you, Bob!",
        )

        # Check substitutions worked
        assert "{current_memories}" not in prompt
        assert "{user_message}" not in prompt
        assert "{assistant_response}" not in prompt

        # Check content is present
        assert "Remember important facts only." in prompt
        assert "[mem_1]" in prompt
        assert "User is Bob" in prompt
        assert "Hello, my name is Bob!" in prompt
        assert "Nice to meet you, Bob!" in prompt


class TestEdgeCases:
    """Test edge cases and potential issues."""

    def test_guidelines_with_curly_braces_sanitized(self):
        """Custom guidelines with curly braces should be sanitized and work."""
        # Users might accidentally include curly braces in their guidelines
        custom = "Remember facts like {name} and {age}."
        sanitized = _sanitize_custom_guidelines(custom)
        prompt_template = sanitized + INGESTION_PROMPT_SUFFIX

        # Should NOT raise - braces are escaped by sanitization
        prompt = prompt_template.format(
            current_memories="(No memories)",
            user_message="Hi",
            assistant_response="Hello",
        )

        # The escaped braces become literal braces in the final prompt
        assert "{name}" in prompt
        assert "{age}" in prompt

    def test_guidelines_with_json_example(self):
        """JSON examples in guidelines should work after sanitization."""
        custom = 'Store memories as JSON like {"type": "fact", "value": "x"}.'
        sanitized = _sanitize_custom_guidelines(custom)
        prompt_template = sanitized + INGESTION_PROMPT_SUFFIX

        prompt = prompt_template.format(
            current_memories="(No memories)",
            user_message="Hi",
            assistant_response="Hello",
        )

        # JSON example should appear as literal text
        assert '{"type": "fact", "value": "x"}' in prompt

    def test_very_long_custom_guidelines(self):
        """Very long custom guidelines should work."""
        custom = "Rule " * 1000  # ~5000 chars
        prompt = custom + INGESTION_PROMPT_SUFFIX

        assert prompt.startswith(custom)
        assert "{current_memories}" in prompt

    def test_unicode_in_guidelines(self):
        """Unicode characters in guidelines should work."""
        custom = "Remember: 用户喜欢 🍕 and émojis café"
        prompt_template = custom + INGESTION_PROMPT_SUFFIX

        prompt = prompt_template.format(
            current_memories="(No memories)",
            user_message="Hi",
            assistant_response="Hello",
        )

        assert "用户喜欢" in prompt
        assert "🍕" in prompt
        assert "émojis café" in prompt
