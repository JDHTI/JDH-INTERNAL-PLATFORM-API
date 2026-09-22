import pytest
from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Environment, Settings
from app.modules.health.application.service import HealthService


@pytest.mark.asyncio
async def test_global_and_v1_containers_are_composed_separately() -> None:
    container = build_application_container(Settings(ENVIRONMENT=Environment.TEST))
    resources = await initialize_global_resources(container)

    health_service = container.v1_container.health_service()

    assert isinstance(health_service, HealthService)
    assert health_service.database is resources.database
    assert health_service.redis is resources.redis
    assert health_service.storage is resources.storage
    assert health_service.data_provider is resources.data_provider

    await shutdown_global_resources(resources)
