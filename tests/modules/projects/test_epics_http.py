"""HTTP contract of the epic routes and the task status catalog."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.projects.application.live import ProjectLiveService
from tests.modules.projects.conftest import FakeLiveChannel
from tests.modules.projects.test_live_http import StubIdentity, StubProjects, settings  # noqa: F401


@pytest.fixture
def client(settings):  # noqa: F811
    app = create_app(settings)
    container = app.state.container.v1_container
    live = ProjectLiveService(channel=FakeLiveChannel(), presence_ttl=45)
    with (
        container.identity_service.override(StubIdentity(settings)),
        container.project_service.override(StubProjects()),
        container.project_live_service.override(live),
        TestClient(app) as test_client,
    ):
        test_client.cookies.set(settings.AUTH_ACCESS_COOKIE_NAME, "valid")
        yield test_client


def test_catalog_lists_task_statuses_in_board_order(client) -> None:
    response = client.get("/api/v1/areas/ti/projects/catalog")

    assert response.status_code == 200
    assert response.json()["task_statuses"] == [
        {"value": "backlog", "label": "Backlog"},
        {"value": "in_progress", "label": "En curso"},
        {"value": "pending_validation", "label": "Pendiente de validación"},
        {"value": "blocked", "label": "Bloqueado"},
        {"value": "done", "label": "Hecha"},
    ]


def test_epic_routes_replace_the_block_routes(client) -> None:
    paths = set(client.app.openapi()["paths"])

    assert "/api/v1/areas/{slug}/projects/{project_id}/epics" in paths
    assert "/api/v1/areas/{slug}/project-epics/{epic_id}" in paths
    assert not any("block" in path for path in paths)
