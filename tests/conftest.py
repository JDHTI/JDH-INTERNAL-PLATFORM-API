import pytest


@pytest.fixture(autouse=True)
def _no_dev_bypass_from_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's .env may enable the dev bypass; tests build test/prod settings.

    Environment variables win over .env in pydantic-settings, so this keeps
    ``Settings(ENVIRONMENT=...)`` valid regardless of the local file.
    """

    monkeypatch.setenv("AUTH_DEV_BYPASS", "false")
