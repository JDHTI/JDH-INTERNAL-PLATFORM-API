import pytest

from app.modules.areas.application.service import AreaService
from app.modules.documents.application.service import DocumentService
from tests.modules.fakes import (
    FakeAreaRepository,
    FakeDirectory,
    FakeDocumentRepository,
    FakeStorage,
)

ADMIN_EMAIL = "analista.ti@jdh.com.co"


@pytest.fixture
def directory() -> FakeDirectory:
    return FakeDirectory()


@pytest.fixture
def area_repo() -> FakeAreaRepository:
    return FakeAreaRepository()


@pytest.fixture
def areas(area_repo, directory) -> AreaService:
    return AreaService(repository=area_repo, users=directory)


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
async def admin(directory):
    """A platform administrator (identity role ``admin``) who has signed in."""

    return directory.sign_in(ADMIN_EMAIL, admin=True)


@pytest.fixture
def login(directory):
    """Sign a regular user in, which is what makes them known to the directory."""

    async def _do(email: str):
        return directory.sign_in(email)

    return _do
