from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    app_name: str = "bioAF"
    app_version: str = "0.1.0"
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
    smtp_configured: bool = False

    # Rate limiting
    rate_limit_login: int = 10
    rate_limit_verify: int = 5
    rate_limit_reset: int = 3

    # Compute
    compute_mode: str = "kubernetes"

    # Bcrypt
    bcrypt_rounds: int = 12

    model_config = {"env_prefix": "BIOAF_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
