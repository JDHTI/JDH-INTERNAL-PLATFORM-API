from app.core.config import Environment, Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_liveness_and_request_id() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.DEV))
    with TestClient(app) as client:
        response = client.get("/api/health/live", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"] == "test-request"


def test_production_hides_api_documentation() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.PROD, DEBUG=True))

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    assert app.debug is False
    with TestClient(app) as client:
        assert client.get("/api/docs").status_code == 404
        assert client.get("/api/openapi.json").status_code == 404


def test_readiness_is_unavailable_without_dependencies() -> None:
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    with TestClient(app) as client:
        response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert set(response.json()["checks"]) == {"postgres", "redis", "s3", "datawarehouse"}
