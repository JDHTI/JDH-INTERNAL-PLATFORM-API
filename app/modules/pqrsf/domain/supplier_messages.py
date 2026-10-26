"""Messages to suppliers written from a case, and the contacts they go to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.errors import InvalidInputError

# What a message to the supplier is, and how its email subject starts.
MESSAGE_KINDS = {
    "request": "Solicitud de información",
    "response": "Respuesta",
    "improvement": "Oportunidad de mejora",
    "notice": "Mensaje informativo",
}
# Addresses one message goes to.
MAX_RECIPIENTS = 5
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class SupplierContact:
    """An address a case wrote to as supplier."""

    id: UUID
    supplier_code: str
    supplier_name: str | None
    email: str
    contact_name: str | None = None
    last_used_at: datetime | None = None


def clean_recipients(values: list[str]) -> list[str]:
    """Lower-case, unique, valid addresses; between one and ``MAX_RECIPIENTS``.

    Raises:
        InvalidInputError: No address, too many, or one that is not an email.
    """

    recipients = list(dict.fromkeys(value.strip().lower() for value in values if value.strip()))
    if not recipients:
        raise InvalidInputError("Indica al menos un correo del proveedor")
    if len(recipients) > MAX_RECIPIENTS:
        raise InvalidInputError(f"Máximo {MAX_RECIPIENTS} correos por mensaje")
    for address in recipients:
        if len(address) > 320 or not _EMAIL.fullmatch(address):
            raise InvalidInputError(f"El correo {address} no es válido")
    return recipients


def clean_kind(value: str) -> str:
    """Accept one of ``MESSAGE_KINDS``.

    Raises:
        InvalidInputError: Unknown kind.
    """

    if value not in MESSAGE_KINDS:
        raise InvalidInputError(
            "El tipo de mensaje debe ser 'request', 'response', 'improvement' o 'notice'"
        )
    return value
