"""PQRSF received by email: the request template, thread matching and intake filters.

The template is plain text, one ``Clave: valor`` per line, with the description last::

    Tipo: Petición
    Sede: Medellín
    Nombre: Ana Pérez
    Razón social: Distribuciones XYZ S.A.S.
    NIT: 900123456-7
    Motivo: Cambio de factura
    Descripción:
    Texto libre, en las líneas que quieras.

Only the description is required: a message that does not follow the template is
still opened as a case (and flagged for review) instead of being lost.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.modules.pqrsf.domain.entities import Cause, RequestType, Subject
from app.modules.sites.domain.entities import Site

TEMPLATE = """Tipo: Petición | Queja | Reclamo | Sugerencia | Felicitación
Sede: (sede o sucursal)
Nombre: (quien solicita)
Razón social: (empresa)
NIT: (de la empresa)
Novedad: (en quejas y reclamos: Calidad del producto, Comportamiento en la entrega…)
Causal: (en quejas y reclamos: Producto vencido, Producto trocado…)
Motivo: (resumen corto)
Descripción:
(cuéntanos tu solicitud con el detalle que necesites)"""

# Statuses of a stored message: ``received`` is a claim taken before the work starts.
RECEIVED = "received"
CREATED = "created"
THREADED = "threaded"
IGNORED = "ignored"
FAILED = "failed"

# Event types the email flow adds to the case timeline.
EVENT_EMAIL_RECEIVED = "email_received"

_FIELD_ALIASES = {
    "request_type": {"tipo", "tipo de solicitud", "clase"},
    "site": {"sede", "sucursal"},
    "requester_name": {"nombre", "nombre completo", "solicitante"},
    "business_name": {"razon social", "empresa", "cliente"},
    "nit": {"nit"},
    "subject": {"motivo", "asunto"},
    # The subject and cause of the catalog (the typed «Asunto» stays free text).
    "novelty": {"novedad"},
    "cause": {"causal", "causa"},
    "description": {"descripcion", "detalle", "mensaje"},
}
_ALIAS_TO_FIELD = {alias: name for name, aliases in _FIELD_ALIASES.items() for alias in aliases}
_LINE = re.compile(r"^\s*([^\W\d_][\w ]{1,28}?)\s*:\s*(.*)$", re.UNICODE)
_QUOTE_START = re.compile(
    r"^\s*(?:"
    r"on .{0,120} wrote:|el .{0,120} escribi[oó]:|-{2,}\s*(?:original message|mensaje original)"
    r"|(?:de|from):\s.+@"
    r")",
    re.IGNORECASE,
)
_CASE_CODE = re.compile(r"\b[A-Z]\d{2}[A-Z]{2,4}\d{4}\b")
_SUBJECT_TAGS = re.compile(r"^\s*(?:(?:re|rv|fw|fwd)\s*:\s*|\[[^\]]{1,20}\]\s*)+", re.IGNORECASE)
# Subject of a supplier's answer sent on to the customer; answers to it are not followed.
FORWARD_SUBJECT = "Novedad caso"
_FORWARD_SUBJECT = re.compile(
    r"^\s*(?:(?:re|rv|fw|fwd)\s*:\s*|\[[^\]]*\]\s*)*novedad caso\b", re.IGNORECASE
)
_AUTOMATIC_SENDERS = re.compile(
    r"^(?:mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounce[s]?|notifications?)@", re.I
)
_AUTOMATIC_PRECEDENCE = {"bulk", "junk", "list", "auto_reply"}


def fold(text: str) -> str:
    """Lower-case without accents and with single spaces, to compare typed names."""

    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


@dataclass(frozen=True)
class ParsedRequest:
    """What the template said; every field is optional except ``description``."""

    request_type: str | None = None
    site: str | None = None
    requester_name: str | None = None
    business_name: str | None = None
    nit: str | None = None
    subject: str | None = None
    description: str = ""
    # True when no template field was found: the message was free text.
    free_text: bool = False
    # Names typed for the subject and cause of the catalog.
    novelty: str | None = None
    cause: str | None = None


def strip_quoted_reply(body: str) -> str:
    """Cut the quoted history a mail client appends to a reply."""

    kept: list[str] = []
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _QUOTE_START.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def parse_request(body: str) -> ParsedRequest:
    """Read the template out of a message body.

    Args:
        body (str): Plain-text body.

    Returns:
        ParsedRequest: Recognized fields; the description is everything after
        ``Descripción:`` or, without it, the lines that are not fields.
    """

    fields: dict[str, str] = {}
    loose: list[str] = []
    description: list[str] | None = None
    for line in strip_quoted_reply(body).split("\n"):
        if description is not None:
            description.append(line)
            continue
        match = _LINE.match(line)
        name = _ALIAS_TO_FIELD.get(fold(match.group(1))) if match else None
        if name is None:
            loose.append(line)
        elif name == "description":
            description = [match.group(2)]
        elif match.group(2).strip() and name not in fields:
            fields[name] = " ".join(match.group(2).split())
    text = "\n".join(description if description is not None else loose).strip()
    return ParsedRequest(
        request_type=fields.get("request_type"),
        site=fields.get("site"),
        requester_name=fields.get("requester_name"),
        business_name=fields.get("business_name"),
        nit=fields.get("nit"),
        subject=fields.get("subject"),
        description=text,
        free_text=not fields and description is None,
        novelty=fields.get("novelty"),
        cause=fields.get("cause"),
    )


def clean_subject(subject: str) -> str:
    """Drop reply/forward prefixes and ``[tags]`` from an email subject."""

    return _SUBJECT_TAGS.sub("", subject).strip()


def is_forward_reply(subject: str) -> bool:
    """Whether a subject answers a supplier's reply sent on to the customer.

    Those emails («Novedad caso {code}») say they are not answered: whatever comes back
    stays out of the platform. Any ``RE:``/``RV:`` and ``[tag]`` before it is skipped (the
    testing redirect adds a long ``[PRUEBA para …]``).
    """

    return bool(_FORWARD_SUBJECT.match(subject))


def find_case_code(subject: str) -> str | None:
    """The case consecutive (``P26AC0012``) quoted in a subject, if any."""

    match = _CASE_CODE.search(subject.upper())
    return match.group(0) if match else None


def match_name[T](value: str | None, options: Iterable[tuple[str | None, T]]) -> T | None:
    """Pick the option whose text equals what was typed, ignoring case and accents.

    ``options`` pairs each candidate text (a name, code or abbreviation) with the
    value to return.
    """

    if not value:
        return None
    wanted = fold(value)
    for text, result in options:
        if text and fold(text) == wanted:
            return result
    return None


def is_automatic(
    headers: Mapping[str, str], sender: str, own_addresses: Iterable[str]
) -> str | None:
    """Why a message must never open a case (a loop or noise), or ``None``.

    Args:
        headers (Mapping[str, str]): Lower-cased header names.
        sender (str): Sender address.
        own_addresses (Iterable[str]): Mailboxes of this platform, whose mail is ours.
    """

    address = sender.lower()
    if not address:
        return "sin remitente"
    if address in {own.lower() for own in own_addresses}:
        return "enviado por la plataforma"
    if _AUTOMATIC_SENDERS.match(address):
        return "remitente automático"
    if headers.get("auto-submitted", "no").strip().lower() not in {"", "no"}:
        return "respuesta automática"
    if headers.get("x-auto-response-suppress", "").strip():
        return "respuesta automática"
    if headers.get("precedence", "").strip().lower() in _AUTOMATIC_PRECEDENCE:
        return "correo masivo o automático"
    return None


def acknowledgment(code: str, needs_review: bool) -> str:
    """Text of the reply that confirms the case and keeps the conversation going."""

    text = (
        f"Hemos recibido tu solicitud y la registramos con el consecutivo {code}.\n"
        "Para agregar información o responder a nuestro equipo, contesta este mismo "
        "correo sin cambiar el asunto."
    )
    if needs_review:
        text += (
            "\n\nNo pudimos identificar todos los datos de tu solicitud, así que un "
            "gestor la revisará. Para agilizarla, la próxima vez usa este formato:\n\n" + TEMPLATE
        )
    return text


@dataclass
class EmailMessage:
    """A message tied to a case (or ignored), as stored for deduplication and the thread."""

    id: UUID
    internet_message_id: str
    graph_id: str
    conversation_id: str | None
    mailbox: str
    sender_address: str
    sender_name: str | None
    subject: str
    body_text: str
    received_at: datetime
    status: str
    case_id: UUID | None = None
    attempts: int = 0
    error: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EmailCaseInput:
    """What the tracking service needs to open a case from an email."""

    request_type: RequestType
    site: Site | None
    received_at: datetime
    subject: str | None
    description: str
    requester_name: str | None
    requester_email: str
    business_name: str | None
    nit: str | None
    message_id: UUID
    # Consecutive of the closed case this message answered, when it starts a new one.
    related_code: str | None = None
    # Subject and cause of the catalog named in the template; they say who attends it.
    subject_ref: Subject | None = None
    cause_ref: Cause | None = None


@dataclass(frozen=True)
class CaseAttachment:
    """A file that came with an email, stored in the bucket under its case."""

    id: UUID
    case_id: UUID
    message_id: UUID | None
    original_filename: str
    extension: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str
    created_at: datetime
    # The timeline comment the file was attached to; empty for files that came by email.
    comment_event_id: UUID | None = None


def case_attachment_key(case_id: UUID, attachment_id: UUID, extension: str) -> str:
    """Private object key built from generated identifiers only."""

    return f"pqrsf/cases/{case_id}/{attachment_id}.{extension}"
