"""The sales data a case reads: who sold the invoice a complaint or claim is about."""

from __future__ import annotations

from dataclasses import dataclass

# Where the sales executive of a case came from.
SOURCE_INVOICE = "invoice"
SOURCE_INTERNAL = "internal"
SOURCE_MANUAL = "manual"


@dataclass(frozen=True)
class InvoiceSeller:
    """The seller of one invoice, as the sales data has it."""

    invoice_number: str
    code: str | None
    name: str | None
    email: str | None


def invoice_key(value: str) -> str:
    """The invoice number as the sales data is searched by.

    Upper case without spaces. The exact form (prefix, operation center, separators) is
    adjusted here once the sales view in ``consumo`` is defined.
    """

    return "".join(value.split()).upper()
