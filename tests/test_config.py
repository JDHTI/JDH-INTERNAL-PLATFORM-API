from datetime import UTC, datetime

import pytest
from app.core.config import Settings
from pydantic import ValidationError


def test_microsoft_entra_id_settings_are_loaded_as_typed_values() -> None:
    app_settings = Settings(
        AUTH_MICROSOFT_ENTRA_ID_SECRET="test-secret",
        AUTH_MICROSOFT_ENTRA_ID_ID="client-id",
        AUTH_MICROSOFT_ENTRA_ID_TENANT_ID="tenant-id",
    )

    assert app_settings.AUTH_MICROSOFT_ENTRA_ID_SECRET.get_secret_value() == "test-secret"
    assert app_settings.AUTH_MICROSOFT_ENTRA_ID_ID == "client-id"
    assert app_settings.AUTH_MICROSOFT_ENTRA_ID_TENANT_ID == "tenant-id"


def test_mail_intake_needs_credentials_to_be_switched_on() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, MAIL_INTAKE_ENABLED=True)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, MAIL_INTAKE_ENABLED=True, MAIL_GRAPH_CLIENT_ID="app")

    ready = Settings(
        _env_file=None,
        MAIL_INTAKE_ENABLED=True,
        ENTRA_TENANT_ID="tenant",
        MAIL_GRAPH_CLIENT_ID="app",
        MAIL_GRAPH_CLIENT_SECRET="secret",
    )
    mail = ready.container_config()["mail"]
    assert (mail["tenant_id"], mail["client_id"], mail["client_secret"]) == (
        "tenant",
        "app",
        "secret",
    )


def test_mail_intake_start_date_is_an_aware_datetime() -> None:
    assert Settings(_env_file=None).mail_intake_since is None
    naive = Settings(_env_file=None, MAIL_INTAKE_SINCE="2026-10-08T09:00:00")
    assert naive.mail_intake_since == datetime(2026, 10, 8, 9, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, MAIL_INTAKE_SINCE="ayer")


def test_pqrsf_notifications_need_graph_credentials_to_be_switched_on() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, PQRSF_NOTIFICATIONS_ENABLED=True)

    ready = Settings(
        _env_file=None,
        PQRSF_NOTIFICATIONS_ENABLED=True,
        PQRSF_UI_BASE_URL="http://localhost:3000",
        PQRSF_NOTIFY_REDIRECT="analista.ti@jdh.com.co",
        ENTRA_TENANT_ID="tenant",
        MAIL_GRAPH_CLIENT_ID="app",
        MAIL_GRAPH_CLIENT_SECRET="secret",
    )
    config = ready.container_config()["pqrsf"]
    assert config["notifications_enabled"] is True
    assert config["notify_redirect"] == "analista.ti@jdh.com.co"
    assert config["holidays_api_url"] == "https://date.nager.at/api/v3"
