from pydantic import BaseModel, EmailStr


class BootstrapStatus(BaseModel):
    setup_complete: bool
    smtp_configured: bool = False


class CreateAdminRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None


class ConfigureOrgRequest(BaseModel):
    org_name: str


class ConfigureSmtpRequest(BaseModel):
    host: str
    port: int = 587
    username: str
    password: str
    from_address: str
    encryption: str = "starttls"


class SmtpSettingsResponse(BaseModel):
    host: str
    port: int
    username: str
    password: str
    from_address: str
    encryption: str
    configured: bool


class TestSmtpRequest(BaseModel):
    to: EmailStr


class TestSmtpResponse(BaseModel):
    status: str
    to: str
    detail: str | None = None
