import pytest
from app.modules.areas.application.service import AreaService
from app.modules.documents.application.service import DocumentService
from app.modules.identity.application.service import IdentityService

from tests.modules.fakes import (
    FakeAreaRepository,
    FakeDocumentRepository,
    FakeStorage,
    FakeTokenVerifier,
    FakeUserRepository,
)

ADMIN_EMAIL = "analista.ti@jdh.com.co"


@pytest.fixture
def user_repo() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def identity(user_repo) -> IdentityService:
    return IdentityService(
        repository=user_repo, token_verifier=FakeTokenVerifier(), admin_emails=[ADMIN_EMAIL]
    )


@pytest.fixture
def area_repo() -> FakeAreaRepository:
    return FakeAreaRepository()


@pytest.fixture
def areas(area_repo, identity) -> AreaService:
    return AreaService(repository=area_repo, users=identity)


@pytest.fixture
def doc_repo() -> FakeDocumentRepository:
    return FakeDocumentRepository()


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def documents(doc_repo, storage, areas) -> DocumentService:
    return DocumentService(
        repository=doc_repo,
        storage=storage,
        areas=areas,
        max_upload_bytes=1024 * 1024,
        allowed_extensions=["pdf", "docx", "xlsx"],
        download_url_ttl=300,
    )


@pytest.fixture
async def admin(identity):
    return await _login(identity, ADMIN_EMAIL)


async def _login(identity: IdentityService, email: str):
    """Sign in through the dev bypass, enabled only for this call."""

    identity.dev_bypass = True
    try:
        return await identity.authenticate(None, dev_email=email)
    finally:
        identity.dev_bypass = False


@pytest.fixture
def login(identity):
    async def _do(email: str):
        return await _login(identity, email)

    return _do
