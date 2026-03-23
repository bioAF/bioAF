from datetime import datetime

from pydantic import BaseModel


class PermissionEntry(BaseModel):
    resource: str
    action: str


class RoleCreate(BaseModel):
    name: str
    description: str | None = None
    permissions: list[PermissionEntry] = []


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class RolePermissionsUpdate(BaseModel):
    permissions: list[PermissionEntry]


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None
    organization_id: int
    is_system: bool
    permissions: list[PermissionEntry] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class RoleListResponse(BaseModel):
    roles: list[RoleResponse]
    total: int
