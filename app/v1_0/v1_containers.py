"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.data_provider import DataProviderConnection
from app.infrastructure.database import Database
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.consumption.application.cursor import ClientCursorCodec
from app.modules.consumption.application.service import ConsumptionService
from app.modules.consumption.infrastructure.repository import WarehouseClientRepository
from app.modules.health.application.service import HealthService
from app.modules.identity.application.service import IdentityService
from app.modules.identity.infrastructure.entra import EntraClient
from app.modules.identity.infrastructure.repository import IdentityRepository


class V1Container(containers.DeclarativeContainer):
    """Version-scoped services and their infrastructure contracts."""

    database = providers.Dependency(instance_of=Database)
    redis = providers.Dependency(instance_of=RedisClient)
    storage = providers.Dependency(instance_of=S3Storage)
    data_provider = providers.Dependency(instance_of=DataProviderConnection)
    settings = providers.Dependency(instance_of=Settings)

    health_service = providers.Factory(
        HealthService,
        database=database,
        redis=redis,
        storage=storage,
        data_provider=data_provider,
    )
    consumption_repository = providers.Factory(
        WarehouseClientRepository,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        view=settings.provided.DWH_CLIENTS_VIEW,
    )
    consumption_cursor_codec = providers.Factory(
        ClientCursorCodec,
        secret=providers.Callable(
            lambda secret: secret.get_secret_value(), settings.provided.DWH_CURSOR_SECRET
        ),
        provider_name=settings.provided.DWH_PROVIDER,
    )
    consumption_service = providers.Factory(
        ConsumptionService,
        data_source=consumption_repository,
        cursor_codec=consumption_cursor_codec,
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
