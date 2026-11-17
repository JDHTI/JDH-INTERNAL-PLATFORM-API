"""Supporting documents of supports: one by one and by selection, over in-memory ports."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import ForbiddenError, InvalidInputError, NotFoundError
from app.modules.supplier_supports.domain.attachments import FileUpload
from app.modules.supplier_supports.domain.entities import SupportData
from tests.modules.supplier_supports.conftest import person

SHOT = FileUpload("correo.png", b"\x89PNG screenshot")


async def make_support(service, manager, sku: str, nit: str | None = None) -> object:
    data = SupportData(
        kind="tactico_cliente",
        calc_method="pct_costo",
        supplier_value=Decimal("0.05"),
        product_sku=sku,
        customer_nit=nit,
        reference_cost=Decimal("1000"),
    )
    return (await service.create(manager, data, supplier_code="233")).support.id


@pytest.fixture
async def trio(service, manager):
    return [await make_support(service, manager, sku) for sku in ("1128", "8749", "3474")]


async def test_a_file_for_one_support_is_stored_classified_and_listed(
    attachments, doc_store, manager, trio
) -> None:
    result = await attachments.attach(
        manager, support_ids=[trio[0]], files=[SHOT], tags=["correo"], description="Acuerdo"
    )

    assert result.supports == 1 and result.links_created == 1 and len(result.documents) == 1
    assert doc_store.tags == [["apoyos", "correo", "proveedor 233"]]
    listed = await attachments.list(trio[0])
    assert [item.document_id for item in listed] == result.documents
    assert listed[0].support_count == 1 and listed[0].linked_by == manager.email
    assert await attachments.list(trio[1]) == []


async def test_one_document_is_linked_to_every_support_a_search_finds(
    attachments, doc_store, manager, trio
) -> None:
    result = await attachments.attach(
        manager, search={"product_skus": ["1128", "8749"]}, files=[SHOT]
    )

    assert result.supports == 2 and result.links_created == 2 and len(doc_store.documents) == 1
    assert (await attachments.list(trio[0]))[0].support_count == 2
    assert await attachments.list(trio[2]) == []


async def test_ids_and_search_are_added_without_repeats(attachments, manager, trio) -> None:
    preview = await attachments.preview(
        manager, support_ids=[trio[0], trio[2]], search={"product_skus": ["1128", "8749"]}
    )

    assert preview.total == 3 and not preview.capped and set(preview.supports) == set(trio)


async def test_a_search_without_any_filter_is_not_a_selection(attachments, manager, trio) -> None:
    for search in ({}, {"include_voided": True}, {"product_skus": []}):
        with pytest.raises(InvalidInputError, match="al menos un filtro"):
            await attachments.preview(manager, search=search)
    with pytest.raises(InvalidInputError, match="ningún apoyo"):
        await attachments.preview(manager, search={"product_skus": ["999999"]})
    with pytest.raises(InvalidInputError, match="ningún apoyo"):
        await attachments.preview(manager)


async def test_a_selection_over_the_limit_can_be_previewed_but_not_attached(
    attachments, service, manager, trio
) -> None:
    for sku in ("1128", "8749", "3474"):
        await make_support(service, manager, sku, "800116299")  # 6 supports; the limit is 5

    preview = await attachments.preview(manager, search={"supplier_codes": ["233"]})
    assert preview.capped and preview.total == 5 and preview.limit == 5
    with pytest.raises(InvalidInputError, match="supera 5 apoyos"):
        await attachments.attach(manager, search={"supplier_codes": ["233"]}, files=[SHOT])


async def test_unknown_or_voided_supports_are_refused(attachments, service, manager, trio) -> None:
    with pytest.raises(InvalidInputError, match="no existen o están anulados"):
        await attachments.attach(manager, support_ids=[uuid4()], files=[SHOT])
    await service.void(manager, trio[0], expected_version=1, reason="duplicado")
    with pytest.raises(InvalidInputError, match="no existen o están anulados"):
        await attachments.attach(manager, support_ids=[trio[0]], files=[SHOT])


async def test_when_a_file_fails_nothing_is_kept(attachments, doc_store, manager, trio) -> None:
    doc_store.fail_on = "malo.exe"

    with pytest.raises(InvalidInputError, match="no permitido"):
        await attachments.attach(
            manager, support_ids=trio, files=[SHOT, FileUpload("malo.exe", b"x")]
        )

    assert doc_store.documents == {}  # the first file was removed too
    assert [await attachments.list(support) for support in trio] == [[], [], []]


async def test_documents_already_in_the_folder_can_be_linked_to_more_supports(
    attachments, doc_store, manager, trio
) -> None:
    first = await attachments.attach(manager, support_ids=[trio[0]], files=[SHOT])
    (document,) = first.documents

    again = await attachments.attach(manager, support_ids=trio, document_ids=[document])

    assert again.links_created == 2  # the first support already had it
    assert (await attachments.list(trio[2]))[0].support_count == 3
    doc_store.foreign.add(document)
    with pytest.raises(InvalidInputError, match="no está en la carpeta"):
        await attachments.attach(manager, support_ids=trio, document_ids=[document])
    with pytest.raises(NotFoundError):
        await attachments.attach(manager, support_ids=trio, document_ids=[uuid4()])


async def test_unlinking_keeps_the_document_and_its_other_supports(
    attachments, doc_store, manager, trio
) -> None:
    result = await attachments.attach(manager, support_ids=trio[:2], files=[SHOT])
    (document,) = result.documents

    await attachments.unlink(manager, trio[0], document)

    assert await attachments.list(trio[0]) == []
    assert (await attachments.list(trio[1]))[0].support_count == 1
    assert document in doc_store.documents
    with pytest.raises(NotFoundError):
        await attachments.unlink(manager, trio[0], document)


async def test_a_support_file_opens_inline_or_downloads_for_any_reader(
    attachments, manager, trio
) -> None:
    result = await attachments.attach(manager, support_ids=[trio[0]], files=[SHOT], name="Correo")
    (document,) = result.documents
    reader = person("lector@jdh.com.co")  # not a manager, not in the document manager's area

    attachment, link = await attachments.view(reader, trio[0], document)
    _, saved = await attachments.view(reader, trio[0], document, download=True)

    assert attachment.name == "Correo" and attachment.content_type == "image/png"
    assert link.url.endswith("inline") and saved.url.endswith("attachment")
    assert link.expires_in == 300 and link.filename == "Correo"


async def test_only_the_documents_of_that_support_can_be_opened(attachments, manager, trio) -> None:
    (document,) = (await attachments.attach(manager, support_ids=[trio[0]], files=[SHOT])).documents

    with pytest.raises(NotFoundError, match="no tiene ese documento"):
        await attachments.view(manager, trio[1], document)  # linked to another support
    with pytest.raises(NotFoundError, match="no tiene ese documento"):
        await attachments.view(manager, trio[0], uuid4())
    with pytest.raises(NotFoundError, match="Apoyo no encontrado"):
        await attachments.view(manager, uuid4(), document)


async def test_input_rules(attachments, manager, trio) -> None:
    with pytest.raises(InvalidInputError, match="al menos un archivo"):
        await attachments.attach(manager, support_ids=trio)
    with pytest.raises(InvalidInputError, match="solo puede usarse con un archivo"):
        await attachments.attach(manager, support_ids=trio, files=[SHOT, SHOT], name="Acuerdo")
    with pytest.raises(InvalidInputError, match="Máximo 10"):
        await attachments.attach(manager, support_ids=trio, files=[SHOT] * 11)
    with pytest.raises(NotFoundError):
        await attachments.list(uuid4())


async def test_only_managers_write(attachments, trio) -> None:
    outsider = person("otro@jdh.com.co")

    with pytest.raises(ForbiddenError):
        await attachments.attach(outsider, support_ids=trio, files=[SHOT])
    with pytest.raises(ForbiddenError):
        await attachments.preview(outsider, support_ids=trio)
    with pytest.raises(ForbiddenError):
        await attachments.unlink(outsider, trio[0], uuid4())
    assert await attachments.list(trio[0]) == []  # reading is open


async def test_many_suppliers_do_not_flood_the_document_with_tags(
    attachments, doc_store, manager, trio, monkeypatch
) -> None:
    async def many(ids):
        return [str(number) for number in range(11)]

    monkeypatch.setattr(attachments.attachments, "supplier_codes", many)

    await attachments.attach(manager, support_ids=trio, files=[SHOT])

    assert doc_store.tags == [["apoyos"]]
