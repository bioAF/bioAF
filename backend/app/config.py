import logging
import sys
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings

from app.settings_contract import (
    ComputeMode,
    LogDelivery,
    SettingsError,
    describe_settings_error,
    parse_compute_mode,
    parse_log_delivery,
)

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


MAX_FILE_VERIFICATION_CONCURRENCY = 8


class Settings(BaseSettings):
    # Application
    app_name: str = "bioAF"
    app_version: str = _read_pyproject_version()
    debug: bool = False
    environment: str = "production"
    # Centralized log delivery (one path, never both): stdout for a host collector such as the Ops
    # Agent, or direct for the application's own cloud log sink. No automatic fallback between them.
    log_delivery: LogDelivery = "stdout"

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

    # Plan 03.5A: the notebook gateway's port on the installation's own host (separate browser origin).
    notebook_gateway_port: int = 8443
    # Where an uploaded organization-CA certificate is staged for the host to install (plan 03.5A).
    tls_upload_dir: str = "/app/tls-upload"

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
    # Plan 03B: the token the managed nginx edge adds to every API request it proxies, so the backend
    # can trust the peer address nginx wrote in X-Real-IP (BIOAF_EDGE_TOKEN; empty: never trusted).
    edge_token: str = ""

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
    # The GEO file association advisory (app/services/geo/association_advisory.py) runs in the started
    # backend after this delay, with one deadline for the whole scan. A timeout only marks the advisory.
    geo_association_advisory_delay_seconds: float = 60
    geo_association_advisory_timeout_seconds: float = 120

    # Bcrypt
    bcrypt_rounds: int = 12

    # Data exports (app/services/data_export). Scratch shares the host disk with Docker and
    # PostgreSQL; exports refuse to build below a free-space floor (data_export/resources.py).
    export_scratch_dir: str = "/tmp/bioaf-exports"
    # Database backup creation and restore (app/services/backup_resources.py): one private workspace per
    # operation, removed when it ends.
    backup_scratch_dir: str = "/tmp/bioaf-backups"
    # Restore preparation limits: the owner-approved provisional envelope, to be replaced by measured values
    # from a representative install. Dump bytes are counted as they are written, not taken from metadata.
    restore_max_dump_bytes: int = 10 * 1024**3
    restore_scratch_floor_min_bytes: int = 3 * 1024**3
    restore_scratch_floor_percent: int = 10
    restore_download_timeout_seconds: int = 15 * 60
    restore_toc_timeout_seconds: int = 60
    restore_pg_restore_timeout_seconds: int = 30 * 60
    restore_preparation_timeout_seconds: int = 45 * 60
    # The PostgreSQL data volume, mounted read-only into the backend (docker-compose.yml) so a restore can check
    # the restored copy and its WAL fit there. Empty: not mounted, and the headroom is reported as not measured.
    restore_db_volume_dir: str = ""
    # A direct download whose client accepts no bytes for this long is closed (nginx's own
    # client send_timeout is pinned to the same 60 s in docker/nginx.conf).
    export_transfer_stall_seconds: int = 60
    # A signed export link must stay usable at least this long after it is issued; shorter is refused.
    export_signed_min_ttl_seconds: int = 3600

    # Installation metrics (ADR-075). The operator override wins over the admin's choice: it stops
    # capture and cancels pending reports. The PostHog pair overrides the release defaults in
    # app/install_metrics_release.py; both or neither, or delivery is disabled.
    install_metrics_disabled: bool = False
    posthog_project_token: str = ""
    posthog_host: str = ""

    # Internal webhook receivers (app/services/webhook_transport.py). Operator-only, set through the
    # BIOAF_WEBHOOK_INTERNAL_DESTINATIONS environment variable as a JSON list of
    # {"organization_id", "origin", "cidrs"} entries; changing it needs a restart. Empty by default:
    # webhooks then reach public HTTPS receivers only. An invalid value refuses startup.
    webhook_internal_destinations: Any = ()

    # Checksum verification worker (app/services/file_verification_worker.py). One active digest job per
    # process by default (BIOAF_FILE_VERIFICATION_CONCURRENCY, 1-8); reads in bounded chunks; a lease is
    # renewed while work progresses and reclaimed after a crash. A job past its deadline is abandoned and
    # retried. Raise concurrency only after measuring the deployment's storage throughput and memory.
    file_verification_concurrency: int = 1
    file_verification_lease_seconds: int = 300
    file_verification_chunk_bytes: int = 8 * 1024 * 1024
    file_verification_job_deadline_seconds: int = 6 * 3600
    # NFS protected copies (app/adapters/storage/nfs_managed_copy.py): the UIDs pipelines, notebooks and
    # uploads write NFS as (comma-separated), which must differ from the backend's, and the free space kept
    # in reserve beyond one extra copy. Without producer UIDs, checksum-required NFS files are refused.
    nfs_producer_uids: str = ""
    nfs_managed_copy_reserve_bytes: int = 1024 * 1024 * 1024

    # Data-at-rest encryption (comma-separated Fernet keys; first key is the
    # primary writer, all keys are accepted readers).
    encryption_keys: str = ""

    # hide_input_in_errors: a rejected setting must not print the other supplied values (secrets).
    model_config = {"env_prefix": "BIOAF_", "env_file": ".env", "extra": "ignore", "hide_input_in_errors": True}

    @field_validator("compute_mode", mode="before")
    @classmethod
    def _normalize_compute_mode(cls, value: object) -> ComputeMode:
        return parse_compute_mode(value)

    @field_validator("webhook_internal_destinations", mode="before")
    @classmethod
    def _parse_webhook_internal_destinations(cls, value: object) -> tuple:
        from app.services.webhook_transport import parse_internal_destinations

        return parse_internal_destinations(value)

    @field_validator("file_verification_concurrency")
    @classmethod
    def _bound_file_verification_concurrency(cls, value: int) -> int:
        if not 1 <= value <= MAX_FILE_VERIFICATION_CONCURRENCY:
            raise ValueError(f"file_verification_concurrency must be between 1 and {MAX_FILE_VERIFICATION_CONCURRENCY}")
        return value

    @field_validator("log_delivery", mode="before")
    @classmethod
    def _normalize_log_delivery(cls, value: object) -> LogDelivery:
        return parse_log_delivery(value)


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
