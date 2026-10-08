import copy
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.pqrsf.application.dto import ImportedCase, ImportedItem, ItemChanges
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.domain.entities import (
    CreateResult,
    PqrsfRequest,
    RequestedDocument,
    RequestType,
)
from app.modules.pqrsf.domain.tracking import (
    ACCESS_EVENT_TYPES,
    ActivityFilters,
    CaseFilters,
    CasePage,
    CaseStatus,
    Dependency,
    DocumentType,
    EventType,
    ItemInput,
    LastSeen,
    add_business_days,
    business_days_between,
    case_code,
    case_series,
)
from app.modules.pqrsf.domain.tracking import CaseInput as Input
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

from tests.modules.fakes import FakeDirectory

NOW = datetime(2026, 9, 25, 15, tzinfo=UTC)
TODAY = date(2026, 9, 28)
# Colombian holidays near the test dates.
HOLIDAYS = frozenset({date(2026, 1, 1), date(2026, 1, 12), date(2026, 10, 12)})


def _request_type(code: str, prefix: str, follow_up: bool = True, sla: int | None = 15):
    return RequestType(
        id=uuid.uuid4(),
        code=code,
        name=code.title(),
        allows_document_request=follow_up,
        allows_attachments=follow_up,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
        prefix=prefix,
        requires_follow_up=follow_up,
        sla_days=sla,
    )


def _status(code: str, order: int, initial=False, terminal=False) -> CaseStatus:
    return CaseStatus(uuid.uuid4(), code, code, None, order, initial, terminal, True, NOW, NOW)


class FakeCalendar:
    def today(self):
        return TODAY

    def local_date(self, moment):
        return moment.date()

    def holidays(self, start, end):
        return HOLIDAYS


class FakeManagers:
    def __init__(self) -> None:
        self.managers: set[uuid.UUID] = set()

    async def is_manager(self, principal):
        return principal.is_platform_admin or principal.user_id in self.managers


class FakeRequests:
    def __init__(self, types) -> None:
        self.types = {t.id: t for t in types}
        self.requests: dict[uuid.UUID, PqrsfRequest] = {}

    async def list_request_types(self, active_only=True):
        return list(self.types.values())

    async def get_request_type(self, request_type_id):
        return self.types.get(request_type_id)

    async def get_request(self, request_id):
        return self.requests.get(request_id)


class FakeCases:
    def __init__(self, statuses, document_types) -> None:
        self.statuses = {s.id: s for s in statuses}
        self.document_types = {d.id: d for d in document_types}
        self.dependencies = {}
        self.cases = {}
        self.events = []
        self.sequences: dict[tuple, int] = {}

    async def list_statuses(self, include_inactive=False):
        return sorted(self.statuses.values(), key=lambda s: s.sort_order)

    async def get_status(self, status_id):
        return self.statuses.get(status_id)

    async def get_status_by_code(self, code):
        return next((s for s in self.statuses.values() if s.code == code), None)

    async def get_dependency(self, dependency_id):
        return self.dependencies.get(dependency_id)

    async def get_document_type(self, document_type_id):
        return self.document_types.get(document_type_id)

    async def create_case(self, case, event, number=None):
        if case.request_id and await self.get_case_by_request(case.request_id):
            raise ConflictError("duplicado")
        series = case_series(case.site)
        key = (case.request_type.id, case.year, series)
        if number is None:
            number = self.sequences.get(key, 0) + 1
        self.sequences[key] = max(self.sequences.get(key, 0), number)
        stored = replace(
            copy.deepcopy(case),
            number=number,
            series=series,
            code=case_code(case.request_type.prefix, case.year, series, number),
        )
        self.cases[case.id] = stored
        self.events.append(event)
        return copy.deepcopy(stored)

    async def get_case(self, case_id):
        case = self.cases.get(case_id)
        return copy.deepcopy(case) if case else None

    async def get_case_by_request(self, request_id):
        case = next((c for c in self.cases.values() if c.request_id == request_id), None)
        return copy.deepcopy(case) if case else None

    async def legacy_key_exists(self, source_key):
        return any((c.legacy or {}).get("source_key") == source_key for c in self.cases.values())

    async def list_cases(self, filters, page, size, today):
        self.last_filters = filters
        items = [
            c
            for c in self.cases.values()
            if filters.visible_to is None
            or filters.visible_to in {c.assignee_user_id, c.created_by_user_id}
        ]
        return CasePage([copy.deepcopy(c) for c in items], len(items), page, size)

    async def save_case(self, case, events):
        self.cases[case.id] = copy.deepcopy(case)
        self.events.extend(events)
        return copy.deepcopy(case)

    async def add_event(self, event):
        self.events.append(event)

    async def list_events(self, case_id):
        return [e for e in self.events if e.case_id == case_id]

    async def delete_case(self, case_id, event):
        self.events.append(event)
        self.cases.pop(case_id)
        for stored in self.events:
            if stored.case_id == case_id:
                stored.case_code = stored.case_code or event.case_code
                stored.case_id = None

    async def list_activity(self, filters):
        items = [
            e
            for e in self.events
            if (filters.before is None or e.created_at < filters.before)
            and (filters.include_access or e.event_type not in ACCESS_EVENT_TYPES)
        ]
        items.sort(key=lambda e: e.created_at, reverse=True)
        return items[: filters.limit]

    async def has_recent_access(self, event_type, actor_user_id, case_id, since):
        return any(
            e.event_type == event_type
            and e.actor_user_id == actor_user_id
            and e.case_id == case_id
            and e.created_at >= since
            for e in self.events
        )

    async def purge_access_events(self, before):
        kept = [
            e
            for e in self.events
            if e.event_type not in ACCESS_EVENT_TYPES or e.created_at >= before
        ]
        purged = len(self.events) - len(kept)
        self.events = kept
        return purged

    async def last_seen(self, since):
        seen = {}
        for e in self.events:
            if e.event_type == EventType.INBOX_OPENED and e.created_at >= since:
                seen[e.actor_user_id] = max(seen.get(e.actor_user_id, e.created_at), e.created_at)
        return [LastSeen(u, t) for u, t in sorted(seen.items(), key=lambda p: p[1], reverse=True)]


class Setup:
    def __init__(self) -> None:
        self.peticion = _request_type("peticion", "P")
        self.felicitacion = _request_type("felicitacion", "F", follow_up=False, sla=None)
        self.recibida = _status("recibida", 10, initial=True)
        self.leida = _status("leida", 20)
        self.en_curso = _status("en_curso", 30)
        self.finalizada = _status("finalizada", 40, terminal=True)
        self.certificado = DocumentType(uuid.uuid4(), "Certificado Calidad", 3, True, NOW, NOW)
        self.ficha = DocumentType(uuid.uuid4(), "Ficha Técnica", 5, True, NOW, NOW)
        self.directory = FakeDirectory()
        self.managers = FakeManagers()
        self.requests = FakeRequests([self.peticion, self.felicitacion])
        self.cases = FakeCases(
            [self.recibida, self.leida, self.en_curso, self.finalizada],
            [self.certificado, self.ficha],
        )
        self.service = CaseTrackingService(
            repository=self.cases,
            requests=self.requests,
            sites=SiteService(None),
            people=self.directory,
            managers=self.managers,
            calendar=FakeCalendar(),
        )
        self.admin = self.directory.sign_in("calidad@jdh.com.co", admin=True)
        self.responsible = self.directory.sign_in("cristian@jdh.com.co")
        self.seller = self.directory.sign_in("carolina@jdh.com.co")
        self.stranger = self.directory.sign_in("otro@jdh.com.co")

    def command(self, **overrides) -> Input:
        values = {
            "request_type_id": self.peticion.id,
            "received_on": date(2026, 9, 25),
            "customer_name": "TODO FRESA",
            "sales_executive_name": "Carolina Aguilar",
            "items": (
                ItemInput(self.certificado.id, quantity=2),
                ItemInput(self.ficha.id, quantity=1),
            ),
        }
        values.update(overrides)
        return Input(**values)


def test_business_days_follow_the_spreadsheet_convention() -> None:
    # Same-day answer counts as one day; Friday to Monday counts two.
    assert business_days_between(date(2026, 1, 2), date(2026, 1, 2), HOLIDAYS) == 1
    assert business_days_between(date(2026, 1, 2), date(2026, 1, 5), HOLIDAYS) == 2
    # Monday 12 January 2026 is a holiday.
    assert business_days_between(date(2026, 1, 9), date(2026, 1, 13), HOLIDAYS) == 2
    assert add_business_days(date(2026, 1, 9), 2, HOLIDAYS) == date(2026, 1, 13)
    assert add_business_days(date(2026, 9, 25), 15, HOLIDAYS) == date(2026, 10, 16)


def test_case_code_carries_year_and_site_series() -> None:
    assert case_code("P", 2026, "AC", 12) == "P26AC0012"
    assert case_code("Q", 2025, "AP", 10000) == "Q25AP10000"
    site = Site(uuid.uuid4(), "aco", "Aco", True, NOW, NOW, "AC")
    assert case_series(site) == "AC"
    assert case_series(None) == "GN"


@pytest.mark.asyncio
async def test_internal_case_gets_consecutive_and_due_date_from_documents() -> None:
    s = Setup()

    first = await s.service.create_case(s.command(), s.seller)
    second = await s.service.create_case(s.command(), s.seller)

    # No site on the command: the generic series.
    assert first.case.code == "P26GN0001"
    assert second.case.code == "P26GN0002"
    assert first.case.status.code == "recibida"
    # Ficha técnica (5 days) is the latest document: Fri 25 Sep + 5 = Thu 1 Oct.
    assert first.case.due_on == date(2026, 10, 1)
    assert [i.due_on for i in first.case.items] == [date(2026, 9, 29), date(2026, 10, 1)]
    assert first.access.can_view and not first.access.can_work


@pytest.mark.asyncio
async def test_only_managers_assign_responsibles() -> None:
    s = Setup()
    with pytest.raises(ForbiddenError):
        await s.service.create_case(s.command(assignee_user_id=s.responsible.user_id), s.seller)

    view = await s.service.create_case(s.command(), s.seller)
    with pytest.raises(ForbiddenError):
        await s.service.assign(view.case.id, s.responsible.user_id, None, s.seller)

    assigned = await s.service.assign(view.case.id, s.responsible.user_id, "Tuyo", s.admin)
    assert assigned.case.assignee_user_id == s.responsible.user_id


@pytest.mark.asyncio
async def test_collaborators_only_see_their_cases() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)

    with pytest.raises(ForbiddenError):
        await s.service.get_case(view.case.id, s.stranger)
    await s.service.list_cases(CaseFilters(), 1, 25, s.stranger)
    assert s.cases.last_filters.visible_to == s.stranger.user_id
    await s.service.list_cases(CaseFilters(), 1, 25, s.admin)
    assert s.cases.last_filters.visible_to is None


@pytest.mark.asyncio
async def test_responsible_opening_the_case_marks_it_read() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)
    await s.service.assign(view.case.id, s.responsible.user_id, None, s.admin)

    read = await s.service.mark_read(view.case.id, s.responsible)

    assert read.case.status.code == "leida"
    assert read.case.first_read_at is not None


@pytest.mark.asyncio
async def test_a_manager_opening_a_received_case_marks_it_read_but_not_for_the_responsible() -> (
    None
):
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)
    await s.service.assign(view.case.id, s.responsible.user_id, None, s.admin)

    by_manager = await s.service.mark_read(view.case.id, s.admin)

    assert by_manager.case.status.code == "leida"
    assert by_manager.case.first_read_at is None  # that is the responsible's own reading
    read = [e for e in s.cases.events if e.event_type == EventType.READ]
    assert [(e.actor_user_id, e.to_status_id) for e in read] == [(s.admin.user_id, s.leida.id)]

    by_responsible = await s.service.mark_read(view.case.id, s.responsible)

    assert by_responsible.case.status.code == "leida" and by_responsible.case.first_read_at
    read = [e for e in s.cases.events if e.event_type == EventType.READ]
    assert [e.actor_user_id for e in read] == [s.admin.user_id, s.responsible.user_id]
    assert read[1].to_status_id is None  # the status was already moved


@pytest.mark.asyncio
async def test_opening_a_case_that_is_not_received_never_changes_its_status() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)
    await s.service.change_status(view.case.id, s.en_curso.id, None, s.admin)

    opened = await s.service.mark_read(view.case.id, s.admin)

    assert opened.case.status.code == "en_curso"
    assert not [e for e in s.cases.events if e.event_type == EventType.READ]


@pytest.mark.asyncio
async def test_someone_who_can_only_see_the_case_does_not_move_its_status() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)  # the seller raised it: view only

    opened = await s.service.mark_read(view.case.id, s.seller)

    assert opened.case.status.code == "recibida"
    assert [e.event_type for e in s.cases.events if e.actor_user_id == s.seller.user_id][-1] == (
        EventType.VIEWED
    )


def _types(s: "Setup") -> list[str]:
    return [e.event_type for e in s.cases.events]


@pytest.mark.asyncio
async def test_every_opening_is_recorded_once_per_window() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)

    # The first opening by a manager moves the case to read; the next ones are access records.
    await s.service.mark_read(view.case.id, s.admin)
    await s.service.mark_read(view.case.id, s.admin)
    await s.service.mark_read(view.case.id, s.admin)
    await s.service.mark_read(view.case.id, s.seller)

    viewed = [e for e in s.cases.events if e.event_type == EventType.VIEWED]
    assert [e.actor_user_id for e in viewed] == [s.admin.user_id, s.seller.user_id]


@pytest.mark.asyncio
async def test_inbox_visits_are_recorded_and_old_ones_purged() -> None:
    s = Setup()
    old = s.service._event(None, EventType.INBOX_OPENED, s.seller.user_id)
    old.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    s.cases.events.append(old)

    await s.service.record_inbox_visit(s.responsible)
    await s.service.record_inbox_visit(s.responsible)

    assert old not in s.cases.events
    assert _types(s) == [EventType.INBOX_OPENED]
    seen = await s.service.last_seen(s.admin)
    assert [row.user_id for row in seen] == [s.responsible.user_id]
    with pytest.raises(ForbiddenError):
        await s.service.last_seen(s.responsible)


@pytest.mark.asyncio
async def test_deleting_a_case_keeps_its_history() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)
    code = view.case.code

    with pytest.raises(ForbiddenError):
        await s.service.delete_case(view.case.id, None, s.seller)
    await s.service.delete_case(view.case.id, "Duplicado de otro caso", s.admin)

    assert view.case.id not in s.cases.cases
    page = await s.service.list_activity(ActivityFilters(), s.admin)
    deleted = page.items[0].event
    assert deleted.event_type == EventType.DELETED
    assert deleted.case_code == code and deleted.case_id is None
    assert deleted.note == "Duplicado de otro caso"
    assert {item.event.case_code for item in page.items} == {code}
    with pytest.raises(ForbiddenError):
        await s.service.list_activity(ActivityFilters(), s.seller)


@pytest.mark.asyncio
async def test_activity_pages_newest_first() -> None:
    s = Setup()
    for minute in range(3):
        event = s.service._event(None, EventType.INBOX_OPENED, s.seller.user_id)
        event.created_at = datetime(2026, 9, 25, 10, minute, tzinfo=UTC)
        s.cases.events.append(event)

    first = await s.service.list_activity(ActivityFilters(limit=2), s.admin)
    rest = await s.service.list_activity(
        ActivityFilters(limit=2, before=first.next_before), s.admin
    )

    assert [i.event.created_at.minute for i in first.items] == [2, 1]
    assert [i.event.created_at.minute for i in rest.items] == [0]
    assert rest.next_before is None


@pytest.mark.asyncio
async def test_late_response_requires_a_cause_and_first_answer_starts_progress() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(), s.seller)
    await s.service.assign(view.case.id, s.responsible.user_id, None, s.admin)
    certificado = view.case.items[0]

    # Certificado: 3 days agreed, answered Monday 28 Sep = 2 business days.
    on_time = await s.service.update_item(
        view.case.id,
        certificado.id,
        ItemChanges(fields=frozenset({"responded_on"}), responded_on=date(2026, 9, 28)),
        s.responsible,
    )
    assert on_time.case.items[0].response_days == 2
    assert on_time.case.items[0].difference_days == -1
    assert on_time.case.status.code == "en_curso"

    # Ficha técnica received Mon 14 Sep (5 days) and answered Mon 28 Sep: 11 days.
    old = await s.service.create_case(
        s.command(received_on=date(2026, 9, 14), items=(ItemInput(s.ficha.id),)), s.admin
    )
    late = ItemChanges(fields=frozenset({"responded_on"}), responded_on=date(2026, 9, 28))
    with pytest.raises(InvalidInputError, match="causal"):
        await s.service.update_item(old.case.id, old.case.items[0].id, late, s.admin)
    explained = await s.service.update_item(
        old.case.id,
        old.case.items[0].id,
        replace(
            late,
            fields=frozenset({"responded_on", "delay_cause"}),
            delay_cause="El proveedor no respondió",
        ),
        s.admin,
    )
    assert explained.case.items[0].difference_days == 6
    assert explained.case.items[0].is_late


@pytest.mark.asyncio
async def test_closing_requires_answered_documents_and_reopening_a_note() -> None:
    s = Setup()
    view = await s.service.create_case(s.command(items=(ItemInput(s.certificado.id),)), s.seller)
    case_id = view.case.id

    with pytest.raises(InvalidInputError, match="por responder"):
        await s.service.change_status(case_id, s.finalizada.id, None, s.admin)

    item_id = view.case.items[0].id
    await s.service.update_item(
        case_id,
        item_id,
        ItemChanges(fields=frozenset({"responded_on"}), responded_on=date(2026, 9, 25)),
        s.admin,
    )
    closed = await s.service.change_status(case_id, s.finalizada.id, "Enviado", s.admin)
    assert closed.case.closed_at is not None

    with pytest.raises(ConflictError, match="finalizado"):
        await s.service.add_item(case_id, ItemInput(s.ficha.id), s.admin)
    with pytest.raises(InvalidInputError, match="reabre"):
        await s.service.change_status(case_id, s.en_curso.id, None, s.admin)
    reopened = await s.service.change_status(case_id, s.en_curso.id, "Falta un lote", s.admin)
    assert reopened.case.closed_at is None


def _public_request(request_type: RequestType) -> PqrsfRequest:
    site = Site(uuid.uuid4(), "medellin", "Medellín", True, NOW, NOW, "MD")
    return PqrsfRequest(
        id=uuid.uuid4(),
        reference="PQRSF-x",
        request_type=request_type,
        site=site,
        full_name="Ana Pérez",
        business_name="Comercial SAS",
        nit="900123456",
        address="Cra 1",
        department="Antioquia",
        city="Medellín",
        email="ana@example.com",
        subject="Pedido incompleto",
        description="Faltaron dos cajas",
        idempotency_key=str(uuid.uuid4()),
        idempotency_hash="h",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_public_request_opens_one_case_and_congratulations_have_no_due_date() -> None:
    s = Setup()
    request = _public_request(s.peticion)

    first = await s.service.open_for_request(request)
    again = await s.service.open_for_request(request)

    assert first.id == again.id
    assert first.code == "P26MD0001"
    assert first.customer_name == "Comercial SAS"
    assert first.due_on == date(2026, 10, 16)

    congratulation = await s.service.open_for_request(_public_request(s.felicitacion))
    assert congratulation.code == "F26MD0001"
    assert congratulation.due_on is None


@pytest.mark.asyncio
async def test_a_document_request_opens_the_case_with_its_documents_and_dependency() -> None:
    s = Setup()
    calidad = Dependency(uuid.uuid4(), "calidad", "Calidad", 1, True, NOW, NOW)
    s.cases.dependencies[calidad.id] = calidad
    request = replace(
        _public_request(s.peticion),
        document_request=True,
        documents=[
            RequestedDocument(s.certificado.id),
            RequestedDocument(s.ficha.id),
            RequestedDocument(None, "Carta de referencia"),
        ],
        document_dependency_id=calidad.id,
    )

    case = await s.service.open_for_request(request)

    assert case.dependency == calidad
    assert [
        (item.document_type and item.document_type.name, item.description) for item in case.items
    ] == [
        ("Certificado Calidad", None),
        ("Ficha Técnica", None),
        (None, "Carta de referencia"),
    ]
    # The slowest document (Ficha Técnica, 5 days) sets when the case is due.
    item_dues = [item.due_on for item in case.items if item.due_on]
    assert len(item_dues) == 2
    assert case.due_on == max(item_dues)


@pytest.mark.asyncio
async def test_a_document_request_without_an_active_dependency_opens_unassigned() -> None:
    s = Setup()
    inactive = Dependency(uuid.uuid4(), "calidad", "Calidad", 1, False, NOW, NOW)
    s.cases.dependencies[inactive.id] = inactive
    request = replace(
        _public_request(s.peticion),
        document_request=True,
        documents=[RequestedDocument(s.certificado.id)],
        document_dependency_id=inactive.id,
    )

    case = await s.service.open_for_request(request)

    assert case.dependency is None
    assert len(case.items) == 1


@pytest.mark.asyncio
async def test_each_site_keeps_its_own_counter() -> None:
    s = Setup()
    apartado = Site(uuid.uuid4(), "apartado", "Apartadó", True, NOW, NOW, "AP")

    medellin_1 = await s.service.open_for_request(_public_request(s.peticion))
    apartado_1 = await s.service.open_for_request(
        replace(_public_request(s.peticion), site=apartado)
    )
    medellin_2 = await s.service.open_for_request(_public_request(s.peticion))

    assert [medellin_1.code, apartado_1.code, medellin_2.code] == [
        "P26MD0001",
        "P26AP0001",
        "P26MD0002",
    ]


class _Opener:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def open_for_request(self, request):
        if self.fail:
            raise RuntimeError("db down")
        return type("Case", (), {"code": "P26MD0007"})()

    async def code_for_request(self, request_id):
        return None


@pytest.mark.asyncio
async def test_submission_survives_a_failing_case_opener() -> None:
    s = Setup()
    request = _public_request(s.peticion)
    result = CreateResult(request, replayed=False)

    ok = await PqrsfService(None, None, cases=_Opener(fail=False))._with_case(result)
    failed = await PqrsfService(None, None, cases=_Opener(fail=True))._with_case(result)

    assert ok.tracking_code == "P26MD0007"
    assert failed.tracking_code is None


@pytest.mark.asyncio
async def test_import_keeps_historical_consecutive_and_skips_reruns() -> None:
    s = Setup()
    command = ImportedCase(
        source_key="P.13.F.01:6",
        request_type_id=s.peticion.id,
        number=2718,
        received_on=date(2026, 9, 18),
        status_code="finalizada",
        customer_name="LOS HORNITOS",
        items=(
            ImportedItem(s.certificado.id, None, 1, 3, date(2026, 9, 25), "Proveedor demorado"),
        ),
        legacy={"responsable": "Juan Camilo Avendaño"},
    )

    stored = await s.service.import_case(command)
    assert stored.code == "P26GN2718"
    assert stored.items[0].response_days == 6
    assert stored.closed_at is not None
    assert await s.service.import_case(command) is None

    following = await s.service.create_case(s.command(), s.seller)
    assert following.case.code == "P26GN2719"


@pytest.mark.asyncio
async def test_unanswered_documents_past_due_are_flagged_and_assignments_keep_names() -> None:
    s = Setup()
    # Received Mon 14 Sep: certificado due Wed 16, ficha due Fri 18; today is 28 Sep.
    view = await s.service.create_case(s.command(received_on=date(2026, 9, 14)), s.admin)
    certificado, ficha = view.case.items
    answered = await s.service.update_item(
        view.case.id,
        certificado.id,
        ItemChanges(
            fields=frozenset({"responded_on", "delay_cause"}),
            responded_on=date(2026, 9, 28),
            delay_cause="Proveedor",
        ),
        s.admin,
    )
    assert answered.is_overdue
    assert answered.overdue_item_ids == frozenset({ficha.id})

    await s.service.assign(view.case.id, s.responsible.user_id, None, s.admin)
    await s.service.assign(view.case.id, s.seller.user_id, None, s.admin)
    assignments = [e for e in s.cases.events if e.event_type == "assigned"]
    assert assignments[-1].data["from_name"] == "cristian@jdh.com.co"
    assert assignments[-1].data["to_name"] == "carolina@jdh.com.co"
