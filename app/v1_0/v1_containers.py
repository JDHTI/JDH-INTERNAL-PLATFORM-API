"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.database import Database
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.health.application.service import HealthService
from app.modules.identity.application.service import IdentityService
from app.modules.identity.infrastructure.entra import EntraClient
from app.modules.identity.infrastructure.repository import IdentityRepository


class V1Container(containers.DeclarativeContainer):
    """Version-scoped services and their infrastructure contracts."""

    database = providers.Dependency(instance_of=Database)
    redis = providers.Dependency(instance_of=RedisClient)
    storage = providers.Dependency(instance_of=S3Storage)
    settings = providers.Dependency(instance_of=Settings)

    health_service = providers.Factory(
        HealthService,
        database=database,
        redis=redis,
        storage=storage,
    )
    identity_repository = providers.Factory(IdentityRepository, database=database)
    entra_client = providers.Singleton(
        EntraClient,
        tenant_id=settings.provided.ENTRA_TENANT_ID,
        client_id=settings.provided.ENTRA_CLIENT_ID,
        client_secret=providers.Callable(
            lambda secret: secret.get_secret_value(), settings.provided.ENTRA_CLIENT_SECRET
        ),
        audience=settings.provided.entra_api_audience,
        api_scope=settings.provided.ENTRA_API_SCOPE,
        redirect_uri=settings.provided.ENTRA_REDIRECT_URI,
        authority=settings.provided.entra_authority,
        issuer=settings.provided.entra_issuer,
    )
    identity_service = providers.Factory(
        IdentityService,
        repository=identity_repository,
        entra=entra_client,
        storage=storage,
        settings=settings,
    )
