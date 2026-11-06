"""Notifications: ingestion, audiences, scheduling, reminders, the bell and push."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.core.errors import InvalidInputError
from app.modules.notifications.domain.entities import (
    AudienceType,
    IngestCommand,
    PushSubscription,
    Source,
    Status,
    Trigger,
)
from tests.modules.fakes import principal


def _command(**fields) -> IngestCommand:
    values = {
        "source": Source.NOTICIA,
        "source_id": "doc-1",
        "title": "Nueva política",
        "idempotency_key": "doc-1:2026-11-04T10:00:00Z",
        "audience_type": AudienceType.TODOS,
        "slug": "nueva-politica",
        "summary": "Resumen",
    }
    values.update(fields)
    return IngestCommand(**values)


async def test_an_immediate_publication_reaches_everyone_once(world) -> None:
    ana = principal("ana@jdh.com.co")
    world.directory.employee(user_id=ana.user_id)
    world.directory.employee()

    first = await world.service.ingest(_command())
    retry = await world.service.ingest(_command())

    assert first.duplicate is False and first.recipients == 2
    assert first.status is Status.ENVIADA
    assert retry.duplicate is True and retry.id == first.id
    feed = await world.service.feed(ana)
    assert feed.total == 1 and feed.unread == 1
    item = feed.items[0]
    assert item.notification.href == "/noticias/nueva-politica" and item.read is False


async def test_a_platform_notice_rings_the_bell_of_the_users_with_an_employee(world) -> None:
    ana = principal("ana@jdh.com.co")
    guest = principal("invitado@x.co")
    employee = world.directory.employee(user_id=ana.user_id)
    world.repository.subs["https://push/ana"] = PushSubscription(
        uuid4(), employee, "https://push/ana", "k", "a"
    )
    path = "/calidad/pqrsf/abc"

    await world.service.ring(
        [ana.user_id, guest.user_id], "pqrsf-news:1", "Novedad en el caso Q1", "Escribió", path
    )
    await world.service.ring([ana.user_id], "pqrsf-news:1", "Novedad en el caso Q1", "Otra", path)

    feed = await world.service.feed(ana)
    assert feed.total == 1 and feed.unread == 1
    notice = feed.items[0].notification
    assert notice.source is Source.PQRSF and notice.href == path
    assert notice.title == "Novedad en el caso Q1"
    assert [payload["href"] for _, payload in world.push.sent] == [path]
    assert (await world.service.feed(guest)).total == 0


async def test_a_platform_notice_without_employees_does_nothing(world) -> None:
    await world.service.ring([uuid4()], "k", "t", "s", "/x")

    assert world.repository.notifications == {}


async def test_audiences_by_site_area_and_people(world) -> None:
    bodega = world.directory.employee(site="s1")
    bi = world.directory.employee(area="BI")
    world.directory.employee()

    by_site = await world.service.ingest(
        _command(source_id="a", audience_type=AudienceType.SEDE, audience_values=["s1"])
    )
    by_area = await world.service.ingest(
        _command(source_id="b", audience_type=AudienceType.AREA, audience_values=["bi"])
    )
    by_people = await world.service.ingest(
        _command(
            source_id="c",
            audience_type=AudienceType.EMPLEADOS,
            audience_values=[str(bodega), str(bi)],
        )
    )

    assert (by_site.recipients, by_area.recipients, by_people.recipients) == (1, 1, 2)
    with pytest.raises(InvalidInputError):
        await world.service.ingest(_command(source_id="d", audience_type=AudienceType.SEDE))
    with pytest.raises(InvalidInputError):
        await world.service.ingest(
            _command(source_id="e", audience_type=AudienceType.EMPLEADOS, audience_values=["x"])
        )


async def test_scheduled_notifications_go_out_on_time_and_late_ones_are_cancelled(world) -> None:
    world.directory.employee()
    later = await world.service.ingest(
        _command(source_id="later", scheduled_for=world.now + timedelta(hours=2))
    )
    assert later.status is Status.PROGRAMADA and later.recipients is None
    with pytest.raises(InvalidInputError):
        await world.service.ingest(_command(source_id="past", scheduled_for=world.now))

    assert await world.service.dispatch_due() == (0, 0)
    world.now += timedelta(hours=3)
    assert await world.service.dispatch_due() == (1, 0)
    assert await world.repository.recipients(later.id)
    assert await world.service.dispatch_due() == (0, 0)

    stale = await world.service.ingest(
        _command(source_id="stale", scheduled_for=world.now + timedelta(minutes=1))
    )
    world.now += timedelta(days=2)
    assert await world.service.dispatch_due() == (0, 1)
    assert world.repository.notifications[stale.id].status is Status.CANCELADA


async def test_events_can_schedule_a_reminder_at_eight_local_time(world) -> None:
    event = _command(
        source=Source.EVENTO,
        source_id="evt",
        slug="feria",
        reminder_days_before=2,
        event_date=date(2026, 11, 20),
    )

    result = await world.service.ingest(event)
    again = await world.service.ingest(event)

    reminder = world.repository.notifications[result.reminder_id]
    assert reminder.trigger is Trigger.RECORDATORIO and reminder.status is Status.PROGRAMADA
    # 08:00 in Bogotá (UTC-5) on 2026-11-18 is 13:00 UTC.
    assert reminder.scheduled_for.isoformat() == "2026-11-18T13:00:00+00:00"
    assert again.duplicate is True
    assert sum(1 for n in world.repository.notifications.values() if n.source_id == "evt") == 2
    with pytest.raises(InvalidInputError):
        await world.service.ingest(_command(source_id="n", reminder_days_before=1))
    past = await world.service.ingest(
        _command(
            source=Source.EVENTO,
            source_id="old",
            reminder_days_before=10,
            event_date=date(2026, 11, 5),
        )
    )
    assert past.reminder_id is None


async def test_the_bell_pages_marks_and_ignores_people_without_employee(world) -> None:
    ana = principal("ana@jdh.com.co")
    world.directory.employee(user_id=ana.user_id)
    for index in range(3):
        await world.service.ingest(_command(source_id=f"n{index}"))

    page = await world.service.feed(ana, page=1, page_size=2)
    assert len(page.items) == 2 and page.total == 3 and page.unread == 3

    await world.service.mark_read(ana, page.items[0].notification.id)
    assert await world.service.unread_count(ana) == 2
    assert (await world.service.feed(ana, unread_only=True)).total == 2
    await world.service.mark_read(ana, None)
    assert await world.service.unread_count(ana) == 0

    guest = principal("invitado@proveedor.com")
    assert (await world.service.feed(guest)).total == 0
    await world.service.mark_read(guest, None)
    with pytest.raises(InvalidInputError):
        await world.service.feed(ana, page_size=51)


async def test_push_goes_to_the_recipients_and_drops_gone_browsers(world) -> None:
    ana = principal("ana@jdh.com.co")
    luis = principal("luis@jdh.com.co")
    world.directory.employee(user_id=ana.user_id, site="s1")
    world.directory.employee(user_id=luis.user_id)
    await world.service.subscribe(ana, "https://push.test/ana", "key", "auth")
    await world.service.subscribe(luis, "https://push.test/luis", "key", "auth")
    world.push.answers["https://push.test/luis"] = 410
    with pytest.raises(InvalidInputError):
        await world.service.subscribe(ana, "http://insecure", "key", "auth")

    await world.service.ingest(_command(audience_type=AudienceType.SEDE, audience_values=["s1"]))
    assert [endpoint for endpoint, _ in world.push.sent] == ["https://push.test/ana"]
    payload = world.push.sent[0][1]
    assert payload["titulo"] == "Nueva política" and payload["href"] == "/noticias/nueva-politica"

    await world.service.ingest(_command(source_id="all"))
    assert "https://push.test/luis" not in world.repository.subs
    await world.service.unsubscribe(ana, "https://push.test/ana")
    assert world.repository.subs == {}


async def test_push_off_sends_nothing_and_has_no_key(world) -> None:
    world.push.key = None
    ana = principal("ana@jdh.com.co")
    world.directory.employee(user_id=ana.user_id)
    await world.service.subscribe(ana, "https://push.test/ana", "key", "auth")

    await world.service.ingest(_command())

    assert world.service.public_key() is None and world.push.sent == []


async def test_audiences_catalog_for_strapi(world) -> None:
    catalog = await world.service.audiences()

    assert catalog["sedes"][0]["nombre"] == "Bodega"
    assert catalog["areas"] == [{"nombre": "BI", "empleados": 3}]
    assert uuid4()  # the catalog ids are strings
    assert isinstance(catalog["sedes"][0]["id"], str)
