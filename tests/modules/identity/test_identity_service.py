import pytest
from app.core.config import Environment, Settings
from app.core.errors import UnauthenticatedError
from app.modules.identity.application.service import IdentityService
from app.modules.identity.domain.entities import VerifiedIdentity

from tests.modules.fakes import FakeTokenVerifier, FakeUserRepository


def make_service(repo, identity=None, dev_bypass=False) -> IdentityService:
    return IdentityService(
        repository=repo,
        token_verifier=FakeTokenVerifier(identity),
        admin_emails=["Analista.TI@jdh.com.co"],
        dev_bypass=dev_bypass,
        dev_user_email="usuario@jdh.com.co",
    )


async def test_request_without_token_is_rejected_unless_dev_bypass() -> None:
    with pytest.raises(UnauthenticatedError):
        await make_service(FakeUserRepository()).authenticate(None)

    principal = await make_service(FakeUserRepository(), dev_bypass=True).authenticate(None)
    assert principal.email == "usuario@jdh.com.co"
    assert principal.is_platform_admin is False


async def test_dev_bypass_honors_header_and_admin_list_case_insensitively() -> None:
    service = make_service(FakeUserRepository(), dev_bypass=True)
    principal = await service.authenticate(None, dev_email="ANALISTA.TI@jdh.com.co")
    assert principal.email == "analista.ti@jdh.com.co"
    assert principal.is_platform_admin is True


async def test_token_wins_over_dev_header_and_links_email_stub() -> None:
    repo = FakeUserRepository()
    stub = await repo.ensure_by_email("ana@jdh.com.co")
    identity = VerifiedIdentity(email="Ana@jdh.com.co", display_name="Ana", entra_object_id="oid-1")
    service = make_service(repo, identity=identity, dev_bypass=True)

    principal = await service.authenticate("valid", dev_email="otra@jdh.com.co")

    assert principal.user_id == stub.id
    assert repo.users[stub.id].entra_object_id == "oid-1"
    assert principal.display_name == "Ana"


async def test_invalid_token_is_rejected() -> None:
    with pytest.raises(UnauthenticatedError):
        await make_service(FakeUserRepository()).authenticate("forged")


def test_dev_bypass_is_refused_outside_dev() -> None:
    with pytest.raises(ValueError, match="AUTH_DEV_BYPASS"):
        Settings(ENVIRONMENT=Environment.PROD, AUTH_DEV_BYPASS=True)
    assert Settings(ENVIRONMENT=Environment.DEV, AUTH_DEV_BYPASS=True).AUTH_DEV_BYPASS
