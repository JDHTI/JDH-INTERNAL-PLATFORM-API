"""Typed application configuration loaded from environment variables."""

import re
from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIGURED_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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

    # Microsoft Entra ID / browser SSO. Tokens are kept in HttpOnly cookies by
    # the API; the browser client never receives them in a response body.
    ENTRA_TENANT_ID: str = ""
    ENTRA_CLIENT_ID: str = ""
    ENTRA_CLIENT_SECRET: SecretStr = SecretStr("")
    ENTRA_API_AUDIENCE: str = ""
    ENTRA_API_SCOPE: str = "access_as_user"
    ENTRA_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/callback"
    AUTH_FRONTEND_REDIRECT_URI: str = ""
    AUTH_DEV_BYPASS: bool = False
    AUTH_DEV_USER_OBJECT_ID: str = "development-user"
    AUTH_DEV_USER_EMAIL: str = "developer@localhost"
    AUTH_DEV_USER_NAME: str = "Development Administrator"
    AUTH_COOKIE_DOMAIN: str | None = None
    AUTH_COOKIE_SAMESITE: str = "lax"
    AUTH_COOKIE_SECURE: bool | None = None
    AUTH_ACCESS_COOKIE_NAME: str = "internal_platform_access"
    AUTH_REFRESH_COOKIE_NAME: str = "internal_platform_refresh"
    AUTH_STATE_COOKIE_NAME: str = "internal_platform_oidc_state"
    AUTH_ACCESS_COOKIE_MAX_AGE: int = 3600
    AUTH_REFRESH_COOKIE_MAX_AGE: int = 2_592_000
    AUTH_STATE_COOKIE_MAX_AGE: int = 600

    DATABASE_URL: SecretStr = SecretStr(
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/internal_platform"
    )
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 10
    DATABASE_CONNECT_TIMEOUT_SECONDS: float = 2.0

    # Read-only external data provider. The first adapter uses PostgreSQL and
    # reads only the configured consumption schema.
    DWH_ENABLED: bool = True
    DWH_PROVIDER: str = "postgres"
    DWH_HOST: str = ""
    DWH_PORT: int = 5432
    DWH_DATABASE: str = ""
    DWH_USER: str = ""
    DWH_PASSWORD: SecretStr = SecretStr("")
    DWH_SCHEMA: str = "consumo"
    DWH_CLIENTS_VIEW: str = "clientes"
    DWH_POOL_MIN_SIZE: int = 1
    DWH_POOL_MAX_SIZE: int = 5
    DWH_CONNECT_TIMEOUT_SECONDS: float = 2.0
    DWH_COMMAND_TIMEOUT_SECONDS: float = 10.0
    DWH_CURSOR_SECRET: SecretStr = SecretStr("")

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

    # Microsoft Entra ID
    AUTH_MICROSOFT_ENTRA_ID_SECRET: SecretStr = SecretStr("")
    AUTH_MICROSOFT_ENTRA_ID_ID: str = ""
    AUTH_MICROSOFT_ENTRA_ID_TENANT_ID: str = ""

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

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        """Reject unsafe or incomplete authentication mode combinations."""

        if self.AUTH_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict, or none")
        if self.AUTH_COOKIE_SAMESITE == "none" and not self.auth_cookie_secure:
            raise ValueError("SameSite=None cookies require AUTH_COOKIE_SECURE=true")
        if self.AUTH_DEV_BYPASS and self.ENVIRONMENT is not Environment.DEV:
            raise ValueError("AUTH_DEV_BYPASS is only allowed in the dev environment")
        if self.ENVIRONMENT is Environment.PROD and self.AUTH_COOKIE_SECURE is False:
            raise ValueError("AUTH_COOKIE_SECURE=false is not allowed in production")
        if self.DWH_POOL_MIN_SIZE < 0 or self.DWH_POOL_MAX_SIZE < self.DWH_POOL_MIN_SIZE:
            raise ValueError("DWH pool sizes are invalid")
        if self.DWH_PROVIDER.strip().lower() not in {"postgres"}:
            raise ValueError("DWH_PROVIDER must be postgres")
        if not _CONFIGURED_IDENTIFIER.fullmatch(self.DWH_SCHEMA):
            raise ValueError("DWH_SCHEMA must be a valid SQL identifier")
        if not _CONFIGURED_IDENTIFIER.fullmatch(self.DWH_CLIENTS_VIEW):
            raise ValueError("DWH_CLIENTS_VIEW must be a valid SQL identifier")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def auth_cookie_secure(self) -> bool:
        """Use secure cookies in production and when explicitly requested."""

        return (
            self.ENVIRONMENT is Environment.PROD
            if self.AUTH_COOKIE_SECURE is None
            else self.AUTH_COOKIE_SECURE
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def entra_authority(self) -> str:
        """Return the single-tenant Entra authority used by OIDC."""

        tenant = self.ENTRA_TENANT_ID or "organizations"
        return f"https://login.microsoftonline.com/{tenant}/v2.0"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def entra_issuer(self) -> str:
        """Return the issuer expected in v2 access tokens."""

        tenant = self.ENTRA_TENANT_ID or "organizations"
        return f"https://login.microsoftonline.com/{tenant}/v2.0"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def entra_api_audience(self) -> str:
        """Use the API application ID URI, defaulting to the client ID."""

        return self.ENTRA_API_AUDIENCE or self.ENTRA_CLIENT_ID

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
            "data_provider": {
                "enabled": self.DWH_ENABLED,
                "provider_name": self.DWH_PROVIDER,
                "host": self.DWH_HOST,
                "port": self.DWH_PORT,
                "database": self.DWH_DATABASE,
                "user": self.DWH_USER,
                "password": self.DWH_PASSWORD.get_secret_value(),
                "schema": self.DWH_SCHEMA,
                "clients_view": self.DWH_CLIENTS_VIEW,
                "min_size": self.DWH_POOL_MIN_SIZE,
                "max_size": self.DWH_POOL_MAX_SIZE,
                "connect_timeout": self.DWH_CONNECT_TIMEOUT_SECONDS,
                "command_timeout": self.DWH_COMMAND_TIMEOUT_SECONDS,
                "cursor_secret": self.DWH_CURSOR_SECRET.get_secret_value()
                or f"{self.APP_NAME}:{self.DWH_PROVIDER}",
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
            "identity": {
                "tenant_id": self.ENTRA_TENANT_ID,
                "client_id": self.ENTRA_CLIENT_ID,
                "client_secret": self.ENTRA_CLIENT_SECRET.get_secret_value(),
                "api_audience": self.entra_api_audience,
                "api_scope": self.ENTRA_API_SCOPE,
                "redirect_uri": self.ENTRA_REDIRECT_URI,
                "frontend_redirect_uri": self.AUTH_FRONTEND_REDIRECT_URI,
                "dev_bypass": self.AUTH_DEV_BYPASS,
                "dev_user_object_id": self.AUTH_DEV_USER_OBJECT_ID,
                "dev_user_email": self.AUTH_DEV_USER_EMAIL,
                "dev_user_name": self.AUTH_DEV_USER_NAME,
                "authority": self.entra_authority,
                "issuer": self.entra_issuer,
                "cookie_domain": self.AUTH_COOKIE_DOMAIN,
                "cookie_samesite": self.AUTH_COOKIE_SAMESITE,
                "cookie_secure": self.auth_cookie_secure,
                "access_cookie_name": self.AUTH_ACCESS_COOKIE_NAME,
                "refresh_cookie_name": self.AUTH_REFRESH_COOKIE_NAME,
                "state_cookie_name": self.AUTH_STATE_COOKIE_NAME,
                "access_cookie_max_age": self.AUTH_ACCESS_COOKIE_MAX_AGE,
                "refresh_cookie_max_age": self.AUTH_REFRESH_COOKIE_MAX_AGE,
                "state_cookie_max_age": self.AUTH_STATE_COOKIE_MAX_AGE,
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Create one settings object per process."""

    return Settings()


settings = get_settings()
