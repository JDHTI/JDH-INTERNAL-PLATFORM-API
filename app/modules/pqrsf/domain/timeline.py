"""How each timeline event reads: who did it, what it was, in one short Spanish sentence."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from uuid import UUID

from app.modules.identity.domain.principal import DirectoryUser
from app.modules.pqrsf.domain.email import EVENT_EMAIL_RECEIVED
from app.modules.pqrsf.domain.tracking import CaseEvent, CaseStatus, EventType

# Who an event is told as coming from.
SYSTEM = "system"
USER = "user"
CLIENT = "client"
SUPPLIER = "supplier"
EXTERNAL = "external"

# What an event is about, so the interface can group or filter it.
LIFECYCLE = "lifecycle"
ACCESS = "access"
ASSIGNMENT = "assignment"
STATUS = "status"
COMMUNICATION = "communication"
DOCUMENTS = "documents"
CLASSIFICATION = "classification"

# A message to the supplier, written from the case.
EVENT_SUPPLIER_MESSAGE = "supplier_message"
SUPPLIER_MESSAGE_KINDS = {
    "request": "una solicitud de información",
    "response": "una respuesta",
    "improvement": "una oportunidad de mejora",
    "notice": "un mensaje informativo",
}

_ORIGINS = {
    "public_form": "desde el formulario público",
    "import": "desde el formato P.13.F.01",
    "internal": "",
}
_FIELDS = {
    "subject": "el asunto",
    "description": "la descripción",
    "received_on": "la fecha de recepción",
    "site_id": "la sede",
    "dependency_id": "la dependencia",
    # The case edit records the attribute it changed, not the id sent.
    "site": "la sede",
    "dependency": "la dependencia",
    "customer_name": "el cliente",
    "customer_nit": "el NIT",
    "supplier_name": "el proveedor",
    "sales_executive_name": "el ejecutivo comercial",
    "requester_name": "el solicitante",
    "requester_email": "el correo del solicitante",
    "pickup_order_requested": "la orden de recogida",
    "pickup_order_number": "el número de la orden de recogida",
    "delivery_date": "la fecha de entrega",
}


@dataclass(frozen=True)
class EventSummary:
    """A timeline event as it reads on screen."""

    actor_kind: str
    actor_name: str
    title: str
    category: str
    status_from: str | None = None
    status_to: str | None = None


@dataclass(frozen=True)
class TimelineContext:
    """What a sentence may need beyond the event: status names and who the case is with."""

    statuses: Mapping[UUID, CaseStatus] = field(default_factory=dict)
    requester_name: str | None = None
    requester_email: str | None = None
    # Addresses the case wrote to as supplier, so their answers read as the supplier's.
    supplier_addresses: Collection[str] = ()


def supplier_addresses(events: Collection[CaseEvent]) -> set[str]:
    """Every address a supplier message of the case went to."""

    found: set[str] = set()
    for event in events:
        if event.event_type == EVENT_SUPPLIER_MESSAGE:
            found.update(address.lower() for address in event.data.get("to", []))
    return found


def describe(
    event: CaseEvent, actor: DirectoryUser | None, context: TimelineContext
) -> EventSummary:
    """Tell an event as one short sentence with its author and category.

    Args:
        event (CaseEvent): The stored event.
        actor (DirectoryUser | None): The platform user who did it, if any.
        context (TimelineContext): Status names and the parties of the case.

    Returns:
        EventSummary: Who, what and, for status changes, from and to.
    """

    data = event.data or {}
    person = _person(actor) if actor else None
    kind, name = (USER, person) if person else (SYSTEM, "Sistema")
    status_from = _status_name(context, event.from_status_id)
    status_to = _status_name(context, event.to_status_id)
    event_type = event.event_type

    if event_type == EventType.CREATED:
        origin = data.get("origin")
        if origin == "email":
            sender = context.requester_email or "un cliente"
            return EventSummary(
                SYSTEM, "Sistema", f"Creó el caso desde un correo de {sender}", LIFECYCLE
            )
        where = _ORIGINS.get(origin or "", "")
        if person is None or origin in ("public_form", "import"):
            return EventSummary(SYSTEM, "Sistema", f"Creó el caso {where}".strip(), LIFECYCLE)
        return EventSummary(kind, name, "Creó el caso", LIFECYCLE)
    if event_type == EventType.READ:
        return EventSummary(
            kind, name, "Abrió el caso por primera vez", ACCESS, status_from, status_to
        )
    if event_type == EventType.VIEWED:
        return EventSummary(kind, name, "Abrió el caso", ACCESS)
    if event_type == EventType.INBOX_OPENED:
        return EventSummary(kind, name, "Entró a la bandeja", ACCESS)
    if event_type == EventType.ASSIGNED:
        return EventSummary(kind, name, _assignment(event, data, actor), ASSIGNMENT)
    if event_type == EventType.STATUS_CHANGED:
        previous = context.statuses.get(event.from_status_id) if event.from_status_id else None
        following = context.statuses.get(event.to_status_id) if event.to_status_id else None
        if previous is not None and previous.is_terminal:
            title = "Reabrió el caso"
        elif following is not None and following.is_terminal:
            title = "Finalizó el caso"
        else:
            title = "Cambió el estado"
        return EventSummary(kind, name, title, STATUS, status_from, status_to)
    if event_type == EventType.UPDATED:
        labels = [_FIELDS.get(item, item) for item in data.get("fields", [])]
        title = f"Editó {_join(labels)}" if labels else "Editó el caso"
        return EventSummary(kind, name, title, LIFECYCLE)
    if event_type == EventType.COMMENT:
        title = "Respondió al cliente" if data.get("reply_to_client") else "Comentó"
        files = len(data.get("file_ids", []))
        if files:
            title += f" ({files} archivo{'s' if files > 1 else ''})"
        return EventSummary(kind, name, title, COMMUNICATION)
    if event_type in (EventType.ITEM_ADDED, EventType.ITEM_UPDATED, EventType.ITEM_REMOVED):
        return EventSummary(kind, name, _item(event_type, data), DOCUMENTS)
    if event_type == EventType.DELETED:
        return EventSummary(kind, name, "Eliminó el caso", LIFECYCLE)
    if event_type == EVENT_SUPPLIER_MESSAGE:
        what = SUPPLIER_MESSAGE_KINDS.get(data.get("kind", ""), "un mensaje")
        target = data.get("supplier_name") or _join(data.get("to", [])) or "el proveedor"
        return EventSummary(kind, name, f"Envió {what} a {target}", COMMUNICATION)
    if event_type == EVENT_EMAIL_RECEIVED:
        return _email(data, context)
    # An event this module does not tell yet still reads in Spanish.
    return EventSummary(kind, name, "Registró un cambio en el caso", LIFECYCLE)


def _assignment(event: CaseEvent, data: dict, actor: DirectoryUser | None) -> str:
    to_id, to_name = data.get("to_user_id"), data.get("to_name")
    if not to_id:
        return "Quitó el responsable"
    if actor is not None and str(actor.id) == to_id:
        return "Tomó el caso"
    if event.actor_user_id is None:
        return f"Asignó el caso a {to_name or 'un responsable'} automáticamente"
    return f"Asignó el caso a {to_name or 'un responsable'}"


def _item(event_type: str, data: dict) -> str:
    document = data.get("document") or "un documento"
    if event_type == EventType.ITEM_ADDED:
        return f"Agregó el documento {document}"
    if event_type == EventType.ITEM_REMOVED:
        return f"Quitó el documento {document}"
    if "responded_on" in data.get("fields", []):
        return f"Respondió el documento {document}"
    return f"Editó el documento {document}"


def _email(data: dict, context: TimelineContext) -> EventSummary:
    sender = (data.get("from") or "").lower()
    if sender and sender in {address.lower() for address in context.supplier_addresses}:
        return EventSummary(SUPPLIER, sender, "Respondió por correo", COMMUNICATION)
    if sender and context.requester_email and sender == context.requester_email.lower():
        return EventSummary(
            CLIENT, context.requester_name or sender, "Escribió por correo", COMMUNICATION
        )
    return EventSummary(EXTERNAL, sender or "Remitente", "Escribió por correo", COMMUNICATION)


def _status_name(context: TimelineContext, status_id: UUID | None) -> str | None:
    if status_id is None:
        return None
    status = context.statuses.get(status_id)
    return status.name if status else None


def _person(user: DirectoryUser) -> str:
    return user.display_name or user.email or "Usuario"


def _join(values: list[str]) -> str:
    values = [value for value in values if value]
    if len(values) <= 1:
        return "".join(values)
    return f"{', '.join(values[:-1])} y {values[-1]}"
