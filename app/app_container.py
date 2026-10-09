"""Global composition root and lifecycle for application resources."""

from __future__ import annotations

from dataclasses import dataclass

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.data_provider import DataProviderConnection
from app.infrastructure.database import Database
from app.infrastructure.realtime import RealtimeBroker
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.v1_0.v1_containers import V1Container


class ApplicationContainer(containers.DeclarativeContainer):
    """Global resources plus the versioned service containers."""

    config = providers.Configuration()
    settings = providers.Dependency(instance_of=Settings)

    database = providers.Singleton(
        Database,
        database_url=config.database.url,
        pool_size=config.database.pool_size,
        max_overflow=config.database.max_overflow,
        pool_timeout=config.database.pool_timeout,
        connect_timeout=config.database.connect_timeout,
    )
    redis = providers.Singleton(
        RedisClient,
        url=config.redis.url,
        key_prefix=config.redis.key_prefix,
        default_ttl=config.redis.default_ttl,
        connect_timeout=config.redis.connect_timeout,
    )
    # One Redis pub/sub reader per process, shared by every live connection.
    realtime = providers.Singleton(RealtimeBroker, redis=redis)
    storage = providers.Singleton(
        S3Storage,
        endpoint_url=config.s3.endpoint_url,
        region=config.s3.region,
        access_key_id=config.s3.access_key_id,
        secret_access_key=config.s3.secret_access_key,
        bucket=config.s3.bucket,
        connect_timeout=config.s3.connect_timeout,
        public_endpoint_url=config.s3.public_endpoint_url,
    )
    data_provider = providers.Singleton(
        DataProviderConnection,
        provider=config.data_provider.provider_name,
        enabled=config.data_provider.enabled,
        host=config.data_provider.host,
        port=config.data_provider.port,
        database=config.data_provider.database,
        user=config.data_provider.user,
        password=config.data_provider.password,
        min_size=config.data_provider.min_size,
        max_size=config.data_provider.max_size,
        connect_timeout=config.data_provider.connect_timeout,
        command_timeout=config.data_provider.command_timeout,
    )

    v1_container = providers.Container(
        V1Container,
        config=config,
        database=database,
        redis=redis,
        realtime=realtime,
        storage=storage,
        data_provider=data_provider,
        settings=settings,
    )


@dataclass(frozen=True)
class GlobalResources:
    """Concrete global resources initialized during the FastAPI lifespan."""

    database: Database
    redis: RedisClient
    storage: S3Storage
    data_provider: DataProviderConnection
    realtime: RealtimeBroker


def build_application_container(app_settings: Settings) -> ApplicationContainer:
    """Configure and wire one container instance for one application instance."""

    from app.modules.areas.presentation import router as areas_router_module
    from app.modules.consumption.presentation import router as consumption_router_module
    from app.modules.documents.presentation import router as documents_router_module
    from app.modules.health.presentation import router as health_router_module
    from app.modules.identity.presentation import router as identity_router_module
    from app.modules.masters.presentation import router as masters_router_module
    from app.modules.pqrsf.presentation import feedback_router as pqrsf_feedback_router_module
    from app.modules.pqrsf.presentation import router as pqrsf_router_module
    from app.modules.pqrsf.presentation import tracking_router as pqrsf_tracking_router_module
    from app.modules.projects.presentation import router as projects_router_module
    from app.modules.sites.presentation import router as sites_router_module

    container = ApplicationContainer()
    container.settings.override(app_settings)
    container.config.from_dict(app_settings.container_config())
    container.wire(
        modules=[
            health_router_module,
            identity_router_module,
            consumption_router_module,
            areas_router_module,
            documents_router_module,
            sites_router_module,
            masters_router_module,
            pqrsf_router_module,
            pqrsf_tracking_router_module,
            pqrsf_feedback_router_module,
            projects_router_module,
        ]
    )
    return container


async def initialize_global_resources(container: ApplicationContainer) -> GlobalResources:
    """Instantiate global clients without performing network probes."""

    database: Database | None = None
    redis: RedisClient | None = None
    storage: S3Storage | None = None
    data_provider: DataProviderConnection | None = None
    try:
        database = container.database()
        redis = container.redis()
        storage = container.storage()
        data_provider = container.data_provider()
        return GlobalResources(
            database=database,
            redis=redis,
            storage=storage,
            data_provider=data_provider,
            realtime=container.realtime(),
        )
    except BaseException:
        if redis is not None:
            await redis.close()
        if database is not None:
            await database.dispose()
        if storage is not None:
            storage.close()
        if data_provider is not None:
            await data_provider.close()
        raise


async def shutdown_global_resources(resources: GlobalResources) -> None:
    """Close global clients in dependency-safe order."""

    await resources.realtime.close()
    await resources.redis.close()
    await resources.database.dispose()
    resources.storage.close()
    await resources.data_provider.close()


__all__ = [
    "ApplicationContainer",
    "GlobalResources",
    "build_application_container",
    "initialize_global_resources",
    "shutdown_global_resources",
]
