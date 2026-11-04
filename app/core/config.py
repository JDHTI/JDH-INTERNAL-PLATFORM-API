"""Typed application configuration loaded from environment variables."""

import re
from datetime import UTC, datetime, time
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    AUTH_DEV_SEED_ANALYST_EMAIL: str = "analista.ti@jdh.com.co"
    AUTH_DEV_SEED_TENANT_ID: str = ""
    AUTH_DEV_SEED_MOCK_PREFIX: str = "development-mock"
    # The single platform owner (global role ``owner``), set by migration 0041 and
    # scripts/set-owner.py. Never assignable through the API.
    PLATFORM_OWNER_EMAIL: str = "analista.ti@jdh.com.co"
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

    # Public and administrative API protection. Values are intentionally
    # separate so each module can choose a tolerance profile without code edits.
    # Three levels per client address, applied to every endpoint by app/core/rate_policy.py:
    # read (queries), relaxed (changes) and strict (costly or open to the public).
    RATE_LIMIT_READ_REQUESTS: int = 600
    RATE_LIMIT_READ_WINDOW_SECONDS: int = 60
    RATE_LIMIT_RELAXED_REQUESTS: int = 120
    RATE_LIMIT_RELAXED_WINDOW_SECONDS: int = 60
    RATE_LIMIT_STRICT_REQUESTS: int = 5
    RATE_LIMIT_STRICT_WINDOW_SECONDS: int = 600
    RATE_LIMIT_TRUSTED_PROXY_IPS: str = ""
    # Public forms (POST /public/*): one budget for every visitor together, so a flood from
    # many addresses cannot get past the per-address limit; and a cap on the request body,
    # checked before it is read.
    RATE_LIMIT_PUBLIC_GLOBAL_REQUESTS: int = 100
    RATE_LIMIT_PUBLIC_GLOBAL_WINDOW_SECONDS: int = 3600
    # Room for the PQRSF evidence: 25 MB of images plus a 20 MB video and the form fields.
    PUBLIC_MAX_BODY_MB: int = 50
    # Cloudflare Turnstile secret; empty leaves the CAPTCHA off. When set, public POSTs must
    # carry a valid token in the X-Captcha-Token header.
    PUBLIC_CAPTCHA_SECRET: SecretStr = SecretStr("")
    PUBLIC_CAPTCHA_VERIFY_URL: str = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    S3_ENDPOINT_URL: str = "http://127.0.0.1:9000"
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: SecretStr = SecretStr("minioadmin")
    S3_BUCKET: str = "internal-platform"
    S3_CONNECT_TIMEOUT_SECONDS: float = 2.0
    # Host used to sign download URLs. The signature covers the host, so it
    # must be the one the browser reaches; empty means S3_ENDPOINT_URL.
    S3_PUBLIC_ENDPOINT_URL: str = ""

    # Employee directory: a nightly copy of the Entra users (Microsoft Graph, application
    # permission User.Read.All). The app registration may differ from the SSO one; an
    # empty tenant falls back to ENTRA_TENANT_ID.
    DIRECTORY_GRAPH_TENANT_ID: str = ""
    DIRECTORY_GRAPH_CLIENT_ID: str = ""
    DIRECTORY_GRAPH_CLIENT_SECRET: SecretStr = SecretStr("")
    # The background sync runs every day at this local time (HH:MM in the timezone).
    DIRECTORY_SYNC_ENABLED: bool = False
    DIRECTORY_SYNC_AT: str = "23:59"
    DIRECTORY_SYNC_TIMEZONE: str = "America/Bogota"
    # At start-up, sync once when no sync succeeded in the last 24 hours.
    DIRECTORY_SYNC_CATCH_UP: bool = True
    # Users whose manager and photo are read from Graph at the same time.
    DIRECTORY_SYNC_BATCH_SIZE: int = 10
    # Mail domains (comma-separated) of the employees the directory shows by default.
    DIRECTORY_CORPORATE_DOMAINS: str = "jdh.com.co"

    # Document management
    DOCUMENTS_MAX_UPLOAD_MB: int = 25
    DOCUMENTS_ALLOWED_EXTENSIONS: str = (
        "pdf,doc,docx,xls,xlsx,xlsm,csv,ppt,pptx,txt,png,jpg,jpeg,zip"
    )
    DOCUMENTS_DOWNLOAD_URL_TTL_SECONDS: int = 300

    # Public PQRSF uploads are stricter than the internal document manager.
    PQRSF_MAX_ATTACHMENTS: int = 5
    PQRSF_MAX_ATTACHMENT_MB: int = 5
    PQRSF_MAX_TOTAL_ATTACHMENT_MB: int = 25
    PQRSF_MAX_IMAGE_PIXELS: int = 20_000_000
    # Evidence videos are not re-encoded: few and short.
    PQRSF_MAX_VIDEOS: int = 1
    PQRSF_MAX_VIDEO_MB: int = 20
    PQRSF_CLAMAV_HOST: str = "clamav"
    PQRSF_CLAMAV_PORT: int = 3310
    PQRSF_CLAMAV_TIMEOUT_SECONDS: float = 10.0
    # Members of this area (plus platform admins) manage PQRSF follow-up.
    PQRSF_MANAGER_AREA_SLUG: str = "calidad"
    PQRSF_TIMEZONE: str = "America/Bogota"
    PQRSF_HOLIDAYS_COUNTRY: str = "CO"
    # Public holidays API for the business-day check of the timers (Nager.Date, no key).
    PQRSF_HOLIDAYS_API_URL: str = "https://date.nager.at/api/v3"
    # Emails sent from the PQRSF mailbox: assignment notices and the two timers.
    PQRSF_NOTIFICATIONS_ENABLED: bool = False
    # Frontend root used for links in the emails (blank leaves the links out).
    PQRSF_UI_BASE_URL: str = ""
    # Testing: send every PQRSF email to this address instead of its recipient.
    PQRSF_NOTIFY_REDIRECT: str = ""
    # Automatic thank-yous one address can get per 24 hours (suggestions and compliments).
    PQRSF_FEEDBACK_REPLIES_PER_EMAIL_PER_DAY: int = 3
    # Certificates answered automatically come from files of this area of the document manager.
    PQRSF_CERTIFICATES_AREA_SLUG: str = "calidad"
    # Emails with certificates one address receives per 24 hours, and attachments per email.
    PQRSF_DOCUMENT_EMAILS_PER_ADDRESS_PER_DAY: int = 5
    PQRSF_DOCUMENT_EMAIL_MAX_MB: int = 15

    # Members of this area (plus platform admins) manage supplier supports.
    SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG: str = "bi"
    # Largest file, and most rows, of a bulk load of supports.
    SUPPLIER_SUPPORTS_MAX_UPLOAD_MB: int = 10
    SUPPLIER_SUPPORTS_MAX_BULK_ROWS: int = 2000
    # Area (document manager) and folder that keep the supporting files of supports: the
    # supports master belongs to BI, so its proofs are listed in BI's document manager. The
    # supports managers file them there even when they are not members of that area.
    SUPPLIER_SUPPORTS_DOCUMENTS_AREA_SLUG: str = "bi"
    SUPPLIER_SUPPORTS_DOCUMENTS_FOLDER: str = "soporte_apoyos"
    # Most supports one upload may be linked to.
    SUPPLIER_SUPPORTS_MAX_ATTACH_SUPPORTS: int = 1000
    # Rows per DWH page and per upsert when syncing the commercial masters.
    MASTERS_SYNC_BATCH_SIZE: int = 2000
    # Largest margin file a manager can upload to update the product margins.
    PRODUCT_MARGINS_MAX_UPLOAD_MB: int = 20

    # Members of this area (plus platform admins) upload retained orders files.
    ORDER_VALIDATION_MANAGER_AREA_SLUG: str = "comercial"
    ORDER_VALIDATION_MAX_UPLOAD_MB: int = 10
    ORDER_VALIDATION_MAX_LINES: int = 10_000
    # Order-level release rules, in margin points / percent: a line more than
    # MAX_POINTS_BELOW_TARGET points below its target holds the order back, and an order at
    # TOLERANCE_MIN_ORDER_MARGIN % or more is released with up to TOLERANCE_MAX_LINES lines
    # below their target (each within those points).
    ORDER_VALIDATION_MAX_POINTS_BELOW_TARGET: Decimal = Decimal("4")
    ORDER_VALIDATION_TOLERANCE_MIN_ORDER_MARGIN: Decimal = Decimal("18")
    ORDER_VALIDATION_TOLERANCE_MAX_LINES: int = 2

    # Quoter: members of this area (plus platform admins) quote customers' price lists. A new
    # price may leave the margin at most MAX_POINTS_BELOW_TARGET points below the target.
    QUOTATIONS_MANAGER_AREA_SLUG: str = "comercial"
    # Members of this area (plus admins) validate a confirmed price list; then its files are
    # released. BI keeps the price lists and the margin master.
    QUOTATIONS_APPROVER_AREA_SLUG: str = "bi"
    # Largest quotation workbook accepted back (bulk prices and margins).
    QUOTATIONS_MAX_UPLOAD_MB: int = 10
    QUOTATIONS_MAX_POINTS_BELOW_TARGET: Decimal = Decimal("1")
    QUOTATIONS_MAX_LINES: int = 3000
    # Seconds per warehouse query of the quoter (the pool default is DWH_COMMAND_TIMEOUT).
    QUOTATIONS_DWH_TIMEOUT_SECONDS: float = 30.0
    # SIESA company written in the import file (``f_cia``).
    QUOTATIONS_SIESA_COMPANY: str = "001"

    # Microsoft Graph mail (application permissions, no mailbox passwords). The
    # app registration is separate from the SSO one; scope it to the mailboxes
    # with an Exchange Application Access Policy.
    MAIL_INTAKE_ENABLED: bool = False
    # Empty falls back to ENTRA_TENANT_ID.
    MAIL_GRAPH_TENANT_ID: str = ""
    MAIL_GRAPH_CLIENT_ID: str = ""
    MAIL_GRAPH_CLIENT_SECRET: SecretStr = SecretStr("")
    # Mailbox whose inbox becomes PQRSF cases; its replies leave from it too.
    MAIL_PQRSF_INBOX: str = "pqrsf@jdh.com.co"
    # Extra addresses (comma-separated) whose mail never opens a case.
    MAIL_IGNORED_SENDERS: str = "info@jdh.com.co"
    # ISO date-time. On the first sync only mail received from then on opens cases;
    # empty means "from the moment it is switched on" (the old inbox is left alone).
    MAIL_INTAKE_SINCE: str = ""
    MAIL_POLL_INTERVAL_SECONDS: int = 120
    MAIL_MAX_ATTEMPTS: int = 5
    MAIL_MAX_ATTACHMENTS: int = 10
    MAIL_MAX_ATTACHMENT_MB: int = 10
    MAIL_MAX_TOTAL_ATTACHMENT_MB: int = 25
    MAIL_ATTACHMENT_EXTENSIONS: str = "pdf,doc,docx,xls,xlsx,csv,txt,ppt,pptx,png,jpg,jpeg,webp"

    # Data warehouse monitoring (TI → Monitoreo → Datawarehouse): a background loop copies
    # the ``monitoreo`` schema over the DWH_* connection; the screens read the copy.
    DWH_MONITORING_SYNC_ENABLED: bool = False
    DWH_MONITORING_SYNC_INTERVAL_SECONDS: int = 600

    # Application monitoring (TI → Monitoreo → Aplicativos): a background loop requests the
    # health endpoint and the UI of every registered platform; checks older than the
    # retention are purged.
    APP_MONITORING_ENABLED: bool = False
    APP_MONITORING_INTERVAL_SECONDS: int = 60
    APP_MONITORING_RETENTION_DAYS: int = 30

    # Areas where the project manager is switched on (comma-separated slugs).
    PROJECTS_AREA_SLUGS: str = "ti"
    # A live connection without a ping for this long leaves the presence list.
    PROJECTS_PRESENCE_TTL_SECONDS: int = 45

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
        return self

    @model_validator(mode="after")
    def validate_mail_settings(self) -> "Settings":
        """Reject a switched-on mail intake that cannot authenticate or has a bad start date."""

        if self.MAIL_INTAKE_SINCE.strip():
            try:
                datetime.fromisoformat(self.MAIL_INTAKE_SINCE.strip())
            except ValueError as exc:
                raise ValueError("MAIL_INTAKE_SINCE must be an ISO date-time") from exc
        if (self.MAIL_INTAKE_ENABLED or self.PQRSF_NOTIFICATIONS_ENABLED) and not (
            (self.MAIL_GRAPH_TENANT_ID or self.ENTRA_TENANT_ID)
            and self.MAIL_GRAPH_CLIENT_ID
            and self.MAIL_GRAPH_CLIENT_SECRET.get_secret_value()
        ):
            raise ValueError(
                "MAIL_INTAKE_ENABLED and PQRSF_NOTIFICATIONS_ENABLED need a tenant, "
                "MAIL_GRAPH_CLIENT_ID and MAIL_GRAPH_CLIENT_SECRET"
            )
        return self

    @model_validator(mode="after")
    def validate_directory_settings(self) -> "Settings":
        """Reject a switched-on directory sync that cannot authenticate or has a bad time."""

        try:
            _ = self.directory_sync_at
        except ValueError as exc:
            raise ValueError("DIRECTORY_SYNC_AT must be HH:MM") from exc
        try:
            ZoneInfo(self.DIRECTORY_SYNC_TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("DIRECTORY_SYNC_TIMEZONE must be an IANA timezone") from exc
        if self.DIRECTORY_SYNC_BATCH_SIZE < 1:
            raise ValueError("DIRECTORY_SYNC_BATCH_SIZE must be positive")
        if self.DIRECTORY_SYNC_ENABLED and not self.directory_graph_configured:
            raise ValueError(
                "DIRECTORY_SYNC_ENABLED needs a tenant, DIRECTORY_GRAPH_CLIENT_ID and "
                "DIRECTORY_GRAPH_CLIENT_SECRET"
            )
        return self

    @property
    def directory_sync_at(self) -> time:
        """Local time of the daily directory sync."""

        return time.fromisoformat(self.DIRECTORY_SYNC_AT.strip())

    @property
    def directory_graph_configured(self) -> bool:
        """Whether the directory has the credentials to read Graph."""

        return bool(
            (self.DIRECTORY_GRAPH_TENANT_ID or self.ENTRA_TENANT_ID)
            and self.DIRECTORY_GRAPH_CLIENT_ID
            and self.DIRECTORY_GRAPH_CLIENT_SECRET.get_secret_value()
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mail_intake_since(self) -> datetime | None:
        """First-sync cutoff as an aware datetime (UTC when written without offset)."""

        text = self.MAIL_INTAKE_SINCE.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

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
                "public_endpoint_url": self.S3_PUBLIC_ENDPOINT_URL,
            },
            "documents": {
                "max_upload_bytes": self.DOCUMENTS_MAX_UPLOAD_MB * 1024 * 1024,
                "allowed_extensions": _split_lower(self.DOCUMENTS_ALLOWED_EXTENSIONS),
                "download_url_ttl": self.DOCUMENTS_DOWNLOAD_URL_TTL_SECONDS,
            },
            "pqrsf": {
                "max_attachments": self.PQRSF_MAX_ATTACHMENTS,
                "max_attachment_bytes": self.PQRSF_MAX_ATTACHMENT_MB * 1024 * 1024,
                "max_total_attachment_bytes": self.PQRSF_MAX_TOTAL_ATTACHMENT_MB * 1024 * 1024,
                "max_image_pixels": self.PQRSF_MAX_IMAGE_PIXELS,
                "max_videos": self.PQRSF_MAX_VIDEOS,
                "max_video_bytes": self.PQRSF_MAX_VIDEO_MB * 1024 * 1024,
                "clamav_host": self.PQRSF_CLAMAV_HOST,
                "clamav_port": self.PQRSF_CLAMAV_PORT,
                "clamav_timeout": self.PQRSF_CLAMAV_TIMEOUT_SECONDS,
                "manager_area_slug": self.PQRSF_MANAGER_AREA_SLUG,
                "timezone": self.PQRSF_TIMEZONE,
                "holidays_country": self.PQRSF_HOLIDAYS_COUNTRY,
                "holidays_api_url": self.PQRSF_HOLIDAYS_API_URL,
                "notifications_enabled": self.PQRSF_NOTIFICATIONS_ENABLED,
                "ui_base_url": self.PQRSF_UI_BASE_URL,
                "notify_redirect": self.PQRSF_NOTIFY_REDIRECT,
                "feedback_replies_per_email": self.PQRSF_FEEDBACK_REPLIES_PER_EMAIL_PER_DAY,
                "certificates_area_slug": self.PQRSF_CERTIFICATES_AREA_SLUG,
                "document_emails_per_address": self.PQRSF_DOCUMENT_EMAILS_PER_ADDRESS_PER_DAY,
                "document_email_max_bytes": self.PQRSF_DOCUMENT_EMAIL_MAX_MB * 1024 * 1024,
            },
            "mail": {
                "tenant_id": self.MAIL_GRAPH_TENANT_ID or self.ENTRA_TENANT_ID,
                "client_id": self.MAIL_GRAPH_CLIENT_ID,
                "client_secret": self.MAIL_GRAPH_CLIENT_SECRET.get_secret_value(),
                "pqrsf_inbox": self.MAIL_PQRSF_INBOX,
                "since": self.mail_intake_since,
                "ignored_senders": _split_lower(self.MAIL_IGNORED_SENDERS),
                "max_attempts": self.MAIL_MAX_ATTEMPTS,
                "max_attachments": self.MAIL_MAX_ATTACHMENTS,
                "max_attachment_bytes": self.MAIL_MAX_ATTACHMENT_MB * 1024 * 1024,
                "max_total_attachment_bytes": self.MAIL_MAX_TOTAL_ATTACHMENT_MB * 1024 * 1024,
                "attachment_extensions": _split_lower(self.MAIL_ATTACHMENT_EXTENSIONS),
            },
            "supplier_supports": {
                "manager_area_slug": self.SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG,
                "max_upload_bytes": self.SUPPLIER_SUPPORTS_MAX_UPLOAD_MB * 1024 * 1024,
                "max_bulk_rows": self.SUPPLIER_SUPPORTS_MAX_BULK_ROWS,
                "documents_area_slug": self.SUPPLIER_SUPPORTS_DOCUMENTS_AREA_SLUG,
                "documents_folder": self.SUPPLIER_SUPPORTS_DOCUMENTS_FOLDER,
                "max_attach_supports": self.SUPPLIER_SUPPORTS_MAX_ATTACH_SUPPORTS,
            },
            "masters_sync": {"batch_size": self.MASTERS_SYNC_BATCH_SIZE},
            "product_margins": {
                "max_upload_bytes": self.PRODUCT_MARGINS_MAX_UPLOAD_MB * 1024 * 1024,
            },
            "order_validation": {
                "manager_area_slug": self.ORDER_VALIDATION_MANAGER_AREA_SLUG,
                "max_upload_bytes": self.ORDER_VALIDATION_MAX_UPLOAD_MB * 1024 * 1024,
                "max_lines": self.ORDER_VALIDATION_MAX_LINES,
                "max_points_below_target": self.ORDER_VALIDATION_MAX_POINTS_BELOW_TARGET / 100,
                "tolerance_min_order_margin": self.ORDER_VALIDATION_TOLERANCE_MIN_ORDER_MARGIN
                / 100,
                "tolerance_max_lines": self.ORDER_VALIDATION_TOLERANCE_MAX_LINES,
            },
            "quotations": {
                "manager_area_slug": self.QUOTATIONS_MANAGER_AREA_SLUG,
                "approver_area_slug": self.QUOTATIONS_APPROVER_AREA_SLUG,
                "max_upload_bytes": self.QUOTATIONS_MAX_UPLOAD_MB * 1024 * 1024,
                "max_points_below_target": self.QUOTATIONS_MAX_POINTS_BELOW_TARGET / 100,
                "max_lines": self.QUOTATIONS_MAX_LINES,
                "dwh_timeout": self.QUOTATIONS_DWH_TIMEOUT_SECONDS,
                "siesa_company": self.QUOTATIONS_SIESA_COMPANY,
            },
            "dwh_monitoring": {
                "sync_enabled": self.DWH_MONITORING_SYNC_ENABLED,
                "sync_interval": self.DWH_MONITORING_SYNC_INTERVAL_SECONDS,
            },
            "app_monitoring": {
                "enabled": self.APP_MONITORING_ENABLED,
                "interval": self.APP_MONITORING_INTERVAL_SECONDS,
                "retention_days": self.APP_MONITORING_RETENTION_DAYS,
            },
            "projects": {
                "area_slugs": _split_lower(self.PROJECTS_AREA_SLUGS),
                "presence_ttl": self.PROJECTS_PRESENCE_TTL_SECONDS,
            },
            "directory": {
                "tenant_id": self.DIRECTORY_GRAPH_TENANT_ID or self.ENTRA_TENANT_ID,
                "client_id": self.DIRECTORY_GRAPH_CLIENT_ID,
                "client_secret": self.DIRECTORY_GRAPH_CLIENT_SECRET.get_secret_value(),
                # Users are provisioned in the SSO tenant, the one sign-in uses.
                "user_tenant_id": self.ENTRA_TENANT_ID or "development",
                "sync_enabled": self.DIRECTORY_SYNC_ENABLED,
                "sync_at": self.directory_sync_at,
                "timezone": self.DIRECTORY_SYNC_TIMEZONE,
                "catch_up": self.DIRECTORY_SYNC_CATCH_UP,
                "batch_size": self.DIRECTORY_SYNC_BATCH_SIZE,
                "corporate_domains": _split_lower(self.DIRECTORY_CORPORATE_DOMAINS),
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
                "dev_seed_analyst_email": self.AUTH_DEV_SEED_ANALYST_EMAIL,
                "dev_seed_tenant_id": self.AUTH_DEV_SEED_TENANT_ID,
                "dev_seed_mock_prefix": self.AUTH_DEV_SEED_MOCK_PREFIX,
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

    def rate_limit_policy(self, profile: str) -> tuple[int, int]:
        """Return the configured ``(limit, window_seconds)`` for a profile.

        Args:
            profile (str): One of ``read``, ``relaxed``, ``strict`` or ``public_global``.

        Returns:
            tuple[int, int]: Maximum requests and window duration.

        Raises:
            ValueError: When the profile is unknown or has invalid values.
        """

        policies = {
            "public_global": (
                self.RATE_LIMIT_PUBLIC_GLOBAL_REQUESTS,
                self.RATE_LIMIT_PUBLIC_GLOBAL_WINDOW_SECONDS,
            ),
            "strict": (self.RATE_LIMIT_STRICT_REQUESTS, self.RATE_LIMIT_STRICT_WINDOW_SECONDS),
            "read": (self.RATE_LIMIT_READ_REQUESTS, self.RATE_LIMIT_READ_WINDOW_SECONDS),
            "relaxed": (
                self.RATE_LIMIT_RELAXED_REQUESTS,
                self.RATE_LIMIT_RELAXED_WINDOW_SECONDS,
            ),
        }
        try:
            limit, window = policies[profile]
        except KeyError as exc:
            raise ValueError(f"Unknown rate limit profile: {profile}") from exc
        if limit < 1 or window < 1:
            raise ValueError(f"Rate limit profile {profile} must be positive")
        return limit, window

    @property
    def trusted_proxy_ips(self) -> frozenset[str]:
        """Return proxy addresses allowed to provide ``X-Forwarded-For``."""

        return frozenset(
            address.strip()
            for address in self.RATE_LIMIT_TRUSTED_PROXY_IPS.split(",")
            if address.strip()
        )


def _split_lower(value: str) -> list[str]:
    return [item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Create one settings object per process."""

    return Settings()


settings = get_settings()
