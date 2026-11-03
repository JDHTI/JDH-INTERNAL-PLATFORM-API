"""The ``ACTUALIZACION LISTAS PRECIOS`` workbook SIESA's Excel importer reads."""

from __future__ import annotations

import io

from openpyxl import Workbook

from app.modules.quotations.domain.export import PriceListExport
from app.modules.quotations.infrastructure.siesa_file import day

PRICE_SHEET = "Precios"
TAX_SHEET = "Impuestos en valor"
PRICE_HEADERS = ("LISTA DE PRECIOS", "REFERENCIA", "AAAAMMDD", "UNIDAD_MEDIDA", "PRECIO DE LISTA")
TAX_HEADERS = (
    "LISTA DE PRECIOS",
    "REFERENCIA",
    "AAAAMMDD",
    "UNIDAD_MEDIDA",
    "LLAVE IMPUESTO",
    "VALOR IMPUESTO",
)


class PriceListWorkbookWriter:
    """Writes a price list update as the two-sheet workbook of the template."""

    def write(self, export: PriceListExport) -> bytes:
        """Codes, references, dates and units go as text so leading zeros survive."""

        workbook = Workbook(write_only=True)
        prices = workbook.create_sheet(PRICE_SHEET)
        prices.append(PRICE_HEADERS)
        for row in export.rows:
            prices.append(
                (row.price_list_id, row.sku, day(row.activation_date), row.unit, row.price)
            )
        taxes = workbook.create_sheet(TAX_SHEET)
        taxes.append(TAX_HEADERS)
        for tax in export.taxes:
            taxes.append(
                (
                    tax.price_list_id,
                    tax.sku,
                    day(tax.activation_date),
                    tax.unit,
                    tax.tax_key,
                    tax.amount,
                )
            )
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()


__all__ = ["PRICE_HEADERS", "PRICE_SHEET", "TAX_HEADERS", "TAX_SHEET", "PriceListWorkbookWriter"]
