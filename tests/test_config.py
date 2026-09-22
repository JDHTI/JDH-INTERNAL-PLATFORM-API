from app.core.config import Settings


def test_microsoft_entra_id_settings_are_loaded_as_typed_values() -> None:
    app_settings = Settings(
        AUTH_MICROSOFT_ENTRA_ID_SECRET="test-secret",
        AUTH_MICROSOFT_ENTRA_ID_ID="client-id",
        AUTH_MICROSOFT_ENTRA_ID_TENANT_ID="tenant-id",
    )

    assert app_settings.AUTH_MICROSOFT_ENTRA_ID_SECRET.get_secret_value() == "test-secret"
    assert app_settings.AUTH_MICROSOFT_ENTRA_ID_ID == "client-id"
    assert app_settings.AUTH_MICROSOFT_ENTRA_ID_TENANT_ID == "tenant-id"
