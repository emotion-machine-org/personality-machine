import json
import logging
import os
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Dict, List
from uuid import UUID

import asyncpg
from fastapi import HTTPException, status

from ..models.companion import (
    CompanionConfig,
    CompanionCreate,
    CompanionDetail,
    CompanionSummary,
    CompanionUpdate,
    CompanionVersion,
    CompanionVersionSummary,
    parse_companion_config_payload,
)
from ..services.cache_manager import cache, ttl_from_env
from .share import CompanionShareRepository

logger = logging.getLogger(__name__)

# Cache TTL for companion details (5 minutes - companion config rarely changes)
_COMPANION_CACHE_TTL_S = ttl_from_env("COMPANION_CACHE_TTL_S", 300.0)
_ACTION_LAYER_ALIASES = {"actions", "behaviors"}
_DEFAULT_ACTION_LAYER_ATTACHMENT: Dict[str, Any] = {
    "key": "actions",
    "category": "actions",
    "enabled": True,
    "priority": 30,
    "params": {},
    "timeout_ms": None,
    "reserved_tokens": None,
    "depends_on": [],
}


def _normalize_metadata(record: Dict[str, Any], key: str = "metadata") -> Dict[str, Any]:
    meta = record.get(key)
    if isinstance(meta, str):
        try:
            record[key] = json.loads(meta)
        except json.JSONDecodeError:
            record[key] = {}
    elif meta is None:
        record[key] = {}
    return record


class CompanionRepository:
    """Repository for companion data operations"""

    @staticmethod
    def _format_relative_time(dt: datetime) -> str:
        """Format datetime as relative time (e.g., '2 days ago')"""
        now = datetime.now(UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        diff = now - dt
        days = diff.days
        hours = diff.seconds // 3600
        minutes = (diff.seconds // 60) % 60

        if days > 0:
            return f"{days} day{'s' if days != 1 else ''} ago"
        elif hours > 0:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif minutes > 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            return "Just now"

    @staticmethod
    async def get_companions_by_user(
        conn: asyncpg.Connection, user_id: UUID
    ) -> List[CompanionSummary]:
        """Get all companions belonging to a user"""
        query = """
            SELECT c.id, c.name, c.project_id,
                   COALESCE(MAX(cv.created_at), c.created_at) as last_updated
            FROM companions c
            LEFT JOIN companion_versions cv ON c.id = cv.companion_id
            WHERE c.owner_id = $1
            GROUP BY c.id, c.name, c.project_id, c.created_at
            ORDER BY last_updated DESC
        """

        rows = await conn.fetch(query, user_id)
        return [
            CompanionSummary(
                id=row["id"],
                name=row["name"],
                project_id=row["project_id"],
                last_updated=CompanionRepository._format_relative_time(row["last_updated"]),
            )
            for row in rows
        ]

    @staticmethod
    def _companion_cache_key(companion_id: UUID) -> str:
        """Build cache key for companion lookup."""
        return str(companion_id)

    @staticmethod
    async def get_companion_by_id_no_auth(
        conn: asyncpg.Connection, companion_id: UUID, *, use_cache: bool = True
    ) -> CompanionDetail | None:
        """Get companion with full configuration (no user authorization check).

        Uses caching to avoid repeated DB queries for the same companion.
        Cached for 5 minutes by default.
        """
        cache_key = CompanionRepository._companion_cache_key(companion_id)

        # Check cache first
        if use_cache:
            cached: CompanionDetail | None = cache.get("companion_detail", cache_key)
            if cached is not None:
                return cached

        # Cache miss - fetch from database
        # Get basic companion info
        companion_query = """
            SELECT id, owner_id, project_id, name, description, created_at, metadata
            FROM companions
            WHERE id = $1
        """
        companion_row = await conn.fetchrow(companion_query, companion_id)

        if not companion_row:
            return None
        companion_dict = _normalize_metadata(dict(companion_row))

        # Get latest version with configuration
        version_query = """
            SELECT id, companion_id, version_number, config, system_prompt, voice_id,
                   memory_enabled, status, created_at
            FROM companion_versions
            WHERE companion_id = $1
            ORDER BY version_number DESC, created_at DESC
            LIMIT 1
        """
        version_row = await conn.fetchrow(version_query, companion_id)

        # Parse config JSON to get configuration (fallback to system_prompt for legacy)
        config = parse_companion_config_payload(
            version_row["config"]
            if version_row and version_row["config"]
            else (version_row["system_prompt"] if version_row else None)
        )

        companion = CompanionDetail(
            id=companion_row["id"],
            owner_id=companion_row["owner_id"],
            project_id=companion_row["project_id"],
            name=companion_row["name"],
            description=companion_row["description"],
            created_at=companion_row["created_at"],
            metadata=companion_dict.get("metadata", {}),
            config=config,
            current_version=CompanionVersion(**dict(version_row)) if version_row else None,
        )

        # Cache the result
        if use_cache:
            cache.set("companion_detail", cache_key, companion, _COMPANION_CACHE_TTL_S)

        return companion

    @staticmethod
    def invalidate_companion_cache(companion_id: UUID) -> None:
        """Invalidate cached companion data.

        Call this when a companion or its config is updated.
        """
        cache.delete("companion_detail", CompanionRepository._companion_cache_key(companion_id))

    @staticmethod
    def _is_actions_layer_attachment(layer: Dict[str, Any]) -> bool:
        key = str(layer.get("key") or "").strip().lower()
        category = str(layer.get("category") or "").strip().lower()
        return key in _ACTION_LAYER_ALIASES or category in _ACTION_LAYER_ALIASES

    @staticmethod
    async def ensure_actions_layer_state(
        conn: asyncpg.Connection,
        companion_id: UUID,
        *,
        enabled: bool = True,
    ) -> bool:
        """Ensure latest companion config has an actions layer in the desired state."""
        version_row = await conn.fetchrow(
            """
            SELECT id, config
            FROM companion_versions
            WHERE companion_id = $1
            ORDER BY version_number DESC, created_at DESC
            LIMIT 1
            """,
            companion_id,
        )
        if not version_row:
            return False

        raw_config = version_row["config"] or {}
        if isinstance(raw_config, str):
            try:
                raw_config = json.loads(raw_config)
            except json.JSONDecodeError:
                raw_config = {}
        if not isinstance(raw_config, dict):
            raw_config = {}

        config = deepcopy(raw_config)
        existing_layers = config.get("layers")
        layers: List[Dict[str, Any]] = []
        if isinstance(existing_layers, list):
            layers = [deepcopy(layer) for layer in existing_layers if isinstance(layer, dict)]

        changed = False
        found_actions_layer = False

        for layer in layers:
            if not CompanionRepository._is_actions_layer_attachment(layer):
                continue

            found_actions_layer = True
            if layer.get("key") != "actions":
                layer["key"] = "actions"
                changed = True
            if layer.get("category") != "actions":
                layer["category"] = "actions"
                changed = True
            if bool(layer.get("enabled", True)) != enabled:
                layer["enabled"] = enabled
                changed = True

        if not found_actions_layer and enabled:
            layers.append(deepcopy(_DEFAULT_ACTION_LAYER_ATTACHMENT))
            changed = True

        if not changed:
            return False

        config["layers"] = layers
        await conn.execute(
            """
            UPDATE companion_versions
            SET config = $2
            WHERE id = $1
            """,
            version_row["id"],
            config,
        )

        CompanionRepository.invalidate_companion_cache(companion_id)
        return True

    @staticmethod
    async def exists_companion_owned(
        conn: asyncpg.Connection, companion_id: UUID, user_id: UUID
    ) -> bool:
        row = await conn.fetchrow(
            "SELECT 1 FROM companions WHERE id = $1 AND owner_id = $2",
            companion_id,
            user_id,
        )
        return bool(row)

    @staticmethod
    async def list_companion_versions(
        conn: asyncpg.Connection,
        companion_id: UUID,
        user_id: UUID,
    ) -> List[CompanionVersionSummary] | None:
        """Return companion version summaries for a user-owned companion."""

        query = """
            SELECT cv.id, cv.version_number, cv.config, cv.system_prompt, cv.created_at
            FROM companion_versions cv
            INNER JOIN companions c ON c.id = cv.companion_id
            WHERE cv.companion_id = $1 AND c.owner_id = $2
            ORDER BY cv.version_number DESC, cv.created_at DESC
        """

        rows = await conn.fetch(query, companion_id, user_id)

        if not rows:
            # Distinguish between "no versions" and "no access"
            owns = await CompanionRepository.exists_companion_owned(conn, companion_id, user_id)
            if not owns:
                return None
            return []

        summaries: List[CompanionVersionSummary] = []
        for row in rows:
            # Prefer config column, fallback to system_prompt for legacy data
            config_payload = row["config"] if row["config"] else row["system_prompt"]
            summaries.append(
                CompanionVersionSummary(
                    id=row["id"],
                    version_number=row["version_number"],
                    created_at=row["created_at"],
                    config=parse_companion_config_payload(config_payload),
                )
            )
        return summaries

    @staticmethod
    async def get_companion_version_config(
        conn: asyncpg.Connection,
        companion_id: UUID,
        version_id: UUID,
        user_id: UUID,
    ) -> CompanionConfig | None:
        """Return a sanitized configuration snapshot for a specific version."""

        query = """
            SELECT cv.config, cv.system_prompt
            FROM companion_versions cv
            INNER JOIN companions c ON c.id = cv.companion_id
            WHERE cv.companion_id = $1 AND cv.id = $2 AND c.owner_id = $3
        """

        row = await conn.fetchrow(query, companion_id, version_id, user_id)
        if not row:
            return None

        # Prefer config column, fallback to system_prompt for legacy data
        config_payload = row["config"] if row["config"] else row["system_prompt"]
        return parse_companion_config_payload(config_payload)

    @staticmethod
    async def get_vector_store_id(
        conn: asyncpg.Connection,
        companion_id: UUID,
    ) -> str | None:
        row = await conn.fetchrow(
            "SELECT metadata FROM companions WHERE id = $1",
            companion_id,
        )
        if not row:
            return None
        meta = _normalize_metadata(dict(row)).get("metadata", {})
        return meta.get("vector_store_id")

    @staticmethod
    async def set_vector_store_id(
        conn: asyncpg.Connection,
        companion_id: UUID,
        vector_store_id: str,
    ) -> None:
        await conn.execute(
            """
            UPDATE companions
            SET metadata = metadata || jsonb_build_object('vector_store_id', $2::text)
            WHERE id = $1
            """,
            companion_id,
            vector_store_id,
        )
        # Invalidate cache since metadata changed
        CompanionRepository.invalidate_companion_cache(companion_id)

    @staticmethod
    async def get_companion_by_id(
        conn: asyncpg.Connection, companion_id: UUID, user_id: UUID
    ) -> CompanionDetail | None:
        """Get companion with full configuration.

        Uses cached get_companion_by_id_no_auth and verifies ownership in Python.
        This allows reusing the same cache for both authenticated and unauthenticated paths.
        """
        # Use cached lookup, then verify ownership
        companion = await CompanionRepository.get_companion_by_id_no_auth(conn, companion_id)
        if not companion:
            return None
        if companion.owner_id != user_id:
            return None

        # Verbose debug logging (requires extra DB query, only when explicitly enabled)
        if os.getenv("COMPANION_VERBOSE_DEBUG", "false").lower() in ("1", "true", "yes", "on"):
            all_versions = await conn.fetch(
                "SELECT version_number, status, created_at FROM companion_versions WHERE companion_id = $1 ORDER BY version_number DESC",
                companion_id,
            )
            logger.info(
                f"[DEBUG] All versions for companion {companion_id}: {[(v['version_number'], v['status'], v['created_at']) for v in all_versions]}"
            )
            version = companion.current_version
            logger.info(
                f"[DEBUG] Retrieved companion {companion_id} - latest version: {version.version_number if version else 'None'}, status: {version.status if version else 'None'}"
            )
            if companion.config:
                config_preview = (
                    companion.config.system_prompt.full_system_prompt[:100]
                    if companion.config.system_prompt
                    and companion.config.system_prompt.full_system_prompt
                    else ""
                )
                if config_preview:
                    logger.info(f"[DEBUG] Config preview: {config_preview}...")

        return companion

    @staticmethod
    async def create_companion(
        conn: asyncpg.Connection,
        user_id: UUID,
        companion_data: CompanionCreate,
        project_id: UUID | None = None,
    ) -> CompanionDetail:
        """Create a new companion with initial version and deployment"""
        if project_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_id is required to create a companion",
            )
        async with conn.transaction():
            # Create companion
            companion_query = """
                INSERT INTO companions (owner_id, project_id, name, description)
                VALUES ($1, $2, $3, $4)
                RETURNING id, owner_id, project_id, name, description, created_at
            """

            try:
                companion_row = await conn.fetchrow(
                    companion_query,
                    user_id,
                    project_id,
                    companion_data.name,
                    companion_data.description,
                )

                companion_id = companion_row["id"]

                # Create initial version with configuration
                # Pass dict directly - asyncpg's JSONB codec handles serialization
                config_dict = companion_data.config.model_dump() if companion_data.config else {}

                # Sync layers array with memory.enabled and knowledge.enabled state
                if config_dict:
                    memory_enabled = config_dict.get("memory", {}).get("enabled", False)
                    knowledge_enabled = config_dict.get("knowledge", {}).get("enabled", False)
                    layers = config_dict.get("layers", [])

                    # Find existing memory layer or create one if memory is enabled
                    memory_layer_found = False
                    for layer in layers:
                        if layer.get("key") == "memory" and layer.get("category") == "memory":
                            layer["enabled"] = memory_enabled
                            memory_layer_found = True
                            break

                    # If memory is enabled but no memory layer exists, create one
                    if memory_enabled and not memory_layer_found:
                        layers.append(
                            {
                                "key": "memory",
                                "category": "memory",
                                "enabled": True,
                                "priority": 50,
                                "params": {},
                                "timeout_ms": None,
                                "reserved_tokens": None,
                                "depends_on": [],
                            }
                        )

                    # Find existing knowledge_base layer or create one if knowledge is enabled
                    knowledge_layer_found = False
                    for layer in layers:
                        if (
                            layer.get("key") == "knowledge_base"
                            and layer.get("category") == "knowledge_base"
                        ):
                            layer["enabled"] = knowledge_enabled
                            knowledge_layer_found = True
                            break

                    # If knowledge is enabled but no knowledge_base layer exists, create one
                    if knowledge_enabled and not knowledge_layer_found:
                        layers.append(
                            {
                                "key": "knowledge_base",
                                "category": "knowledge_base",
                                "enabled": True,
                                "priority": 40,
                                "params": {},
                                "timeout_ms": None,
                                "reserved_tokens": None,
                                "depends_on": [],
                            }
                        )

                    config_dict["layers"] = layers

                version_query = """
                    INSERT INTO companion_versions (companion_id, config, memory_enabled, status)
                    VALUES ($1, $2, $3, 'DRAFT')
                    RETURNING id, companion_id, version_number, config, voice_id,
                              memory_enabled, status, created_at
                """

                version_row = await conn.fetchrow(
                    version_query,
                    companion_id,
                    config_dict,
                    companion_data.config.memory.enabled if companion_data.config else False,
                )

                # Use the config from version_row which has layers synced
                version_config = parse_companion_config_payload(version_row["config"])
                return CompanionDetail(
                    id=companion_row["id"],
                    owner_id=companion_row["owner_id"],
                    project_id=companion_row["project_id"],
                    name=companion_row["name"],
                    description=companion_row["description"],
                    created_at=companion_row["created_at"],
                    config=version_config,
                    current_version=CompanionVersion(**dict(version_row)),
                )

            except Exception:
                logger.exception("Companion creation failed")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create companion",
                )

    @staticmethod
    async def update_companion(
        conn: asyncpg.Connection, companion_id: UUID, user_id: UUID, updates: CompanionUpdate
    ) -> CompanionDetail | None:
        """Update companion (creates new version if config changes)"""
        async with conn.transaction():
            # Check if companion exists and belongs to user
            existing = await CompanionRepository.get_companion_by_id(conn, companion_id, user_id)
            if not existing:
                return None

            previous_name: str | None = existing.name if updates.name is not None else None

            # Update basic companion info if provided
            if updates.name is not None or updates.description is not None:
                update_fields = []
                values = []
                param_count = 1

                if updates.name is not None:
                    update_fields.append(f"name = ${param_count}")
                    values.append(updates.name)
                    param_count += 1

                if updates.description is not None:
                    update_fields.append(f"description = ${param_count}")
                    values.append(updates.description)
                    param_count += 1

                update_query = f"""
                    UPDATE companions
                    SET {", ".join(update_fields)}
                    WHERE id = ${param_count} AND owner_id = ${param_count + 1}
                """
                values.extend([companion_id, user_id])

                await conn.execute(update_query, *values)

            # Keep the share display name aligned with the companion unless a manual override exists.
            if updates.name is not None:
                share = await CompanionShareRepository.get_for_companion(conn, companion_id)
                if share:
                    should_overwrite = share.display_name is None or (
                        previous_name is not None and share.display_name == previous_name
                    )
                    if should_overwrite and updates.name != share.display_name:
                        await CompanionShareRepository.update(
                            conn,
                            share.id,
                            display_name=updates.name,
                        )

            # Create new version if config is provided
            if updates.config is not None:
                # Deep-merge incoming partial config onto existing to avoid wiping fields
                def _merge_dict(base: dict, patch: dict) -> dict:
                    for key, value in patch.items():
                        if isinstance(value, dict) and isinstance(base.get(key), dict):
                            base[key] = _merge_dict(dict(base[key]), value)
                        else:
                            base[key] = value
                    return base

                incoming_patch = updates.config.model_dump(exclude_unset=True)
                merged_config_dict = _merge_dict(existing.config.model_dump(), incoming_patch)

                # Sync layers array with memory.enabled and knowledge.enabled state
                # This ensures the layer enabled flags match their respective config flags
                memory_enabled = merged_config_dict.get("memory", {}).get("enabled", False)
                knowledge_enabled = merged_config_dict.get("knowledge", {}).get("enabled", False)
                layers = merged_config_dict.get("layers", [])

                # Find existing memory layer or create one if memory is enabled
                memory_layer_found = False
                for layer in layers:
                    if layer.get("key") == "memory" and layer.get("category") == "memory":
                        layer["enabled"] = memory_enabled
                        memory_layer_found = True
                        break

                # If memory is enabled but no memory layer exists, create one
                if memory_enabled and not memory_layer_found:
                    layers.append(
                        {
                            "key": "memory",
                            "category": "memory",
                            "enabled": True,
                            "priority": 50,
                            "params": {},
                            "timeout_ms": None,
                            "reserved_tokens": None,
                            "depends_on": [],
                        }
                    )

                # Find existing knowledge_base layer or create one if knowledge is enabled
                knowledge_layer_found = False
                for layer in layers:
                    if (
                        layer.get("key") == "knowledge_base"
                        and layer.get("category") == "knowledge_base"
                    ):
                        layer["enabled"] = knowledge_enabled
                        knowledge_layer_found = True
                        break

                # If knowledge is enabled but no knowledge_base layer exists, create one
                if knowledge_enabled and not knowledge_layer_found:
                    layers.append(
                        {
                            "key": "knowledge_base",
                            "category": "knowledge_base",
                            "enabled": True,
                            "priority": 40,
                            "params": {},
                            "timeout_ms": None,
                            "reserved_tokens": None,
                            "depends_on": [],
                        }
                    )

                merged_config_dict["layers"] = layers

                # Compute effective system prompt for the new config using current core memories
                try:
                    from ..services.context_assembly import build_effective_system_prompt_for_config

                    builder_prompt = (
                        merged_config_dict.get("system_prompt", {}).get("full_system_prompt", "")
                        or ""
                    )
                    effective_prompt = await build_effective_system_prompt_for_config(
                        conn, companion_id=companion_id, builder_prompt=builder_prompt or ""
                    )
                except Exception:
                    effective_prompt = None

                # Create new version via helper to populate effective_system_prompt
                from .companion import CompanionConfig as _Cfg

                cfg_obj = _Cfg(**merged_config_dict)
                version_id = await CompanionRepository.create_companion_version_from_config(
                    conn,
                    companion_id,
                    cfg_obj,
                    status="DEPLOYED",
                    effective_system_prompt=effective_prompt,
                )

                # Deactivate old deployments
                await conn.execute(
                    "UPDATE deployments SET is_active = false WHERE companion_id = $1", companion_id
                )

                # Create new deployment for updated version
                deployment_slug = f"companion-{companion_id}-v{version_id}"
                await conn.execute(
                    """
                    INSERT INTO deployments (companion_id, version_id, slug, is_active)
                    VALUES ($1, $2, $3, true)
                    """,
                    companion_id,
                    version_id,
                    deployment_slug,
                )

                logger.info(
                    f"Created new deployment for companion {companion_id} version {version_id}"
                )

            # Invalidate cache for this companion
            CompanionRepository.invalidate_companion_cache(companion_id)

            # Return updated companion
            return await CompanionRepository.get_companion_by_id(conn, companion_id, user_id)

    @staticmethod
    async def create_companion_version_from_config(
        conn: asyncpg.Connection,
        companion_id: UUID,
        config: CompanionConfig,
        status: str = "SESSION",
        effective_system_prompt: str | None = None,
    ) -> UUID:
        """
        Create a new companion version from a configuration object.
        Used when system prompt changes and we need to version it.

        Args:
            conn: Database connection
            companion_id: ID of the companion
            config: CompanionConfig object with the current configuration
            status: Version status ('SESSION' for testing, 'DEPLOYED' for saved)

        Returns:
            UUID of the created version
        """
        # Pass dict directly - asyncpg's JSONB codec handles serialization
        config_dict = config.model_dump()

        # Sync layers array with memory.enabled and knowledge.enabled state
        memory_enabled = config_dict.get("memory", {}).get("enabled", False)
        knowledge_enabled = config_dict.get("knowledge", {}).get("enabled", False)
        layers = config_dict.get("layers", [])

        # Find existing memory layer or create one if memory is enabled
        memory_layer_found = False
        for layer in layers:
            if layer.get("key") == "memory" and layer.get("category") == "memory":
                layer["enabled"] = memory_enabled
                memory_layer_found = True
                break

        # If memory is enabled but no memory layer exists, create one
        if memory_enabled and not memory_layer_found:
            layers.append(
                {
                    "key": "memory",
                    "category": "memory",
                    "enabled": True,
                    "priority": 50,
                    "params": {},
                    "timeout_ms": None,
                    "reserved_tokens": None,
                    "depends_on": [],
                }
            )

        # Find existing knowledge_base layer or create one if knowledge is enabled
        knowledge_layer_found = False
        for layer in layers:
            if layer.get("key") == "knowledge_base" and layer.get("category") == "knowledge_base":
                layer["enabled"] = knowledge_enabled
                knowledge_layer_found = True
                break

        # If knowledge is enabled but no knowledge_base layer exists, create one
        if knowledge_enabled and not knowledge_layer_found:
            layers.append(
                {
                    "key": "knowledge_base",
                    "category": "knowledge_base",
                    "enabled": True,
                    "priority": 40,
                    "params": {},
                    "timeout_ms": None,
                    "reserved_tokens": None,
                    "depends_on": [],
                }
            )

        config_dict["layers"] = layers

        if effective_system_prompt is None:
            version_query = """
                INSERT INTO companion_versions (companion_id, config, memory_enabled, status)
                VALUES ($1, $2, $3, $4)
                RETURNING id, version_number
            """
            version_row = await conn.fetchrow(
                version_query, companion_id, config_dict, config.memory.enabled, status
            )
        else:
            version_query = """
                INSERT INTO companion_versions (companion_id, config, memory_enabled, status, effective_system_prompt)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, version_number
            """
            version_row = await conn.fetchrow(
                version_query,
                companion_id,
                config_dict,
                config.memory.enabled,
                status,
                effective_system_prompt,
            )

        logger.info(
            f"[DEBUG] Created companion version {version_row['id']} v{version_row['version_number']} for companion {companion_id} with status {status}"
        )
        logger.info(
            f"[DEBUG] New version config preview: {config_dict.get('system_prompt', {}).get('full_system_prompt', '')[:100]}..."
        )
        return version_row["id"]

    @staticmethod
    async def delete_companion(conn: asyncpg.Connection, companion_id: UUID, user_id: UUID) -> bool:
        """Delete companion (cascades to versions)"""
        query = "DELETE FROM companions WHERE id = $1 AND owner_id = $2"
        try:
            result = await conn.execute(query, companion_id, user_id)
            return result == "DELETE 1"
        except Exception:
            logger.exception("Companion deletion failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete companion",
            )

    @staticmethod
    async def is_companion_in_project(
        conn: asyncpg.Connection,
        companion_id: UUID,
        project_id: UUID,
    ) -> bool:
        row = await conn.fetchrow(
            """
            SELECT 1
            FROM companions c
            JOIN projects p ON p.id = c.project_id
            WHERE c.id = $1
              AND c.project_id = $2
              AND c.owner_id = p.owner_id
            """,
            companion_id,
            project_id,
        )
        return row is not None

    @staticmethod
    async def list_companions_for_project(
        conn: asyncpg.Connection,
        project_id: UUID,
    ) -> List[CompanionSummary]:
        rows = await conn.fetch(
            """
            SELECT c.id, c.name, c.project_id,
                   COALESCE(MAX(cv.created_at), c.created_at) AS last_updated
            FROM companions c
            LEFT JOIN companion_versions cv ON cv.companion_id = c.id
            WHERE c.project_id = $1
            GROUP BY c.id, c.name, c.project_id, c.created_at
            ORDER BY last_updated DESC
            """,
            project_id,
        )
        return [
            CompanionSummary(
                id=row["id"],
                name=row["name"],
                project_id=row["project_id"],
                last_updated=CompanionRepository._format_relative_time(row["last_updated"]),
            )
            for row in rows
        ]

    @staticmethod
    async def has_knowledge_assets(conn: asyncpg.Connection, companion_id: UUID) -> bool:
        """Check if companion has knowledge assets or successful ingestion jobs.

        Used by the orchestrator to determine if knowledge layer should run.
        """
        # Check knowledge_assets table
        row = await conn.fetchrow(
            "SELECT 1 FROM knowledge_assets WHERE companion_id = $1 LIMIT 1",
            companion_id,
        )
        if row:
            return True
        # Check unified jobs table for completed knowledge ingestion
        job_row = await conn.fetchrow(
            """
            SELECT 1 FROM jobs
            WHERE companion_id = $1
              AND job_type = 'knowledge_ingestion'
              AND status = 'completed'
            LIMIT 1
            """,
            companion_id,
        )
        return bool(job_row)
