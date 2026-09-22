"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.infrastructure.database import Database
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.health.application.service import HealthService


class V1Container(containers.DeclarativeContainer):
    """Version-scoped services and their infrastructure contracts."""

    database = providers.Dependency(instance_of=Database)
    redis = providers.Dependency(instance_of=RedisClient)
    storage = providers.Dependency(instance_of=S3Storage)

    health_service = providers.Factory(
        HealthService,
        database=database,
        redis=redis,
        storage=storage,
    )
