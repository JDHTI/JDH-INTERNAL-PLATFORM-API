"""The list update template (2026-11-25): download it, delete rows, upload it back."""

from __future__ import annotations

import io

import openpyxl
import pytest

from app.core.errors import ConflictError, InvalidInputError
from app.modules.quotations.infrastructure.lines_workbook import TEMPLATE_COLUMNS
from tests.modules.quotations.conftest import LIST_MANAGER, NIT
from tests.modules.supplier_supports.conftest import person


@pytest.fixture
def lister():
    return person(LIST_MANAGER)


def _edited(content: bytes, drop: set[str] = frozenset(), **header: str) -> bytes:
    """The downloaded template with the rows of ``drop`` deleted and header rows changed."""

    book = openpyxl.load_workbook(io.BytesIO(content))
    lines = book["Lineas"]
    for index in range(lines.max_row, 1, -1):
        if str(lines.cell(index, 1).value) in drop:
            lines.delete_rows(index)
    for row in book["Cotizacion"].iter_rows():
        if row[0].value in header:
            row[1].value = header[row[0].value]
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _template(service, lister) -> tuple:
    detail = await service.create_list_update(lister, price_list_id="10U")
    file = await service.list_template_file(lister, detail.quotation.id)
    return detail, file


async def test_the_template_has_what_the_advisors_see_and_no_costs(service, lister) -> None:
    detail, file = await _template(service, lister)

    assert file.filename.startswith("Plantilla MAYORISTAS CENTRO ")
    book = openpyxl.load_workbook(io.BytesIO(file.content))
    lines = book["Lineas"]
    assert tuple(cell.value for cell in lines[1]) == TEMPLATE_COLUMNS
    assert [lines.cell(row, 1).value for row in (2, 3)] == [line.sku for line in detail.lines]
    assert lines.auto_filter.ref == "A1:P3" and lines.freeze_panes == "B2"
    header = {row[0].value: row[1].value for row in book["Cotizacion"].iter_rows()}
    assert header["Lista de precios"] == "10U"
    # No cost column reaches the advisors.
    assert not {"Costo más alto", "Costo inventario", "Costo cotización del proveedor"} & set(
        TEMPLATE_COLUMNS
    )


async def test_only_a_list_update_has_a_template(service, advisor) -> None:
    quotation = (await service.create(advisor, NIT, 12)).quotation

    with pytest.raises(InvalidInputError, match="Actualización de listas"):
        await service.list_template_file(advisor, quotation.id)


async def test_the_deleted_rows_leave_the_update_after_a_preview(service, lister) -> None:
    detail, file = await _template(service, lister)
    quotation_id = detail.quotation.id
    content = _edited(file.content, {"26278"})

    _, preview = await service.upload_list_template(lister, quotation_id, 1, content)

    assert preview.removed == (("26278", detail.lines[1].description),)
    assert (preview.kept, preview.unknown, preview.applied) == (1, (), False)
    # The preview changes nothing.
    assert (await service.get(lister, quotation_id)).quotation.version == 1

    saved, applied = await service.upload_list_template(
        lister, quotation_id, 1, content, apply=True
    )

    assert applied.applied and [line.sku for line in saved.lines] == ["2537"]
    assert saved.quotation.excluded_skus == ("26278",)
    assert saved.quotation.version == 2 and saved.quotation.line_count == 1
    # A refresh does not bring it back from the list.
    refreshed = await service.refresh(lister, quotation_id, 2)
    assert [line.sku for line in refreshed.lines] == ["2537"]
    # Adding it again brings it back for good.
    added, _ = await service.add_lines(lister, quotation_id, 3, ["26278"])
    assert "26278" in [line.sku for line in added.lines]
    assert added.quotation.excluded_skus == ()


async def test_the_template_is_checked_before_anything_is_removed(service, lister) -> None:
    detail, file = await _template(service, lister)
    quotation_id = detail.quotation.id

    with pytest.raises(InvalidInputError, match="de la lista 003"):
        await service.upload_list_template(
            lister, quotation_id, 1, _edited(file.content, **{"Lista de precios": "003"})
        )
    with pytest.raises(InvalidInputError, match="no trae referencias"):
        await service.upload_list_template(
            lister, quotation_id, 1, _edited(file.content, {"2537", "26278"})
        )
    with pytest.raises(InvalidInputError, match="no hay nada que quitar"):
        await service.upload_list_template(lister, quotation_id, 1, file.content, apply=True)
    with pytest.raises(InvalidInputError, match="Excel válido"):
        await service.upload_list_template(lister, quotation_id, 1, b"no es un libro")
    with pytest.raises(ConflictError):
        await service.upload_list_template(lister, quotation_id, 7, file.content)

    other = openpyxl.Workbook()
    other.active.title = "Hoja1"
    other.active.append(["SKU"])
    buffer = io.BytesIO()
    other.save(buffer)
    with pytest.raises(InvalidInputError, match="falta la hoja Lineas"):
        await service.upload_list_template(lister, quotation_id, 1, buffer.getvalue())


async def test_references_typed_again_and_unknown_ones(service, lister) -> None:
    detail, file = await _template(service, lister)
    book = openpyxl.load_workbook(io.BytesIO(file.content))
    lines = book["Lineas"]
    # Retyped as a number by Excel, and a reference the update does not have.
    lines.cell(2, 1).value = 2537
    lines.cell(3, 1).value = "99999"
    buffer = io.BytesIO()
    book.save(buffer)

    _, preview = await service.upload_list_template(
        lister, detail.quotation.id, 1, buffer.getvalue()
    )

    assert [sku for sku, _ in preview.removed] == ["26278"]
    assert preview.unknown == ("99999",) and preview.kept == 1


async def test_a_template_that_leaves_nothing_is_refused(service, lister) -> None:
    detail, file = await _template(service, lister)
    book = openpyxl.load_workbook(io.BytesIO(file.content))
    lines = book["Lineas"]
    lines.cell(2, 1).value = "88888"
    lines.cell(3, 1).value = "99999"
    buffer = io.BytesIO()
    book.save(buffer)

    with pytest.raises(InvalidInputError, match="no deja ninguna referencia"):
        await service.upload_list_template(lister, detail.quotation.id, 1, buffer.getvalue())
