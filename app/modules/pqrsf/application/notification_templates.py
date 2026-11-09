"""Wording of the PQRSF notifications.

Each function returns ``(subject, body)``. Emails to the team are plain text; emails to
the customer (acknowledgment, updates, thank-yous) return a :class:`BrandedEmail`, the
content of the JDH layout. They are the only place the wording lives.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.modules.mail.layout import BrandedEmail
from app.modules.pqrsf.domain.entities import ClaimedProduct
from app.modules.pqrsf.domain.tracking import TrackingCase

_LIST_LIMIT = 30
_FOOTER = "\n\n— PQRSF JDH · mensaje automático, no respondas a este correo."


def _name(value: str | None) -> str:
    return (value or "").strip() or "equipo"


def _day(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "sin fecha límite"


def _link(url: str | None) -> str:
    return f"\nAbrir el caso: {url}" if url else ""


def case_line(case: TrackingCase, today: date, assignee: str | None = None) -> str:
    """One line of a case list: code, type, reason, owner and due date."""

    parts = [case.code, case.request_type.name]
    if case.subject:
        parts.append(case.subject[:60])
    if assignee is not None:
        parts.append(assignee or "sin responsable")
    if case.due_on is None:
        parts.append("sin fecha límite")
    elif case.is_overdue(today):
        parts.append(
            f"vencido hace {(today - case.due_on).days} día(s) (venció {_day(case.due_on)})"
        )
    else:
        parts.append(f"vence {_day(case.due_on)}")
    return "- " + " · ".join(parts)


def _listing(lines: Sequence[str]) -> str:
    shown = list(lines[:_LIST_LIMIT])
    if len(lines) > _LIST_LIMIT:
        shown.append(f"… y {len(lines) - _LIST_LIMIT} más.")
    return "\n".join(shown)


def assignment(
    case: TrackingCase,
    assignee_name: str | None,
    actor_name: str | None,
    note: str | None,
    url: str | None,
) -> tuple[str, str]:
    """«You have a new case»: sent to the person a colleague assigned a case to."""

    actor = _name(actor_name)
    lines = [
        f"Hola {_name(assignee_name)},",
        "",
        f"{actor[:1].upper()}{actor[1:]} te asignó un caso de PQRSF.",
        "",
        f"Consecutivo: {case.code}",
        f"Tipo: {case.request_type.name}",
    ]
    if case.subject:
        lines.append(f"Motivo: {case.subject}")
    if case.requester_name or case.requester_email:
        lines.append(
            "Solicitante: "
            + " · ".join(x for x in (case.requester_name, case.requester_email) if x)
        )
    if case.customer_name:
        lines.append(f"Cliente: {case.customer_name}")
    lines.append(f"Fecha límite: {_day(case.due_on)}")
    if note:
        lines += ["", f"Nota: {note}"]
    return f"Nuevo caso asignado: {case.code}", "\n".join(lines) + "\n" + _link(url) + _FOOTER


def news_sender(case: TrackingCase, sender: str) -> str:
    """Who wrote, as the team knows them: the client by name, anyone else by address."""

    if case.requester_email and sender.lower() == case.requester_email.lower():
        return f"El cliente ({case.requester_name})" if case.requester_name else "El cliente"
    return sender or "Un remitente externo"


def case_news(
    case: TrackingCase,
    recipient_name: str | None,
    sender: str,
    excerpt: str,
    url: str | None,
) -> tuple[str, str]:
    """«There is news on your case»: an email from outside joined a case they work."""

    who = news_sender(case, sender)
    lines = [
        f"Hola {_name(recipient_name)},",
        "",
        f"{who} escribió por correo en el caso {case.code}.",
        "",
        f"Consecutivo: {case.code}",
        f"Tipo: {case.request_type.name}",
    ]
    if case.subject:
        lines.append(f"Motivo: {case.subject}")
    lines.append(f"Fecha límite: {_day(case.due_on)}")
    text = excerpt.strip()
    if text:
        lines += ["", "Mensaje:", text[:1_000]]
    return f"Novedad en el caso {case.code}", "\n".join(lines) + "\n" + _link(url) + _FOOTER


SIGNATURE = "PQRSF JDH"

_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
_LOCAL = ZoneInfo("America/Bogota")
# What the customer can do after any email: answering keeps the case together.
_ANSWER_HINT = (
    "Si necesitas agregar información, responde a este correo conservando el radicado en el asunto."
)


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0] if (name or "").strip() else ""


def _greeting(name: str | None) -> str:
    first = _first_name(name)
    return f"Hola {first}," if first else "Hola,"


def received_at_text(value: datetime) -> str:
    """«6 de noviembre de 2026, 9:41 a. m.» in Colombian time."""

    local = value.astimezone(_LOCAL) if value.tzinfo else value
    hour = local.hour % 12 or 12
    meridian = "a. m." if local.hour < 12 else "p. m."
    return (
        f"{local.day} de {_MONTHS[local.month - 1]} de {local.year}, "
        f"{hour}:{local.minute:02d} {meridian}"
    )


def request_received(
    type_name: str, code: str, received_at: datetime, name: str | None
) -> tuple[str, BrandedEmail]:
    """The acknowledgment of a request filed on the public form, with its radicado.

    Only requests that open a case get one (petitions, complaints, claims): suggestions
    and compliments have no radicado and get their own thank-you. The subject carries
    the radicado, so an answer from the customer finds its case.
    """

    kind = type_name.strip().lower() or "solicitud"
    content = BrandedEmail(
        title=f"Radicamos tu {kind}",
        greeting=_greeting(name),
        paragraphs=(
            f"Recibimos tu {kind}. Este es el radicado con el que le haremos seguimiento:",
        ),
        facts=(
            ("Radicado", code),
            ("Tipo de solicitud", type_name.strip()),
            ("Fecha de recepción", received_at_text(received_at)),
        ),
        closing=(
            "Te daremos pronta respuesta. Mantente pendiente de tu correo: por este medio "
            "te contaremos los avances.",
            _ANSWER_HINT,
        ),
        footnote=(
            "Recibiste este correo porque radicaste una solicitud en el formulario PQRSF de JDH."
        ),
    )
    return f"Radicamos tu {kind}: {code}", content


def client_reply(case: TrackingCase, text: str) -> tuple[str, BrandedEmail]:
    """An update to the client, written by the team from the case (a comment sent to them).

    Same layout as the acknowledgment; the message is what the team wrote. The subject
    carries the consecutive, so the client's answer finds its case even when the
    conversation is lost; a reply sent inside a thread keeps the original subject.
    """

    subject = f"Actualización de tu solicitud {case.code}"
    if case.subject:
        subject += f": {case.subject[:80]}"
    content = BrandedEmail(
        title=f"Actualización de tu {case.request_type.name.strip().lower() or 'solicitud'}",
        greeting=_greeting(case.requester_name),
        paragraphs=(text.strip(),),
        facts=(("Radicado", case.code), ("Tipo de solicitud", case.request_type.name)),
        closing=(_ANSWER_HINT,),
    )
    return subject, content


AUTOMATIC_NOTICE = (
    "Este es un correo automático: no lo respondas. Las respuestas a esta dirección no se "
    "leen ni se procesan."
)


def customer_forward(
    case: TrackingCase, supplier_text: str, note: str | None, mailbox: str
) -> tuple[str, BrandedEmail]:
    """A supplier's answer passed on to the customer by whoever works the case.

    It leaves from a mailbox nobody reads, so it says so at the top and tells where to
    write instead. The subject is fixed: «Novedad caso {radicado}».
    """

    kind = case.request_type.name.strip().lower() or "solicitud"
    paragraphs = [f"Tenemos una novedad sobre tu {kind}."]
    if note and note.strip():
        paragraphs.append(note.strip())
    paragraphs.append("Esta es la respuesta que recibimos:")
    content = BrandedEmail(
        title=f"Novedad de tu {kind}",
        greeting=_greeting(case.requester_name),
        notice=AUTOMATIC_NOTICE,
        paragraphs=tuple(paragraphs),
        quote=supplier_text.strip() or None,
        facts=(("Radicado", case.code), ("Tipo de solicitud", case.request_type.name)),
        closing=(
            f"Si necesitas agregar información, escríbenos a {mailbox} con el radicado "
            f"{case.code} en el asunto.",
        ),
        footnote="Correo automático enviado desde una cuenta que no recibe respuestas.",
    )
    return f"Novedad caso {case.code}", content


def supplier_message(
    case: TrackingCase,
    kind_label: str,
    text: str,
    products: Sequence[ClaimedProduct] = (),
    with_case_data: bool = False,
) -> tuple[str, str]:
    """A message to a supplier written from a case.

    The consecutive opens the subject in brackets, so the supplier's answer returns to the
    case through the mailbox even from a new conversation.
    """

    subject = f"[{case.code}] {kind_label}"
    if case.subject:
        subject += f": {case.subject[:80]}"
    lines = ["Cordial saludo,", "", text.strip()]
    if with_case_data:
        lines += ["", f"Datos del caso {case.code}:"]
        if case.customer_name:
            lines.append(f"- Cliente: {case.customer_name}")
        if case.site:
            lines.append(f"- Sede: {case.site.name}")
        lines.append(f"- Recibido el: {_day(case.received_on)}")
        for product in products:
            lines.append(
                f"- Producto {product.sku} {product.description or ''}".rstrip()
                + f" · Lote {product.lot} · Vence {_day(product.expiry_date)}"
                + f" · Cantidad afectada {product.affected_quantity}"
                + f" · Factura {product.invoice_number}"
            )
    lines += ["", f"Por favor responda conservando el consecutivo {case.code} en el asunto."]
    return subject, "\n".join(lines) + f"\n\n{SIGNATURE}"


def feedback_ack(kind_code: str, name: str | None) -> tuple[str, BrandedEmail]:
    """The generic thank-you for a suggestion or a compliment; they carry no radicado."""

    if kind_code == "felicitacion":
        title = "Recibimos tu felicitación"
        text = (
            "Recibimos tu felicitación. Gracias por tomarte el tiempo de escribirnos: "
            "tus palabras nos motivan a seguir mejorando cada día."
        )
    else:
        title = "Recibimos tu sugerencia"
        text = (
            "Recibimos tu sugerencia. Agradecemos tu mensaje: nos ayuda a mejorar "
            "nuestro servicio y la tendremos en cuenta."
        )
    return title, BrandedEmail(title=title, greeting=_greeting(name), paragraphs=(text,))


def document_delivery(
    name: str | None,
    documents: Sequence[str],
    part: int,
    parts: int,
    case_code: str | None,
    manual: Sequence[str],
) -> tuple[str, str]:
    """The certificates asked for on the public form, sent without anyone writing to them.

    A request too big for one email goes in several (``part`` of ``parts``); what a person
    still has to answer (``manual``) is told in the first one, with its case.
    """

    first = (name or "").strip().split(" ")[0] if (name or "").strip() else ""
    lines = [f"Hola {first}," if first else "Hola,", ""]
    one = len(documents) == 1
    lines.append(
        "Adjuntamos el documento que solicitaste:"
        if one
        else "Adjuntamos los documentos que solicitaste:"
    )
    lines += [f"- {document}" for document in documents]
    if part == 1 and (manual or case_code):
        lines.append("")
        if case_code:
            lines.append(
                f"El resto de tu solicitud ({', '.join(manual)}) quedó registrada con el "
                f"consecutivo {case_code}; te responderemos por este medio."
                if manual
                else f"El resto de tu solicitud quedó registrada con el consecutivo {case_code}."
            )
    subject = "Documentos solicitados a JDH"
    if parts > 1:
        subject += f" ({part} de {parts})"
    return subject, "\n".join(lines) + f"\n\n{SIGNATURE}"


def case_document(name: str | None, case_code: str, document: str) -> tuple[str, str]:
    """A document the client asked for in a case, answered by the team.

    The subject carries the consecutive, so the client's answer finds its case.
    """

    first = (name or "").strip().split(" ")[0] if (name or "").strip() else ""
    lines = [f"Hola {first}," if first else "Hola,", ""]
    lines.append(f"Adjuntamos el documento «{document}» de tu solicitud {case_code}.")
    return f"Documento de tu solicitud {case_code}", "\n".join(lines) + f"\n\n{SIGNATURE}"


def daily_summary(
    name: str | None, lines: Sequence[str], total: int, overdue: int, today: date, url: str | None
) -> tuple[str, str]:
    """End-of-day summary: how many cases stayed open and unresolved."""

    if total == 0:
        subject = f"Resumen PQRSF {_day(today)}: sin casos abiertos"
        body = f"Hola {_name(name)},\n\nNo quedaron casos abiertos sin resolver el día de hoy."
        return subject, body + _FOOTER
    plural = "caso abierto" if total == 1 else "casos abiertos"
    body = [
        f"Hola {_name(name)},",
        "",
        f"Quedaron {total} {plural} sin resolver el día de hoy.",
    ]
    if overdue:
        body.append(
            "De ellos, 1 está vencido." if overdue == 1 else f"De ellos, {overdue} están vencidos."
        )
    body += ["", _listing(lines)]
    if url:
        body += ["", f"Ver todos los casos: {url}"]
    return f"Resumen PQRSF {_day(today)}: {total} {plural}", "\n".join(body) + _FOOTER


def overdue_reminder(
    name: str | None,
    own: Sequence[str],
    unassigned: Sequence[str],
    url: str | None,
) -> tuple[str, str]:
    """Morning reminder grouped per person: their overdue cases and, for the team, the orphans."""

    total = len(own) + len(unassigned)
    noun = "caso vencido" if total == 1 else "casos vencidos"
    body = [f"Hola {_name(name)},", ""]
    if own:
        body += [
            f"Tienes {len(own)} {'caso vencido' if len(own) == 1 else 'casos vencidos'} "
            "a tu cargo. Dale seguimiento:",
            "",
            _listing(own),
        ]
    if unassigned:
        if own:
            body.append("")
        one = len(unassigned) == 1
        body += [
            f"Hay {len(unassigned)} {'caso vencido' if one else 'casos vencidos'} sin responsable. "
            + ("Asígnalo o atiéndelo:" if one else "Asígnalos o atiéndelos:"),
            "",
            _listing(unassigned),
        ]
    if url:
        body += ["", f"Abrir PQRSF: {url}"]
    return f"PQRSF: {total} {noun}, dale seguimiento", "\n".join(body) + _FOOTER
