from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.identity.domain.principal import DirectoryUser
from app.modules.pqrsf.domain.timeline import (
    EVENT_SUPPLIER_MESSAGE,
    TimelineContext,
    describe,
    supplier_addresses,
)
from app.modules.pqrsf.domain.tracking import CaseEvent, CaseStatus, EventType

NOW = datetime(2026, 10, 26, tzinfo=UTC)
ANA = DirectoryUser(uuid4(), "ana@jdh.com.co", "Ana Pérez")
JUAN = DirectoryUser(uuid4(), "juan@jdh.com.co", "Juan Gómez")


def _status(code: str, name: str, terminal: bool = False) -> CaseStatus:
    return CaseStatus(uuid4(), code, name, None, 0, False, terminal, True, NOW, NOW)


RECIBIDA = _status("recibida", "Recibida")
LEIDA = _status("leida", "Leída")
EN_CURSO = _status("en_curso", "En curso")
FINALIZADA = _status("finalizada", "Finalizada", terminal=True)
CONTEXT = TimelineContext(
    statuses={s.id: s for s in (RECIBIDA, LEIDA, EN_CURSO, FINALIZADA)},
    requester_name="Cliente SAS",
    requester_email="cliente@example.com",
    supplier_addresses={"calidad@proveedor.com"},
)


def _event(event_type, actor=None, data=None, from_status=None, to_status=None) -> CaseEvent:
    return CaseEvent(
        uuid4(),
        uuid4(),
        event_type,
        actor.id if actor else None,
        from_status.id if from_status else None,
        to_status.id if to_status else None,
        None,
        data or {},
        NOW,
    )


@pytest.mark.parametrize(
    ("event", "actor", "expected"),
    [
        (
            _event("created", data={"origin": "public_form"}),
            None,
            ("system", "Sistema", "Creó el caso desde el formulario público"),
        ),
        (
            _event("created", data={"origin": "email"}),
            None,
            ("system", "Sistema", "Creó el caso desde un correo de cliente@example.com"),
        ),
        (
            _event("created", ANA, {"origin": "internal"}),
            ANA,
            ("user", "Ana Pérez", "Creó el caso"),
        ),
        (
            _event("read", ANA, from_status=RECIBIDA, to_status=LEIDA),
            ANA,
            ("user", "Ana Pérez", "Abrió el caso por primera vez"),
        ),
        (_event("viewed", JUAN), JUAN, ("user", "Juan Gómez", "Abrió el caso")),
        (
            _event("assigned", ANA, {"to_user_id": str(JUAN.id), "to_name": "Juan Gómez"}),
            ANA,
            ("user", "Ana Pérez", "Asignó el caso a Juan Gómez"),
        ),
        (
            _event("assigned", JUAN, {"to_user_id": str(JUAN.id), "to_name": "Juan Gómez"}),
            JUAN,
            ("user", "Juan Gómez", "Tomó el caso"),
        ),
        (
            _event("status_changed", JUAN, from_status=EN_CURSO, to_status=FINALIZADA),
            JUAN,
            ("user", "Juan Gómez", "Finalizó el caso"),
        ),
        (
            _event("status_changed", JUAN, from_status=FINALIZADA, to_status=EN_CURSO),
            JUAN,
            ("user", "Juan Gómez", "Reabrió el caso"),
        ),
        (
            _event("comment", JUAN, {"reply_to_client": True, "file_ids": ["a", "b"]}),
            JUAN,
            ("user", "Juan Gómez", "Respondió al cliente (2 archivos)"),
        ),
        (
            _event("item_updated", JUAN, {"document": "Ficha Técnica", "fields": ["responded_on"]}),
            JUAN,
            ("user", "Juan Gómez", "Respondió el documento Ficha Técnica"),
        ),
        (
            _event(
                EVENT_SUPPLIER_MESSAGE, JUAN, {"kind": "request", "supplier_name": "TEAM FOODS"}
            ),
            JUAN,
            ("user", "Juan Gómez", "Envió una solicitud de información a TEAM FOODS"),
        ),
        (
            _event("email_received", data={"from": "Calidad@Proveedor.com"}),
            None,
            ("supplier", "calidad@proveedor.com", "Respondió por correo"),
        ),
        (
            _event("email_received", data={"from": "cliente@example.com"}),
            None,
            ("client", "Cliente SAS", "Escribió por correo"),
        ),
        (
            _event("email_received", data={"from": "otro@example.com"}),
            None,
            ("external", "otro@example.com", "Escribió por correo"),
        ),
        (
            _event("updated", ANA, {"fields": ["subject", "site_id"]}),
            ANA,
            ("user", "Ana Pérez", "Editó el asunto y la sede"),
        ),
    ],
)
def test_each_event_reads_as_one_sentence(event, actor, expected) -> None:
    summary = describe(event, actor, CONTEXT)

    assert (summary.actor_kind, summary.actor_name, summary.title) == expected


def test_a_status_change_carries_the_status_names() -> None:
    summary = describe(_event("read", ANA, from_status=RECIBIDA, to_status=LEIDA), ANA, CONTEXT)

    assert (summary.status_from, summary.status_to) == ("Recibida", "Leída")
    assert summary.category == "access"


def test_supplier_addresses_come_from_the_messages_sent() -> None:
    sent = _event(EVENT_SUPPLIER_MESSAGE, JUAN, {"to": ["A@x.com", "b@y.com"]})

    assert supplier_addresses([sent, _event("comment", JUAN)]) == {"a@x.com", "b@y.com"}


def test_every_event_type_reads_in_spanish() -> None:
    inbox = describe(_event(EventType.INBOX_OPENED, ANA), ANA, CONTEXT)
    unknown = describe(_event("something_new", ANA), ANA, CONTEXT)

    assert (inbox.title, inbox.category) == ("Entró a la bandeja", "access")
    assert unknown.title == "Registró un cambio en el caso"
    names = [v for k, v in vars(EventType).items() if k.isupper()]
    titles = {describe(_event(name, ANA), ANA, CONTEXT).title for name in names}
    assert "Registró un cambio en el caso" not in titles
