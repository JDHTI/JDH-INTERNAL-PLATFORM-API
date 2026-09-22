"""Dependency-injector composition root."""

from dependency_injector import containers, providers

from app.infrastructure.database import Database
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.health.application.service import HealthService


class ApplicationContainer(containers.DeclarativeContainer):
    """Application-wide providers shared by all API versions and modules."""

    config = providers.Configuration()

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
    storage = providers.Singleton(
        S3Storage,
        endpoint_url=config.s3.endpoint_url,
        region=config.s3.region,
        access_key_id=config.s3.access_key_id,
        secret_access_key=config.s3.secret_access_key,
        bucket=config.s3.bucket,
        connect_timeout=config.s3.connect_timeout,
    )

    health_service = providers.Factory(
        HealthService,
        database=database,
        redis=redis,
        storage=storage,
    )
