import hashlib

import pytest
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaError,
)
from app.modules.areas.application.dto import CreateAreaCommand
from app.modules.documents.application.dto import (
    SearchQuery,
    UpdateDocumentCommand,
    UploadCommand,
)
from app.modules.documents.domain.entities import (
    normalize_tags,
    split_filename,
    storage_key_for,
)


@pytest.fixture
async def setup(areas, documents, admin, login):
    """Area "bi" with Ana as member and an empty "Formatos" folder."""

    ana = await login("ana@jdh.com.co")
    area = await areas.create_area(CreateAreaCommand(slug="bi", name="BI"), admin)
    role = await areas.create_role("bi", "Analista BI", admin)
    await areas.add_member("bi", ana.email, role.id, admin)
    folder = await documents.create_folder("bi", "Formatos", None, ana)
    return area, folder.folder, ana


def file(content: bytes = b"%PDF-1.7", filename: str = "Formato.pdf", **kw) -> UploadCommand:
    return UploadCommand(filename=filename, content=content, **kw)


async def test_upload_stores_under_id_key_and_indexes(documents, storage, setup) -> None:
    area, folder, ana = setup
    content = b"%PDF-1.7 vinculacion"

    doc = await documents.upload(
        "bi",
        folder.id,
        file(content, r"C:\fakepath\Vinculación.PDF", tags="Proveedores, vinculación,proveedores"),
        ana,
    )

    assert doc.storage_key == storage_key_for(area.id, folder.id, doc.id, "pdf")
    assert storage.objects[doc.storage_key] == (content, "application/pdf")
    assert (doc.name, doc.original_filename) == ("Vinculación.PDF", "Vinculación.PDF")
    assert doc.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert doc.tags == ["proveedores", "vinculación"]
    assert doc.uploaded_by == ana.email


@pytest.mark.parametrize(
    ("command", "error"),
    [
        (file(b"MZ", "virus.exe"), UnsupportedMediaError),
        (file(b"x" * (1024 * 1024 + 1)), PayloadTooLargeError),
        (file(b""), InvalidInputError),
        (file(b"x", "sin_extension"), InvalidInputError),
    ],
)
async def test_upload_validations(documents, setup, command, error) -> None:
    _, folder, ana = setup
    with pytest.raises(error):
        await documents.upload("bi", folder.id, command, ana)


async def test_duplicate_name_is_rejected_before_touching_storage(
    documents, storage, setup
) -> None:
    _, folder, ana = setup
    await documents.upload("bi", folder.id, file(name="Formato"), ana)
    with pytest.raises(ConflictError):
        await documents.upload("bi", folder.id, file(name="formato"), ana)
    assert len(storage.objects) == 1


async def test_failed_indexing_removes_the_stored_object(
    documents, doc_repo, storage, setup
) -> None:
    _, folder, ana = setup
    doc_repo.fail_next_add = ConflictError("Ya existe un documento con ese nombre en la carpeta")
    with pytest.raises(ConflictError):
        await documents.upload("bi", folder.id, file(), ana)
    assert storage.objects == {}
    assert len(storage.deleted) == 1


async def test_outsiders_cannot_touch_the_area(documents, setup, login) -> None:
    _, folder, ana = setup
    doc = await documents.upload("bi", folder.id, file(), ana)
    luis = await login("luis@jdh.com.co")

    for call in (
        documents.list_folders("bi", luis),
        documents.upload("bi", folder.id, file(filename="otro.pdf"), luis),
        documents.get_document(doc.id, luis),
        documents.download_link(doc.id, luis),
        documents.delete_document(doc.id, luis),
        documents.search(SearchQuery(area_slug="bi"), luis),
    ):
        with pytest.raises(ForbiddenError):
            await call


async def test_folder_of_another_area_is_not_found(areas, documents, admin, setup) -> None:
    _, folder, _ = setup
    await areas.create_area(CreateAreaCommand(slug="mercadeo", name="Mercadeo"), admin)
    with pytest.raises(NotFoundError):
        await documents.upload("mercadeo", folder.id, file(), admin)


async def test_folder_with_documents_cannot_be_deleted(documents, setup) -> None:
    _, folder, ana = setup
    await documents.upload("bi", folder.id, file(), ana)
    with pytest.raises(ConflictError):
        await documents.delete_folder("bi", folder.id, ana)


async def test_delete_removes_object_then_entry(documents, doc_repo, storage, setup) -> None:
    _, folder, ana = setup
    doc = await documents.upload("bi", folder.id, file(), ana)
    await documents.delete_document(doc.id, ana)
    assert storage.deleted == [doc.storage_key]
    assert doc.id not in doc_repo.documents


async def test_rename_keeps_storage_key_and_download_uses_new_name(documents, setup) -> None:
    _, folder, ana = setup
    doc = await documents.upload("bi", folder.id, file(filename="f.xlsx"), ana)
    key = doc.storage_key

    updated = await documents.update_document(
        doc.id,
        UpdateDocumentCommand(
            fields=frozenset({"name", "tags"}), name="Plantilla cartera", tags=["Cartera"]
        ),
        ana,
    )
    assert (updated.name, updated.tags, updated.storage_key) == (
        "Plantilla cartera",
        ["cartera"],
        key,
    )
    link = await documents.download_link(doc.id, ana)
    assert link.filename == "Plantilla cartera.xlsx"
    assert link.expires_in == 300


async def test_search_scope_depends_on_the_caller(
    areas, documents, doc_repo, admin, login, setup
) -> None:
    area, _, ana = setup
    await documents.search(SearchQuery(), admin)
    assert doc_repo.last_filters.area_ids is None

    await documents.search(SearchQuery(tags="Formatos, Proveedores"), ana)
    assert doc_repo.last_filters.area_ids == [area.id]
    assert list(doc_repo.last_filters.tags) == ["formatos", "proveedores"]

    nobody = await login("nadie@jdh.com.co")
    page = await documents.search(SearchQuery(), nobody)
    assert doc_repo.last_filters.area_ids == []
    assert page.total == 0


def test_split_filename_strips_client_paths() -> None:
    assert split_filename("../../etc/passwd.txt") == ("passwd.txt", "txt")
    assert split_filename(r"C:\Users\x\Informe.XLSX") == ("Informe.XLSX", "xlsx")
    for bad in ("", None, "sinext", ".env"):
        with pytest.raises(InvalidInputError):
            split_filename(bad)


def test_normalize_tags_limits() -> None:
    assert normalize_tags(" A , b ,a,, ") == ["a", "b"]
    with pytest.raises(InvalidInputError):
        normalize_tags(",".join(f"t{i}" for i in range(21)))
    with pytest.raises(InvalidInputError):
        normalize_tags(["x" * 51])
