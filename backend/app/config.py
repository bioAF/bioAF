import tomllib
from pathlib import Path

from pydantic_settings import BaseSettings


def _read_pyproject_version() -> str:
    """Read the version string from pyproject.toml (single source of truth)."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


class Settings(BaseSettings):
    # Application
    app_name: str = "bioAF"
    app_version: str = _read_pyproject_version()
    debug: bool = False
    environment: str = "production"

    # Database
    database_url: str = "postgresql+asyncpg://bioaf_app:password@localhost:5432/bioaf"

    # JWT
    jwt_secret_key: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # GCP
    gcp_project_id: str = ""
    use_secret_manager: bool = False

    # SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_encryption: str = "starttls"
    smtp_configured: bool = False

    # Slack OAuth
    slack_client_id: str = ""
    slack_client_secret: str = ""
    slack_signing_secret: str = ""

    # Rate limiting
    rate_limit_login: int = 10
    rate_limit_verify: int = 5
    rate_limit_reset: int = 3
    trusted_proxy_cidrs: str = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128"

    # Compute
    compute_mode: str = "kubernetes"

    # Local mode cost overrides (used when BIOAF_COMPUTE_MODE=local)
    local_node_cost_hourly: float = 0.01
    local_storage_cost_monthly: float = 0.11

    # SSL / TLS
    ssl_enabled: bool = False
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    # Bcrypt
    bcrypt_rounds: int = 12

    model_config = {"env_prefix": "BIOAF_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
