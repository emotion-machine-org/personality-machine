import logging

import asyncpg
from fastapi import HTTPException, status

from ..models.user import User, UserCreate, UserUpdate

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user data operations"""

    @staticmethod
    async def get_by_clerk_id(conn: asyncpg.Connection, clerk_user_id: str) -> User | None:
        """Get user by Clerk user ID"""
        query = """
            SELECT id, clerk_user_id, email, username, display_name, avatar_url,
                   auth_provider, created_at, updated_at, onboarding_completed, onboarding_completed_at
            FROM users
            WHERE clerk_user_id = $1
            ORDER BY created_at ASC
            LIMIT 1
        """
        row = await conn.fetchrow(query, clerk_user_id)
        return User(**dict(row)) if row else None

    @staticmethod
    async def get_by_email(conn: asyncpg.Connection, email: str) -> User | None:
        """Get user by email."""
        query = """
            SELECT id, clerk_user_id, email, username, display_name, avatar_url,
                   auth_provider, created_at, updated_at, onboarding_completed, onboarding_completed_at
            FROM users
            WHERE LOWER(email) = LOWER($1)
            ORDER BY created_at ASC
            LIMIT 1
        """
        row = await conn.fetchrow(query, email)
        return User(**dict(row)) if row else None

    @staticmethod
    async def create(conn: asyncpg.Connection, user_data: UserCreate) -> User:
        """Create a new user"""
        query = """
            INSERT INTO users (clerk_user_id, email, username, display_name, avatar_url, auth_provider)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, clerk_user_id, email, username, display_name, avatar_url,
                      auth_provider, created_at, updated_at, onboarding_completed, onboarding_completed_at
        """
        try:
            row = await conn.fetchrow(
                query,
                user_data.clerk_user_id,
                user_data.email,
                user_data.username,
                user_data.display_name,
                user_data.avatar_url,
                user_data.auth_provider,
            )
            return User(**dict(row))
        except asyncpg.UniqueViolationError as e:
            logger.error(f"User creation failed - unique constraint: {e}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email or clerk_user_id already exists",
            )
        except Exception as e:
            logger.error(f"User creation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create user"
            )

    @staticmethod
    async def update_by_clerk_id(
        conn: asyncpg.Connection, clerk_user_id: str, updates: UserUpdate
    ) -> User | None:
        """Update user by Clerk user ID"""
        # Build dynamic update query
        set_clauses = []
        values = []
        param_count = 1

        for field, value in updates.model_dump(exclude_unset=True).items():
            if value is not None:
                set_clauses.append(f"{field} = ${param_count}")
                values.append(value)
                param_count += 1

        if not set_clauses:
            # No fields to update, return current user
            return await UserRepository.get_by_clerk_id(conn, clerk_user_id)

        query = f"""
            UPDATE users
            SET {", ".join(set_clauses)}, updated_at = CURRENT_TIMESTAMP
            WHERE clerk_user_id = ${param_count}
            RETURNING id, clerk_user_id, email, username, display_name, avatar_url,
                      auth_provider, created_at, updated_at, onboarding_completed, onboarding_completed_at
        """
        values.append(clerk_user_id)

        try:
            row = await conn.fetchrow(query, *values)
            return User(**dict(row)) if row else None
        except asyncpg.UniqueViolationError as e:
            logger.error(f"User update failed - unique constraint: {e}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            )
        except Exception as e:
            logger.error(f"User update failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update user"
            )

    @staticmethod
    async def reassign_clerk_identity(
        conn: asyncpg.Connection,
        *,
        canonical_user_id: str,
        new_clerk_user_id: str,
        email: str,
        username: str | None,
        display_name: str | None,
        avatar_url: str | None,
        auth_provider: str | None,
    ) -> User | None:
        """Point a canonical user record at a Clerk identity and refresh profile fields."""
        query = """
            UPDATE users
            SET clerk_user_id = $1,
                email = $2,
                username = COALESCE($3, username),
                display_name = COALESCE($4, display_name),
                avatar_url = COALESCE($5, avatar_url),
                auth_provider = COALESCE($6, auth_provider),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $7::uuid
            RETURNING id, clerk_user_id, email, username, display_name, avatar_url,
                      auth_provider, created_at, updated_at, onboarding_completed, onboarding_completed_at
        """
        row = await conn.fetchrow(
            query,
            new_clerk_user_id,
            email,
            username,
            display_name,
            avatar_url,
            auth_provider,
            canonical_user_id,
        )
        return User(**dict(row)) if row else None
