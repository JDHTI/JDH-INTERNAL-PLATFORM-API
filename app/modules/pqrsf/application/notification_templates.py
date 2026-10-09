"""Plain-text bodies of the PQRSF notifications.

Each function returns ``(subject, body)``. They are the only place the wording lives,
so moving to HTML templates later means replacing these functions and nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

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


SIGNATURE = "PQRSF JDH"


def client_reply(case: TrackingCase, text: str) -> tuple[str, str]:
    """A partial reply to the client, written by the team from a case comment.

    The subject carries the consecutive, so the client's answer finds its case even when
    the conversation is lost; a reply sent inside a thread keeps the original subject.
    """

    greeting = f"Hola {case.requester_name}," if case.requester_name else "Hola,"
    subject = f"Respuesta a tu solicitud {case.code}"
    if case.subject:
        subject += f": {case.subject[:80]}"
    body = (
        f"{greeting}\n\n{text.strip()}\n\nConsecutivo de tu solicitud: {case.code}\n\n{SIGNATURE}"
    )
    return subject, body


def feedback_ack(kind_code: str, name: str | None) -> tuple[str, str]:
    """The generic thank-you for a suggestion or a compliment, signed PQRSF JDH."""

    first = (name or "").strip().split(" ")[0] if (name or "").strip() else ""
    greeting = f"Hola {first}," if first else "Hola,"
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
    return title, f"{greeting}\n\n{text}\n\n{SIGNATURE}"


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
