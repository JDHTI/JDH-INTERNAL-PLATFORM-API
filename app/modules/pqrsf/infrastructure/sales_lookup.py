"""Sellers of invoices, read live from the warehouse sales view (``consumo.ventas``)."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from app.infrastructure.data_provider import DataProviderConnection
from app.modules.pqrsf.domain.sales import InvoiceSeller, nit_core

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# How far back a complaint may point: the view holds ~21 M rows, so every query is
# bounded by invoice number and date.
_LOOKBACK = timedelta(days=730)


class WarehouseSalesLookup:
    """Find who sold an invoice in the sales view, matched by ``nro_transaccion``.

    One row per invoice line, but the seller, customer and date belong to the invoice
    header, so the lines of one invoice agree. Only sales count (no returns), and voided
    invoices are left out. With the case's NIT only that customer's invoice counts;
    without a NIT, a number found for several customers is ambiguous and skipped.
    """

    def __init__(
        self,
        connection: DataProviderConnection,
        schema: str = "consumo",
        today: Callable[[], date] = date.today,
    ) -> None:
        """Create the lookup.

        Args:
            connection (DataProviderConnection): Read-only warehouse connection.
            schema (str): Consumption schema holding ``ventas`` and ``vendedores``.
            today (Callable[[], date]): Current date (replaced in tests).

        Raises:
            ValueError: The schema is not a SQL identifier.
        """

        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError("Invalid data provider schema")
        self.connection = connection
        self.schema = schema
        self.today = today

    async def sellers_of(
        self, invoice_numbers: Sequence[str], customer_nit: str | None
    ) -> list[InvoiceSeller]:
        """Find the seller of each invoice that exists, in the order given.

        Args:
            invoice_numbers (Sequence[str]): ``nro_transaccion`` values (``FEV-1386163``).
            customer_nit (str | None): The customer's NIT, to choose among invoices of
                different customers with the same number.

        Returns:
            list[InvoiceSeller]: One per invoice found.
        """

        if not invoice_numbers:
            return []
        query = (
            'SELECT DISTINCT v."nro_transaccion", v."nit_cliente", '
            'd."codigo", d."nombre", d."correo" '
            f'FROM "{self.schema}"."ventas" v '
            f'LEFT JOIN "{self.schema}"."vendedores" d ON d."vendedor_key" = v."vendedor_key" '
            'WHERE v."nro_transaccion" = ANY($1::text[]) AND v."fecha" >= $2 '
            "AND v.\"concepto\" = 'Ventas' AND COALESCE(v.\"estado\", '') <> 'Anulada'"
        )
        rows = await self.connection.fetch(query, list(invoice_numbers), self.today() - _LOOKBACK)
        wanted = nit_core(customer_nit)
        found: list[InvoiceSeller] = []
        for number in invoice_numbers:
            matches = [row for row in rows if row["nro_transaccion"] == number]
            if wanted:
                # Another customer's invoice would hand the case to a seller who must not
                # see it: with a NIT, only that customer's invoice counts.
                matches = [row for row in matches if nit_core(row["nit_cliente"]) == wanted]
            customers = {nit_core(row["nit_cliente"]) for row in matches}
            if not matches or len(customers) > 1:
                continue
            row = matches[0]
            found.append(InvoiceSeller(number, row["codigo"], row["nombre"], row["correo"]))
        return found
