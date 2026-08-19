"""Tests for Memory V2 scratchpad functionality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.context.behavior_context import BehaviorContext, MemoryNamespace
from app.context.memory_v2_layer import MemoryV2Layer
from app.context.schemas import TurnContext, TurnEffect
from app.repositories.memory_v2_repository import MemoryV2Repository
from app.services.cache_manager import cache
from app.services.memory_v2_service import MemoryV2Service

# =============================================================================
# MemoryV2Service Tests
# =============================================================================


class TestMemoryV2Service:
    """Tests for MemoryV2Service utility methods."""

    def test_format_for_prompt_empty(self):
        """Empty entries should return empty string."""
        result = MemoryV2Service.format_for_prompt([])
        assert result == ""

    def test_format_for_prompt_with_type(self):
        """Entries with type should be formatted with type label."""
        entries = [
            {"content": "User's name is Sarah", "type": "identity"},
            {"content": "Prefers direct feedback", "type": "preference"},
        ]
        result = MemoryV2Service.format_for_prompt(entries)
        assert "User's name is Sarah (identity)" in result
        assert "Prefers direct feedback (preference)" in result

    def test_format_for_prompt_without_type(self):
        """Entries without type should be formatted without type label."""
        entries = [
            {"content": "Important fact", "type": None},
        ]
        result = MemoryV2Service.format_for_prompt(entries)
        assert "- Important fact" in result
        assert "()" not in result

    def test_format_entries_for_llm_empty(self):
        """Empty entries should indicate no memories."""
        result = MemoryV2Service.format_entries_for_llm([])
        assert result == "(No memories stored yet)"

    def test_format_entries_for_llm_with_entries(self):
        """Entries should be formatted with IDs for LLM."""
        entries = [
            {"id": "abc-123", "content": "User's name is Sarah", "type": "identity"},
        ]
        result = MemoryV2Service.format_entries_for_llm(entries)
        assert "[abc-123]" in result
        assert "(identity)" in result
        assert "User's name is Sarah" in result


# =============================================================================
# BehaviorContext MemoryNamespace Tests
# =============================================================================


class TestMemoryNamespace:
    """Tests for ctx.memory.* operations in BehaviorContext."""

    def _create_context(self) -> BehaviorContext:
        """Create a test BehaviorContext."""
        turn_context = TurnContext(
            message="test message",
            companion_id=uuid4(),
            relationship_id=uuid4(),
        )
        return BehaviorContext(turn_context=turn_context)

    def test_memory_add_emits_effect(self):
        """ctx.memory.add should emit memory_v2_write effect."""
        ctx = self._create_context()
        ctx.memory.add("User's name is Sarah", type="identity")

        assert len(ctx.effects) == 1
        effect = ctx.effects[0]
        assert effect.effect_type == "memory_v2_write"
        assert effect.payload["operation"] == "add"
        assert effect.payload["content"] == "User's name is Sarah"
        assert effect.payload["type"] == "identity"

    def test_memory_add_empty_content_ignored(self):
        """ctx.memory.add with empty content should be ignored."""
        ctx = self._create_context()
        ctx.memory.add("")
        ctx.memory.add("   ")

        assert len(ctx.effects) == 0

    def test_memory_add_strips_whitespace(self):
        """ctx.memory.add should strip whitespace from content."""
        ctx = self._create_context()
        ctx.memory.add("  User likes coffee  ")

        assert len(ctx.effects) == 1
        assert ctx.effects[0].payload["content"] == "User likes coffee"

    def test_memory_update_emits_effect(self):
        """ctx.memory.update should emit memory_v2_write effect."""
        ctx = self._create_context()
        memory_id = str(uuid4())
        ctx.memory.update(memory_id, "Updated content", type="preference")

        assert len(ctx.effects) == 1
        effect = ctx.effects[0]
        assert effect.effect_type == "memory_v2_write"
        assert effect.payload["operation"] == "update"
        assert effect.payload["memory_id"] == memory_id
        assert effect.payload["content"] == "Updated content"
        assert effect.payload["type"] == "preference"

    def test_memory_update_missing_id_ignored(self):
        """ctx.memory.update without memory_id should be ignored."""
        ctx = self._create_context()
        ctx.memory.update("", "New content")

        assert len(ctx.effects) == 0

    def test_memory_update_empty_content_ignored(self):
        """ctx.memory.update with empty content should be ignored."""
        ctx = self._create_context()
        ctx.memory.update(str(uuid4()), "")

        assert len(ctx.effects) == 0

    def test_memory_delete_emits_effect(self):
        """ctx.memory.delete should emit memory_v2_write effect."""
        ctx = self._create_context()
        memory_id = str(uuid4())
        ctx.memory.delete(memory_id)

        assert len(ctx.effects) == 1
        effect = ctx.effects[0]
        assert effect.effect_type == "memory_v2_write"
        assert effect.payload["operation"] == "delete"
        assert effect.payload["memory_id"] == memory_id

    def test_memory_delete_missing_id_ignored(self):
        """ctx.memory.delete without memory_id should be ignored."""
        ctx = self._create_context()
        ctx.memory.delete("")

        assert len(ctx.effects) == 0


# =============================================================================
# MemoryV2Repository Tests (require DB)
# =============================================================================


@pytest.mark.anyio
@pytest.mark.db
class TestMemoryV2Repository:
    """Database tests for MemoryV2Repository."""

    async def test_create_entry(self, db_conn, test_relationship_id):
        """Test creating a memory entry."""
        entry = await MemoryV2Repository.create_entry(
            db_conn,
            test_relationship_id,
            "User's name is Sarah",
            "identity",
        )
        assert entry["content"] == "User's name is Sarah"
        assert entry["type"] == "identity"
        assert entry["relationship_id"] == test_relationship_id

    async def test_list_entries(self, db_conn, test_relationship_id):
        """Test listing memory entries."""
        # Create entries
        await MemoryV2Repository.create_entry(db_conn, test_relationship_id, "First entry")
        await MemoryV2Repository.create_entry(db_conn, test_relationship_id, "Second entry")

        entries = await MemoryV2Repository.list_entries(db_conn, test_relationship_id)
        assert len(entries) >= 2

    async def test_update_entry(self, db_conn, test_relationship_id):
        """Test updating a memory entry."""
        entry = await MemoryV2Repository.create_entry(
            db_conn, test_relationship_id, "Original content"
        )
        updated = await MemoryV2Repository.update_entry(
            db_conn,
            entry["id"],
            test_relationship_id,
            "Updated content",
            "preference",
        )
        assert updated["content"] == "Updated content"
        assert updated["type"] == "preference"

    async def test_delete_entry(self, db_conn, test_relationship_id):
        """Test deleting a memory entry."""
        entry = await MemoryV2Repository.create_entry(
            db_conn, test_relationship_id, "To be deleted"
        )
        deleted = await MemoryV2Repository.delete_entry(db_conn, entry["id"], test_relationship_id)
        assert deleted is True

        # Verify it's gone
        fetched = await MemoryV2Repository.get_entry(db_conn, entry["id"], test_relationship_id)
        assert fetched is None

    async def test_clear_all(self, db_conn, test_relationship_id):
        """Test clearing all memory entries."""
        # Create entries
        await MemoryV2Repository.create_entry(db_conn, test_relationship_id, "Entry 1")
        await MemoryV2Repository.create_entry(db_conn, test_relationship_id, "Entry 2")

        count = await MemoryV2Repository.clear_all(db_conn, test_relationship_id)
        assert count >= 2

        entries = await MemoryV2Repository.list_entries(db_conn, test_relationship_id)
        assert len(entries) == 0


# =============================================================================
# MemoryV2Layer Caching Tests
# =============================================================================


class TestMemoryV2LayerCaching:
    """Tests for MemoryV2Layer caching behavior."""

    def test_cache_invalidation(self):
        """Test that invalidate_cache removes cached entries."""
        relationship_id = uuid4()
        cache_key = str(relationship_id)

        # Set a value in cache
        cache.set("memory_v2", cache_key, [{"content": "test"}], 30.0)

        # Verify it's cached
        assert cache.get("memory_v2", cache_key) is not None

        # Invalidate
        MemoryV2Layer.invalidate_cache(relationship_id)

        # Verify it's gone
        assert cache.get("memory_v2", cache_key) is None

    @pytest.mark.anyio
    async def test_layer_uses_cache_on_hit(self):
        """Test that MemoryV2Layer returns cached entries without DB call."""
        relationship_id = uuid4()
        cache_key = str(relationship_id)
        cached_entries = [{"id": "abc", "content": "Cached memory", "type": "identity"}]

        # Pre-populate cache
        cache.set("memory_v2", cache_key, cached_entries, 30.0)

        # Create layer with mock connection
        turn_context = TurnContext(
            message="test",
            companion_id=uuid4(),
            relationship_id=relationship_id,
        )
        mock_conn = AsyncMock()
        layer = MemoryV2Layer(turn_context, conn=mock_conn)

        # Run layer
        result = await layer.run()

        # Should NOT have called the database
        mock_conn.fetch.assert_not_called()

        # Should have returned formatted cached content
        assert len(result.messages) == 1
        assert "Cached memory" in result.messages[0]["content"]

        # Cleanup
        cache.delete("memory_v2", cache_key)

    @pytest.mark.anyio
    async def test_layer_fetches_from_db_on_miss(self):
        """Test that MemoryV2Layer fetches from DB when cache misses."""
        relationship_id = uuid4()
        cache_key = str(relationship_id)

        # Ensure cache is empty
        cache.delete("memory_v2", cache_key)

        # Create layer with mock connection
        turn_context = TurnContext(
            message="test",
            companion_id=uuid4(),
            relationship_id=relationship_id,
        )

        db_entries = [
            {
                "id": uuid4(),
                "content": "DB memory",
                "type": "preference",
                "created_at": None,
                "updated_at": None,
            }
        ]

        with patch.object(MemoryV2Repository, "list_entries", new_callable=AsyncMock) as mock_list:
            mock_list.return_value = db_entries
            mock_conn = AsyncMock()
            layer = MemoryV2Layer(turn_context, conn=mock_conn)

            result = await layer.run()

            # Should have called the repository
            mock_list.assert_called_once()

            # Should have returned formatted DB content
            assert len(result.messages) == 1
            assert "DB memory" in result.messages[0]["content"]

        # Cleanup
        cache.delete("memory_v2", cache_key)

    @pytest.mark.anyio
    async def test_layer_skips_without_relationship_id(self):
        """Test that MemoryV2Layer skips when no relationship_id."""
        turn_context = TurnContext(
            message="test",
            companion_id=uuid4(),
            relationship_id=None,  # No relationship
        )
        mock_conn = AsyncMock()
        layer = MemoryV2Layer(turn_context, conn=mock_conn)

        result = await layer.run()

        # Should return empty messages
        assert len(result.messages) == 0
        # Should have gate event indicating skip
        gate_events = [e for e in result.events if "gate" in e.name]
        assert len(gate_events) == 1
        assert gate_events[0].meta["gate_result"]["reason"] == "no_relationship_id"


# =============================================================================
# build_transient_memory_v2_block Tests
# =============================================================================


class TestBuildTransientMemoryV2Block:
    """Tests for build_transient_memory_v2_block function."""

    @pytest.mark.anyio
    async def test_returns_empty_without_external_user_id(self):
        """Test that function returns empty string without external_user_id."""
        from app.services.context_assembly import build_transient_memory_v2_block

        mock_conn = AsyncMock()
        timings = {}

        result = await build_transient_memory_v2_block(
            mock_conn,
            companion_id=uuid4(),
            external_user_id=None,  # No user ID
            timings=timings,
        )

        assert result == ""
        assert timings.get("memory_v2_skip") == "no_external_user_id"

    @pytest.mark.anyio
    async def test_returns_formatted_block_with_entries(self):
        """Test that function returns formatted memory block."""
        from app.repositories.relationship_repository import RelationshipRepository
        from app.services.context_assembly import build_transient_memory_v2_block

        companion_id = uuid4()
        relationship_id = uuid4()
        external_user_id = "user123"

        mock_relationship = MagicMock()
        mock_relationship.id = relationship_id

        db_entries = [{"id": uuid4(), "content": "User loves coffee", "type": "preference"}]

        with patch.object(
            RelationshipRepository, "ensure_exists", new_callable=AsyncMock
        ) as mock_ensure:
            mock_ensure.return_value = (mock_relationship, False)

            with patch.object(
                MemoryV2Repository, "list_entries", new_callable=AsyncMock
            ) as mock_list:
                mock_list.return_value = db_entries

                mock_conn = AsyncMock()
                timings = {}

                result = await build_transient_memory_v2_block(
                    mock_conn,
                    companion_id=companion_id,
                    external_user_id=external_user_id,
                    timings=timings,
                )

                assert "# MEMORY" in result
                assert "User loves coffee" in result
                assert timings.get("memory_v2_entries") == 1

        # Cleanup cache
        cache.delete("memory_v2", f"memory_v2_legacy:{relationship_id}")

    @pytest.mark.anyio
    async def test_returns_guidance_when_no_entries(self):
        """Test that function returns guidance text when no entries (so companion knows it has memory)."""
        from app.repositories.relationship_repository import RelationshipRepository
        from app.services.context_assembly import build_transient_memory_v2_block

        companion_id = uuid4()
        relationship_id = uuid4()
        external_user_id = "user123"

        mock_relationship = MagicMock()
        mock_relationship.id = relationship_id

        with patch.object(
            RelationshipRepository, "ensure_exists", new_callable=AsyncMock
        ) as mock_ensure:
            mock_ensure.return_value = (mock_relationship, False)

            with patch.object(
                MemoryV2Repository, "list_entries", new_callable=AsyncMock
            ) as mock_list:
                mock_list.return_value = []  # No entries

                mock_conn = AsyncMock()
                timings = {}

                result = await build_transient_memory_v2_block(
                    mock_conn,
                    companion_id=companion_id,
                    external_user_id=external_user_id,
                    timings=timings,
                )

                # Now we inject guidance even when no entries, so companion knows it has memory
                assert "# MEMORY" in result
                assert "Stored memories: None yet" in result
                assert timings.get("memory_v2_entries") == 0
