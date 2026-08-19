"""
OnboardingService: Creates companions from conversational onboarding.

Used by:
- /api/internal/onboarding/create-companion-from-answers (voice tools)
- /api/onboarding/create-companion-from-answers (dashboard, legacy)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Tuple
from uuid import UUID

if TYPE_CHECKING:
    import asyncpg

from ..models.companion import CompanionCreate, CompanionDetail
from ..repositories.companion import CompanionRepository
from ..repositories.project import ProjectRepository
from .onboarding_enrichment import OnboardingInput, enrich_onboarding_input

logger = logging.getLogger(__name__)


async def create_companion_from_description(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    description: str,
    name: str | None = None,
    vibe: str | None = None,
    mark_onboarding_complete: bool = True,
) -> Tuple[CompanionDetail, str]:
    """
    Create a companion from a conversational description.

    Args:
        conn: Database connection
        user_id: The user to create the companion for
        description: Rich description of the companion from conversation
        name: Optional custom name
        vibe: Optional vibe (chill, energetic, warm, witty, intense)
        mark_onboarding_complete: Whether to mark user's onboarding as complete

    Returns:
        Tuple of (companion, companion_name)
    """
    # Transform to internal model
    input = OnboardingInput(
        description=description,
        name=name,
        vibe=vibe,
    )

    # Enrich into full companion config
    config, companion_name = enrich_onboarding_input(input)

    # Get or create default project for user
    project = await ProjectRepository.ensure_default_project(conn, user_id)

    # Create the companion
    companion_data = CompanionCreate(
        name=companion_name,
        description=description[:200] + "..." if len(description) > 200 else description,
        config=config,
    )

    companion = await CompanionRepository.create_companion(
        conn,
        user_id=user_id,
        companion_data=companion_data,
        project_id=project.id,
    )

    # Mark onboarding as completed if requested
    if mark_onboarding_complete:
        await conn.execute(
            """
            UPDATE users
            SET onboarding_completed = true,
                onboarding_completed_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            """,
            user_id,
        )

    logger.info(
        "Created companion from description",
        extra={
            "companion_id": str(companion.id),
            "companion_name": companion_name,
            "user_id": str(user_id),
            "vibe": vibe,
            "description_length": len(description),
        },
    )

    return companion, companion_name


# Legacy function for backward compatibility
async def create_companion_from_answers(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    purpose: str,
    approach: str,
    tone: str,
    custom_purpose: str | None = None,
    name: str | None = None,
    mark_onboarding_complete: bool = True,
) -> Tuple[CompanionDetail, str]:
    """Legacy function - converts old format to new."""
    # Build description from old fields
    if custom_purpose:
        description = custom_purpose
    else:
        description = f"A {approach} {purpose} with a {tone} communication style"

    # Map approach to vibe
    approach_to_vibe = {
        "playful": "witty",
        "supportive": "warm",
        "challenging": "intense",
    }
    vibe = approach_to_vibe.get(approach)

    return await create_companion_from_description(
        conn,
        user_id=user_id,
        description=description,
        name=name,
        vibe=vibe,
        mark_onboarding_complete=mark_onboarding_complete,
    )
