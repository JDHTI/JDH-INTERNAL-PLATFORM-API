"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, computed_field
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
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Create one settings object per process."""

    return Settings()


settings = get_settings()
