from datetime import UTC, datetime, time

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_entra_endpoints_hang_from_the_tenant_and_the_issuer_has_v2() -> None:
    app_settings = Settings(_env_file=None, ENTRA_TENANT_ID="tenant")

    # {authority}/oauth2/v2.0/authorize is what Entra serves; /tenant/v2.0/oauth2/... is a 404.
    assert app_settings.entra_authority == "https://login.microsoftonline.com/tenant"
    assert app_settings.entra_issuer == "https://login.microsoftonline.com/tenant/v2.0"


def test_directory_graph_settings_fall_back_to_the_sso_tenant() -> None:
    app_settings = Settings(
        _env_file=None,
        ENTRA_TENANT_ID="sso-tenant",
        DIRECTORY_GRAPH_CLIENT_ID="client-id",
        DIRECTORY_GRAPH_CLIENT_SECRET="test-secret",
        DIRECTORY_CORPORATE_DOMAINS="JDH.com.co, filial.co",
    )

    directory = app_settings.container_config()["directory"]
    assert directory["tenant_id"] == "sso-tenant"
    assert directory["user_tenant_id"] == "sso-tenant"
    assert directory["client_secret"] == "test-secret"
    assert directory["sync_at"] == time(23, 59)
    assert directory["corporate_domains"] == ["jdh.com.co", "filial.co"]
    assert Settings(_env_file=None).container_config()["directory"]["user_tenant_id"] == (
        "development"
    )


def test_directory_sync_needs_credentials_and_a_valid_schedule() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DIRECTORY_SYNC_ENABLED=True)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DIRECTORY_SYNC_AT="medianoche")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DIRECTORY_SYNC_TIMEZONE="Bogota")

    ready = Settings(
        _env_file=None,
        DIRECTORY_SYNC_ENABLED=True,
        DIRECTORY_GRAPH_TENANT_ID="tenant",
        DIRECTORY_GRAPH_CLIENT_ID="app",
        DIRECTORY_GRAPH_CLIENT_SECRET="secret",
        DIRECTORY_SYNC_AT="02:30",
    )
    assert ready.directory_sync_at == time(2, 30)


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
