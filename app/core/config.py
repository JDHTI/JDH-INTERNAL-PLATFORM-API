"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported runtime environments."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class Settings(BaseSettings):
    """Application settings loaded from a local .env file or the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_NAME: str = "Internal Platform API"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Environment = Environment.DEV
    DEBUG: bool = False
    PORT: int = 8000
    API_PREFIX: str = "/api"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"

    DATABASE_URL: SecretStr = SecretStr(
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/internal_platform"
    )
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 10
    DATABASE_CONNECT_TIMEOUT_SECONDS: float = 2.0

    REDIS_URL: SecretStr = SecretStr("redis://:redis@127.0.0.1:6379/0")
    REDIS_KEY_PREFIX: str = "internal-platform"
    REDIS_DEFAULT_TTL_SECONDS: int = 3600
    REDIS_CONNECT_TIMEOUT_SECONDS: float = 2.0

    S3_ENDPOINT_URL: str = "http://127.0.0.1:9000"
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: SecretStr = SecretStr("minioadmin")
    S3_BUCKET: str = "internal-platform"
    S3_CONNECT_TIMEOUT_SECONDS: float = 2.0
    # Host used to sign download URLs. The signature covers the host, so it
    # must be the one the browser reaches; empty means S3_ENDPOINT_URL.
    S3_PUBLIC_ENDPOINT_URL: str = ""

    # Microsoft Entra ID
    AUTH_MICROSOFT_ENTRA_ID_SECRET: SecretStr = SecretStr("")
    AUTH_MICROSOFT_ENTRA_ID_ID: str = ""
    AUTH_MICROSOFT_ENTRA_ID_TENANT_ID: str = ""
    # Dev-only stand-in for Entra while the UI has no real session: requests
    # without a bearer token act as X-Dev-User-Email or AUTH_DEV_USER_EMAIL.
    # Refused at startup outside ENVIRONMENT=dev.
    AUTH_DEV_BYPASS: bool = False
    AUTH_DEV_USER_EMAIL: str = "usuario@jdh.com.co"
    # Platform administrators: manage areas, roles and members, and see every area.
    PLATFORM_ADMIN_EMAILS: str = "analista.ti@jdh.com.co,requerimientos@jdh.com.co"

    # Document management
    DOCUMENTS_MAX_UPLOAD_MB: int = 25
    DOCUMENTS_ALLOWED_EXTENSIONS: str = (
        "pdf,doc,docx,xls,xlsx,xlsm,csv,ppt,pptx,txt,png,jpg,jpeg,zip"
    )
    DOCUMENTS_DOWNLOAD_URL_TTL_SECONDS: int = 300

    @model_validator(mode="after")
    def _forbid_dev_bypass_outside_dev(self) -> "Settings":
        if self.AUTH_DEV_BYPASS and self.ENVIRONMENT is not Environment.DEV:
            raise ValueError("AUTH_DEV_BYPASS solo se permite con ENVIRONMENT=dev")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def APP_ENV(self) -> str:
        """Compatibility alias used by startup logs and integrations."""

        return self.ENVIRONMENT.value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def docs_enabled(self) -> bool:
        """Only expose OpenAPI and interactive docs outside production."""

        return self.ENVIRONMENT is not Environment.PROD

    @computed_field  # type: ignore[prop-decorator]
    @property
    def debug_enabled(self) -> bool:
        """Debug is opt-in and never enabled implicitly in production."""

        return self.DEBUG and self.ENVIRONMENT is not Environment.PROD

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins(self) -> list[str]:
        """Return normalized CORS origins."""

        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    def container_config(self) -> dict[str, dict[str, object]]:
        """Return the subset consumed by dependency providers."""

        return {
            "database": {
                "url": self.DATABASE_URL.get_secret_value(),
                "pool_size": self.DATABASE_POOL_SIZE,
                "max_overflow": self.DATABASE_MAX_OVERFLOW,
                "pool_timeout": self.DATABASE_POOL_TIMEOUT,
                "connect_timeout": self.DATABASE_CONNECT_TIMEOUT_SECONDS,
            },
            "redis": {
                "url": self.REDIS_URL.get_secret_value(),
                "key_prefix": self.REDIS_KEY_PREFIX,
                "default_ttl": self.REDIS_DEFAULT_TTL_SECONDS,
                "connect_timeout": self.REDIS_CONNECT_TIMEOUT_SECONDS,
            },
            "s3": {
                "endpoint_url": self.S3_ENDPOINT_URL,
                "region": self.S3_REGION,
                "access_key_id": self.S3_ACCESS_KEY_ID,
                "secret_access_key": self.S3_SECRET_ACCESS_KEY.get_secret_value(),
                "bucket": self.S3_BUCKET,
                "connect_timeout": self.S3_CONNECT_TIMEOUT_SECONDS,
                "public_endpoint_url": self.S3_PUBLIC_ENDPOINT_URL,
            },
            "auth": {
                "tenant_id": self.AUTH_MICROSOFT_ENTRA_ID_TENANT_ID,
                "client_id": self.AUTH_MICROSOFT_ENTRA_ID_ID,
                "dev_bypass": self.AUTH_DEV_BYPASS,
                "dev_user_email": self.AUTH_DEV_USER_EMAIL.strip().lower(),
                "admin_emails": _split_lower(self.PLATFORM_ADMIN_EMAILS),
            },
            "documents": {
                "max_upload_bytes": self.DOCUMENTS_MAX_UPLOAD_MB * 1024 * 1024,
                "allowed_extensions": _split_lower(self.DOCUMENTS_ALLOWED_EXTENSIONS),
                "download_url_ttl": self.DOCUMENTS_DOWNLOAD_URL_TTL_SECONDS,
            },
        }


def _split_lower(value: str) -> list[str]:
    return [item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Create one settings object per process."""

    return Settings()


settings = get_settings()
