import logging
import sys
import tomllib
from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings

from app.settings_contract import ComputeMode, SettingsError, describe_settings_error, parse_compute_mode

logger = logging.getLogger("bioaf")

# Secrets that ship in the public repo or docker-compose defaults.
# Compared case-insensitively against the configured JWT secret at startup.
INSECURE_JWT_SECRETS = frozenset(
    {
        "dev-secret-key-change-in-production",
        "change_me_to_random_string",
        "change_me",
    }
)

MIN_JWT_SECRET_LENGTH = 32

# Fernet keys are 32 raw bytes encoded as urlsafe-base64 -> 44 chars.
FERNET_KEY_LENGTH = 44


def validate_jwt_secret(secret: str) -> None:
    """Refuse to start if the JWT signing key is a known default or too short.

    Calls sys.exit(1) on failure so the container stops immediately with a
    clear error message rather than silently serving traffic with a compromised
    secret.
    """
    if not secret or secret.lower() in INSECURE_JWT_SECRETS:
        logger.critical(
            "FATAL: JWT secret key is a known insecure default. "
            "Set BIOAF_JWT_SECRET_KEY to a random value (e.g. `openssl rand -hex 32`). "
            "Run `./install.sh generate-env --force` to regenerate secrets."
        )
        sys.exit(1)

    if len(secret) < MIN_JWT_SECRET_LENGTH:
        logger.critical(
            "FATAL: JWT secret key is too short (minimum %d chars). "
            "Set BIOAF_JWT_SECRET_KEY to a random value (e.g. `openssl rand -hex 32`).",
            MIN_JWT_SECRET_LENGTH,
        )
        sys.exit(1)


def parse_encryption_keys(raw: str) -> list[str]:
    """Split BIOAF_ENCRYPTION_KEYS on commas and return non-empty entries."""
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def validate_encryption_keys(raw: str) -> list[str]:
    """Refuse to start if the at-rest encryption keys are missing or invalid.

    Returns the parsed key list on success. Mirrors validate_jwt_secret in
    behavior: a fatal-log + sys.exit(1) on failure so the container halts
    rather than running with broken encryption.
    """
    keys = parse_encryption_keys(raw)

    if not keys:
        logger.critical(
            "FATAL: BIOAF_ENCRYPTION_KEYS is unset. "
            'Generate a key with `python3 -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and set BIOAF_ENCRYPTION_KEYS. '
            "Run `./install.sh generate-env --force` to regenerate secrets."
        )
        sys.exit(1)

    from cryptography.fernet import Fernet

    for index, key in enumerate(keys):
        if len(key) != FERNET_KEY_LENGTH:
            logger.critical(
                "FATAL: BIOAF_ENCRYPTION_KEYS entry %d is %d chars; expected %d (urlsafe-base64 Fernet key).",
                index,
                len(key),
                FERNET_KEY_LENGTH,
            )
            sys.exit(1)
        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            logger.critical(
                "FATAL: BIOAF_ENCRYPTION_KEYS entry %d is not a valid Fernet key: %s",
                index,
                exc,
            )
            sys.exit(1)

    logger.info("Loaded %d encryption key(s) for data-at-rest", len(keys))
    return keys


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

    # Compute: required, with no default (app/settings_contract.py). Production Compose supplies
    # kubernetes when the host leaves it unset; development and tests supply local explicitly.
    compute_mode: ComputeMode

    # Local mode cost overrides (used when BIOAF_COMPUTE_MODE=local)
    local_node_cost_hourly: float = 0.01
    local_storage_cost_monthly: float = 0.11

    # SSL / TLS
    ssl_enabled: bool = False
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    # Backups
    backup_postgres_interval_hours: int = 24
    backup_postgres_retention_days: int = 14
    backup_config_retention_days: int = 30

    # Update system
    update_requests_dir: str = "/app/update-requests"
    update_status_dir: str = "/app/update-status"

    # Database upgrade lifecycle (app/schema_upgrade.py). Recovery dumps go to a host bind mount
    # (docker-compose.yml: ../backups:/app/backups) so they survive container replacement, made with
    # the image's pinned PostgreSQL 16 client (Dockerfile.backend), never whatever is on PATH.
    upgrade_backups_dir: str = "/app/backups"
    upgrade_pg_bin_dir: str = "/usr/lib/postgresql/16/bin"
    upgrade_backup_reserve_bytes: int = 512 * 1024 * 1024
    upgrade_backup_timeout_seconds: int = 3600
    upgrade_lock_timeout_seconds: int = 7200
    # Set in the image (Dockerfile.backend): a dump in the container's writable layer would be lost
    # when Compose replaces the container, so there the backups directory must be a mount.
    upgrade_require_mounted_backups: bool = False

    # Bcrypt
    bcrypt_rounds: int = 12

    # Data-at-rest encryption (comma-separated Fernet keys; first key is the
    # primary writer, all keys are accepted readers).
    encryption_keys: str = ""

    # hide_input_in_errors: a rejected setting must not print the other supplied values (secrets).
    model_config = {"env_prefix": "BIOAF_", "env_file": ".env", "extra": "ignore", "hide_input_in_errors": True}

    @field_validator("compute_mode", mode="before")
    @classmethod
    def _normalize_compute_mode(cls, value: object) -> ComputeMode:
        return parse_compute_mode(value)


def load_settings() -> Settings:
    """The process settings, or a SettingsError whose message names the bad settings and no values."""
    try:
        return Settings()  # type: ignore[call-arg]  # compute_mode comes from the environment or .env
    except ValidationError as exc:
        raise SettingsError(describe_settings_error(exc)) from None


settings = load_settings()


def compute_mode_for(override: ComputeMode | None = None) -> ComputeMode:
    """The mode a mode-aware adapter is built in: the resolved setting, or a typed in-process override.

    The override is a test seam, not a second bootstrap path: the setting stays required either way."""
    return settings.compute_mode if override is None else ComputeMode(override)
