import re
from datetime import datetime

from pydantic import BaseModel, field_validator


class SessionCredentialRequest(BaseModel):
    username: str | None = None
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 32:
            raise ValueError("Username must be at most 32 characters")
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                "Username must start with a letter and contain only lowercase letters, numbers, and underscores"
            )
        return v


class SessionCredentialResponse(BaseModel):
    configured: bool
    username: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}
