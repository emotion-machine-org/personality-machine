from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class UserBase(BaseModel):
    email: str = Field(..., max_length=255)
    username: str | None = Field(None, max_length=100)
    display_name: str | None = Field(None, max_length=255)
    avatar_url: str | None = None
    auth_provider: str = Field(default="email", max_length=50)


class UserCreate(UserBase):
    clerk_user_id: str = Field(..., max_length=255)


class UserUpdate(BaseModel):
    email: str | None = Field(None, max_length=255)
    username: str | None = Field(None, max_length=100)
    display_name: str | None = Field(None, max_length=255)
    avatar_url: str | None = None
    auth_provider: str | None = Field(None, max_length=50)


class User(UserBase):
    id: UUID
    clerk_user_id: str
    created_at: datetime
    updated_at: datetime
    onboarding_completed: bool = False
    onboarding_completed_at: datetime | None = None

    class Config:
        from_attributes = True
