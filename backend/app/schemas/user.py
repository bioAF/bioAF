from datetime import datetime

from pydantic import BaseModel, EmailStr


class UserInvite(BaseModel):
    email: EmailStr
    role: str = "viewer"
    name: str | None = None


class BulkInvite(BaseModel):
    invites: list[UserInvite]


class UserUpdate(BaseModel):
    role: str | None = None
    name: str | None = None


class AcceptInviteRequest(BaseModel):
    token: str
    password: str
    name: str | None = None


class UserResponse(BaseModel):
    id: int
    email: str
    name: str | None
    role: str
    status: str
    organization_id: int
    last_login: datetime | None = None
    session_credentials_configured: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int
