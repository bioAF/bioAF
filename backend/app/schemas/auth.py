from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenRefreshRequest(BaseModel):
    token: str


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AdminResetPasswordRequest(BaseModel):
    mode: str  # "email" or "temporary"
    temporary_password: str | None = None


class PermissionEntry(BaseModel):
    resource: str
    action: str


class UserProfile(BaseModel):
    id: int
    email: str
    name: str | None
    role_id: int
    role_name: str = ""
    organization_id: int
    status: str
    permissions: list[PermissionEntry] = []

    model_config = {"from_attributes": True}
