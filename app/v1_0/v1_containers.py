"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.infrastructure.database import Database
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.repository import SqlAlchemyAreaRepository
from app.modules.documents.application.service import DocumentService
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from app.modules.health.application.service import HealthService
from app.modules.identity.application.service import IdentityService
from app.modules.identity.infrastructure.entra import EntraTokenVerifier
from app.modules.identity.infrastructure.repository import SqlAlchemyUserRepository


class V1Container(containers.DeclarativeContainer):
    """Version-scoped services and their infrastructure contracts."""

    config = providers.Configuration()

    database = providers.Dependency(instance_of=Database)
    redis = providers.Dependency(instance_of=RedisClient)
    storage = providers.Dependency(instance_of=S3Storage)

    health_service = providers.Factory(
        HealthService,
        database=database,
        redis=redis,
        storage=storage,
    )

    # --- Identity ---
    # Singleton: PyJWT caches the tenant signing keys inside the verifier.
    token_verifier = providers.Singleton(
        EntraTokenVerifier,
        tenant_id=config.auth.tenant_id,
        client_id=config.auth.client_id,
    )
    user_repository = providers.Factory(SqlAlchemyUserRepository, database=database)
    identity_service = providers.Factory(
        IdentityService,
        repository=user_repository,
        token_verifier=token_verifier,
        admin_emails=config.auth.admin_emails,
        dev_bypass=config.auth.dev_bypass,
        dev_user_email=config.auth.dev_user_email,
    )

    # --- Areas ---
    area_repository = providers.Factory(SqlAlchemyAreaRepository, database=database)
    area_service = providers.Factory(
        AreaService,
        repository=area_repository,
        users=identity_service,
    )

    # --- Documents (segmented by area) ---
    document_repository = providers.Factory(SqlAlchemyDocumentRepository, database=database)
    document_service = providers.Factory(
        DocumentService,
        repository=document_repository,
        storage=storage,
        areas=area_service,
        max_upload_bytes=config.documents.max_upload_bytes,
        allowed_extensions=config.documents.allowed_extensions,
        download_url_ttl=config.documents.download_url_ttl,
    )
