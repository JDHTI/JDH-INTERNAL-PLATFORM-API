import copy
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.pqrsf.application.dto import ImportedCase, ImportedItem, ItemChanges
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.domain.entities import CreateResult, PqrsfRequest, RequestType
from app.modules.pqrsf.domain.tracking import (
    CaseFilters,
    CasePage,
    CaseStatus,
    DocumentType,
    ItemInput,
    add_business_days,
    business_days_between,
    case_code,
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
        allows_reason=follow_up,
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
        return None

    async def get_document_type(self, document_type_id):
        return self.document_types.get(document_type_id)

    async def create_case(self, case, event, number=None):
        if case.request_id and await self.get_case_by_request(case.request_id):
            raise ConflictError("duplicado")
        key = (case.request_type.id, case.year)
        if number is None:
            number = self.sequences.get(key, 0) + 1
        self.sequences[key] = max(self.sequences.get(key, 0), number)
        stored = replace(
            copy.deepcopy(case),
            number=number,
            code=case_code(case.request_type.prefix, case.year, number),
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


def test_case_code_restarts_per_type_and_year() -> None:
    assert case_code("P", 2026, 12) == "P-2026-00012"


@pytest.mark.asyncio
async def test_internal_case_gets_consecutive_and_due_date_from_documents() -> None:
    s = Setup()

    first = await s.service.create_case(s.command(), s.seller)
    second = await s.service.create_case(s.command(), s.seller)

    assert first.case.code == "P-2026-00001"
    assert second.case.code == "P-2026-00002"
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

    untouched = await s.service.mark_read(view.case.id, s.admin)
    assert untouched.case.status.code == "recibida"

    read = await s.service.mark_read(view.case.id, s.responsible)
    assert read.case.status.code == "leida"
    assert read.case.first_read_at is not None


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
    site = Site(uuid.uuid4(), "medellin", "Medellín", True, NOW, NOW)
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
        reason="Pedido incompleto",
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
    assert first.code == "P-2026-00001"
    assert first.customer_name == "Comercial SAS"
    assert first.due_on == date(2026, 10, 16)

    congratulation = await s.service.open_for_request(_public_request(s.felicitacion))
    assert congratulation.code == "F-2026-00001"
    assert congratulation.due_on is None


class _Opener:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def open_for_request(self, request):
        if self.fail:
            raise RuntimeError("db down")
        return type("Case", (), {"code": "P-2026-00007"})()

    async def code_for_request(self, request_id):
        return None


@pytest.mark.asyncio
async def test_submission_survives_a_failing_case_opener() -> None:
    s = Setup()
    request = _public_request(s.peticion)
    result = CreateResult(request, replayed=False)

    ok = await PqrsfService(None, None, cases=_Opener(fail=False))._with_case(result)
    failed = await PqrsfService(None, None, cases=_Opener(fail=True))._with_case(result)

    assert ok.tracking_code == "P-2026-00007"
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
    assert stored.code == "P-2026-02718"
    assert stored.items[0].response_days == 6
    assert stored.closed_at is not None
    assert await s.service.import_case(command) is None

    following = await s.service.create_case(s.command(), s.seller)
    assert following.case.code == "P-2026-02719"
