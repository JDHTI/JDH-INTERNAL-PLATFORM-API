"""The sales data a case reads: who sold the invoice a complaint or claim is about."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Where the sales executive of a case came from.
SOURCE_INVOICE = "invoice"
SOURCE_INTERNAL = "internal"
SOURCE_MANUAL = "manual"

# Document types of a sale in the ERP (``consumo.ventas.nro_transaccion`` is
# ``<tipo>-<consecutivo>``, for example ``FEV-1386163``). A number typed without its type is
# tried with each of these.
SALE_DOCUMENT_TYPES = ("FEV", "FEE", "NND")
_TYPED = re.compile(r"^([A-Z]+)0*(\d+)$")
_DIGITS = re.compile(r"^0*(\d+)$")


@dataclass(frozen=True)
class InvoiceSeller:
    """The seller of one invoice, as the sales data has it."""

    invoice_number: str
    code: str | None
    name: str | None
    email: str | None


def invoice_candidates(value: str) -> list[str]:
    """The ``nro_transaccion`` values an invoice number typed by a customer may be.

    Spaces, hyphens and dots go away, letters are the document type and the digits the
    consecutive without leading zeros: ``fev 001386163`` → ``FEV-1386163``. Digits alone are
    tried with every sale document type. Anything else (letters after digits) is not an
    invoice of the sales data.

    The printed electronic invoice (DIAN resolution prefix) is not confirmed to match the
    ERP document; adjust here once checked against real invoices.
    """

    compact = re.sub(r"[\s.\-_/]", "", value).upper()
    typed = _TYPED.fullmatch(compact)
    if typed:
        return [f"{typed.group(1)}-{typed.group(2)}"]
    digits = _DIGITS.fullmatch(compact)
    if digits:
        return [f"{kind}-{digits.group(1)}" for kind in SALE_DOCUMENT_TYPES]
    return []


def nit_core(value: str | None) -> str:
    """The NIT digits without the verification digit: ``900123456-7`` → ``900123456``."""

    if not value:
        return ""
    return re.sub(r"\D", "", value.split("-")[0])
