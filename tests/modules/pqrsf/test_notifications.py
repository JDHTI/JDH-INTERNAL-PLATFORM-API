import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.errors import ForbiddenError, InvalidInputError
from app.modules.pqrsf.application import notification_templates as templates
from app.modules.pqrsf.application.notifications import NotificationService
from app.modules.pqrsf.domain.notifications import (
    ASSIGNMENT,
    CASE_NEWS,
    DAILY_SUMMARY,
    MAX_ATTEMPTS,
    OVERDUE_REMINDER,
    JobSchedule,
    Notification,
    NotificationSettings,
    format_time,
    parse_time,
)
from app.modules.pqrsf.domain.tracking import CasePage
from app.modules.pqrsf.infrastructure.holidays_api import NagerHolidayProvider
from tests.modules.pqrsf.test_tracking import Setup

BOGOTA = ZoneInfo("America/Bogota")
# Monday 5 October 2026; Monday 12 October is a public holiday in Colombia.
MONDAY = date(2026, 10, 5)
HOLIDAY = date(2026, 10, 12)
SATURDAY = date(2026, 10, 10)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=BOGOTA).astimezone(UTC)


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class FakeOutbox:
    def __init__(self) -> None:
        self.items: list[Notification] = []
        self.settings: dict | None = None
        self.settings_by: uuid.UUID | None = None
        self.runs: set[tuple[str, date]] = set()

    async def enqueue(self, notification):
        if notification.dedupe_key and any(
            i.dedupe_key == notification.dedupe_key for i in self.items
        ):
            return False
        self.items.append(notification)
        return True

    async def claim_due(self, now, limit):
        due = [
            i
            for i in self.items
            if i.status == "pending" and i.next_attempt_at and i.next_attempt_at <= now
        ]
        return due[:limit]

    async def save_notification(self, notification):
        pass  # the service mutates the stored object itself

    async def get_notification(self, notification_id):
        return next((i for i in self.items if i.id == notification_id), None)

    async def list_for_case(self, case_id, kind):
        return [i for i in self.items if i.case_id == case_id and i.kind == kind]

    async def get_settings(self):
        return self.settings

    async def save_settings(self, value, user_id):
        self.settings, self.settings_by = value, user_id

    async def claim_job_run(self, job, run_date):
        if (job, run_date) in self.runs:
            return False
        self.runs.add((job, run_date))
        return True

    async def release_job_run(self, job, run_date):
        self.runs.discard((job, run_date))


class FakeMail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str]] = []
        self.fail = 0

    async def send(self, mailbox, to, subject, text):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("graph caído")
        self.sent.append((mailbox, to, subject, text))


class FakeHolidays:
    def __init__(self) -> None:
        self.days_off = {HOLIDAY}

    async def is_business_day(self, day):
        return day.weekday() < 5 and day not in self.days_off


class FakeTeam:
    def __init__(self, ids) -> None:
        self.ids = ids

    async def team_user_ids(self):
        return self.ids


class OpenCases:
    """List cases the way the repository does for the filters the reports use."""

    def __init__(self, cases) -> None:
        self.cases = cases

    async def list_cases(self, filters, page, size, today):
        items = [
            c
            for c in self.cases.cases.values()
            if (not filters.open_only or not c.is_closed)
            and (not filters.overdue or c.is_overdue(today))
        ]
        window = items[(page - 1) * size : page * size]
        return CasePage(window, len(items), page, size)


class Env:
    """Tracking setup plus the notification service wired over the same fakes."""

    def __init__(self, now: datetime | None = None, redirect: str | None = None) -> None:
        self.s = Setup()
        self.outbox = FakeOutbox()
        self.mail = FakeMail()
        self.holidays = FakeHolidays()
        self.clock = Clock(now or at(MONDAY, 8))
        self.ana = self.s.directory.sign_in("ana@jdh.com.co")
        self.beto = self.s.directory.sign_in("beto@jdh.com.co")
        self.carla = self.s.directory.sign_in("carla@jdh.com.co")
        self.noemail = self.s.directory.sign_in("noemail@jdh.com.co")
        self.s.directory.users[self.noemail.user_id] = type(
            self.s.directory.users[self.noemail.user_id]
        )(id=self.noemail.user_id, email=None, display_name="Sin correo")
        self.team = [self.ana.user_id, self.beto.user_id]
        self.service = NotificationService(
            repository=self.outbox,
            cases=OpenCases(self.s.cases),
            people=self.s.directory,
            team=FakeTeam(self.team),
            mail=self.mail,
            managers=self.s.managers,
            holidays=self.holidays,
            mailbox="pqrsf@jdh.com.co",
            area_slug="calidad",
            ui_base_url="http://localhost:3000/",
            redirect_to=redirect,
            clock=self.clock,
        )
        self.s.service.notifier = self.service
        for principal in (self.ana, self.beto):
            self.s.managers.managers.add(principal.user_id)

    async def new_case(self, assignee=None, creator=None):
        view = await self.s.service.create_case(
            self.s.command(assignee_user_id=assignee.user_id if assignee else None),
            creator or self.s.admin,
        )
        # On time by default: each test makes overdue only the cases it means to.
        self.s.cases.cases[view.case.id].due_on = MONDAY + timedelta(days=10)
        return view.case

    def kinds(self, kind=None):
        return [i for i in self.outbox.items if kind is None or i.kind == kind]


# --- News on a case ---


class FakeBell:
    def __init__(self) -> None:
        self.rung: list[tuple] = []

    async def ring(self, user_ids, key, title, summary, path):
        self.rung.append((sorted(user_ids), title, summary, path))


@pytest.mark.asyncio
async def test_an_email_on_an_assigned_case_tells_its_responsible_by_bell_and_email() -> None:
    env = Env()
    bell = env.service.bell = FakeBell()
    case = await env.new_case(assignee=env.carla, creator=env.s.admin)
    env.outbox.items.clear()

    await env.s.service.record_email(case.id, uuid.uuid4(), "proveedor@x.co", "Ya enviamos", {})

    (item,) = env.kinds(CASE_NEWS)
    assert item.recipient_email == "carla@jdh.com.co" and item.case_id == case.id
    assert item.subject == f"Novedad en el caso {case.code}"
    assert "proveedor@x.co escribió por correo" in item.body and "Ya enviamos" in item.body
    assert f"http://localhost:3000/calidad/pqrsf/{case.id}" in item.body
    assert bell.rung == [
        (
            [env.carla.user_id],
            f"Novedad en el caso {case.code}",
            "proveedor@x.co escribió por correo",
            f"/calidad/pqrsf/{case.id}",
        )
    ]


@pytest.mark.asyncio
async def test_news_on_a_case_without_responsible_goes_to_the_team() -> None:
    env = Env()
    bell = env.service.bell = FakeBell()
    case = await env.new_case()
    env.s.cases.cases[case.id].requester_email = "Cliente@X.co"
    env.s.cases.cases[case.id].requester_name = "Ana Cliente"

    await env.s.service.record_email(case.id, uuid.uuid4(), "cliente@x.co", "Hola", {})

    assert sorted(i.recipient_email for i in env.kinds(CASE_NEWS)) == [
        "ana@jdh.com.co",
        "beto@jdh.com.co",
    ]
    assert "El cliente (Ana Cliente) escribió por correo" in env.kinds(CASE_NEWS)[0].body
    assert bell.rung[0][0] == sorted([env.ana.user_id, env.beto.user_id])


@pytest.mark.asyncio
async def test_with_emails_off_the_news_still_rings_the_bell() -> None:
    env = Env()
    env.service.enabled = False
    bell = env.service.bell = FakeBell()
    case = await env.new_case(assignee=env.carla, creator=env.s.admin)

    await env.s.service.record_email(case.id, uuid.uuid4(), "cliente@x.co", "Hola", {})

    assert env.kinds(CASE_NEWS) == []
    assert len(bell.rung) == 1


# --- Assignment notice ---


@pytest.mark.asyncio
async def test_assigning_to_someone_else_queues_a_new_case_email() -> None:
    env = Env()
    case = await env.new_case()

    await env.s.service.assign(case.id, env.carla.user_id, "Revísalo hoy", env.ana)

    (item,) = env.kinds(ASSIGNMENT)
    assert item.recipient_email == "carla@jdh.com.co" and item.case_id == case.id
    assert item.subject == f"Nuevo caso asignado: {case.code}"
    assert case.code in item.body and "Revísalo hoy" in item.body
    assert f"http://localhost:3000/calidad/pqrsf/{case.id}" in item.body
    assert item.status == "pending" and item.dedupe_key is None


@pytest.mark.asyncio
async def test_assigning_to_yourself_sends_nothing() -> None:
    env = Env()
    case = await env.new_case()

    await env.s.service.assign(case.id, env.ana.user_id, None, env.ana)

    assert env.kinds() == []


@pytest.mark.asyncio
async def test_every_reassignment_notifies_the_new_responsible_but_unassigning_does_not() -> None:
    env = Env()
    case = await env.new_case()

    await env.s.service.assign(case.id, env.beto.user_id, None, env.ana)
    await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)
    await env.s.service.assign(case.id, env.beto.user_id, None, env.ana)
    await env.s.service.assign(case.id, None, None, env.ana)

    assert [i.recipient_email for i in env.kinds(ASSIGNMENT)] == [
        "beto@jdh.com.co",
        "carla@jdh.com.co",
        "beto@jdh.com.co",
    ]


@pytest.mark.asyncio
async def test_a_case_created_already_assigned_to_someone_else_notifies_them() -> None:
    env = Env()

    case = await env.new_case(assignee=env.carla, creator=env.s.admin)
    await env.new_case(assignee=env.s.admin, creator=env.s.admin)  # to themselves: nothing

    (item,) = env.kinds(ASSIGNMENT)
    assert item.case_id == case.id


@pytest.mark.asyncio
async def test_a_responsible_without_email_is_skipped_and_a_queue_failure_never_blocks() -> None:
    env = Env()
    case = await env.new_case()

    await env.s.service.assign(case.id, env.noemail.user_id, None, env.ana)
    assert env.kinds() == []

    async def broken(*args, **kwargs):
        raise RuntimeError("base de datos caída")

    env.service.case_assigned = broken
    view = await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)

    assert view.case.assignee_user_id == env.carla.user_id


@pytest.mark.asyncio
async def test_nothing_is_queued_while_notifications_are_switched_off() -> None:
    env = Env()
    env.service.enabled = False
    case = await env.new_case()

    await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)

    assert env.kinds() == []


# --- Timers ---


def test_the_timers_default_to_end_and_start_of_the_workday() -> None:
    settings = NotificationSettings()

    assert (settings.daily_summary.enabled, format_time(settings.daily_summary.at)) == (
        True,
        "17:30",
    )
    assert format_time(settings.overdue_reminder.at) == "07:30"
    assert NotificationSettings.from_json(settings.to_json()) == settings
    assert NotificationSettings.from_json({"daily_summary": {"time": "18:00"}}).daily_summary == (
        JobSchedule(True, time(18, 0))
    )


@pytest.mark.parametrize("value", ["5:30", "17h30", "25:00", "17:60", "", "17:30:00"])
def test_a_time_must_be_hh_mm(value: str) -> None:
    with pytest.raises(InvalidInputError):
        parse_time(value)


@pytest.mark.asyncio
async def test_whoever_works_pqrsf_can_read_and_change_the_timers_for_everyone() -> None:
    env = Env()
    assert (await env.service.get_settings(env.ana)).daily_summary.at == time(17, 30)

    changed = NotificationSettings(
        daily_summary=JobSchedule(True, time(18, 0)),
        overdue_reminder=JobSchedule(False, time(8, 0)),
    )
    await env.service.update_settings(changed, env.beto)

    # Persisted once for the organization, whoever reads it next.
    assert await env.service.get_settings(env.ana) == changed
    assert env.outbox.settings_by == env.beto.user_id


@pytest.mark.asyncio
async def test_people_outside_pqrsf_cannot_touch_the_timers() -> None:
    env = Env()
    stranger = env.s.stranger

    with pytest.raises(ForbiddenError):
        await env.service.get_settings(stranger)
    with pytest.raises(ForbiddenError):
        await env.service.update_settings(NotificationSettings(), stranger)


@pytest.mark.asyncio
async def test_a_timer_fires_once_on_the_business_day_inside_its_window() -> None:
    env = Env(now=at(MONDAY, 17, 29))
    await env.new_case()
    assert await env.service.run_scheduled() == []

    env.clock.moment = at(MONDAY, 17, 30)
    assert await env.service.run_scheduled() == [DAILY_SUMMARY]
    env.clock.moment = at(MONDAY, 17, 45)
    assert await env.service.run_scheduled() == []  # already ran today

    next_day = MONDAY + timedelta(days=1)
    env.clock.moment = at(next_day, 17, 30)
    assert await env.service.run_scheduled() == [DAILY_SUMMARY]


@pytest.mark.asyncio
async def test_the_morning_reminder_runs_at_07_30_and_late_starts_are_skipped() -> None:
    env = Env(now=at(MONDAY, 7, 30))
    assert await env.service.run_scheduled() == [OVERDUE_REMINDER]

    # The API was down: coming back within two hours still sends; later skips the day.
    late = Env(now=at(MONDAY, 9, 20))
    assert await late.service.run_scheduled() == [OVERDUE_REMINDER]
    stale = Env(now=at(MONDAY, 9, 31))
    assert await stale.service.run_scheduled() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("day", [SATURDAY, HOLIDAY])
async def test_nothing_runs_on_weekends_or_holidays(day: date) -> None:
    env = Env(now=at(day, 17, 30))

    assert await env.service.run_scheduled() == []
    env.clock.moment = at(day, 7, 30)
    assert await env.service.run_scheduled() == []


@pytest.mark.asyncio
async def test_a_switched_off_timer_does_not_fire_and_a_changed_time_applies() -> None:
    env = Env(now=at(MONDAY, 17, 30))
    await env.service.update_settings(
        NotificationSettings(
            daily_summary=JobSchedule(False, time(17, 30)),
            overdue_reminder=JobSchedule(True, time(17, 30)),
        ),
        env.ana,
    )

    assert await env.service.run_scheduled() == [OVERDUE_REMINDER]


@pytest.mark.asyncio
async def test_a_failed_run_is_released_so_the_next_tick_retries() -> None:
    env = Env(now=at(MONDAY, 17, 30))
    original = env.service.run_daily_summary

    async def broken(today):
        raise RuntimeError("falló")

    env.service.run_daily_summary = broken
    with pytest.raises(RuntimeError):
        await env.service.run_scheduled()
    assert (DAILY_SUMMARY, MONDAY) not in env.outbox.runs

    env.service.run_daily_summary = original
    assert await env.service.run_scheduled() == [DAILY_SUMMARY]


# --- End-of-day summary ---


@pytest.mark.asyncio
async def test_the_summary_counts_the_open_cases_for_each_team_member() -> None:
    env = Env()
    first = await env.new_case(assignee=env.carla)
    await env.new_case()
    closed = await env.new_case()
    env.s.cases.cases[closed.id].status = env.s.finalizada
    env.s.cases.cases[first.id].due_on = MONDAY - timedelta(days=3)

    queued = await env.service.run_daily_summary(MONDAY)

    assert queued == 2
    assert {i.recipient_email for i in env.kinds(DAILY_SUMMARY)} == {
        "ana@jdh.com.co",
        "beto@jdh.com.co",
    }
    item = env.kinds(DAILY_SUMMARY)[0]
    assert "Quedaron 2 casos abiertos sin resolver el día de hoy." in item.body
    assert "De ellos, 1 está vencido." in item.body
    assert first.code in item.body and closed.code not in item.body
    assert item.subject.endswith("2 casos abiertos")
    assert "carla" not in item.recipient_email


@pytest.mark.asyncio
async def test_the_summary_is_sent_once_per_person_and_day() -> None:
    env = Env()
    await env.new_case()

    assert await env.service.run_daily_summary(MONDAY) == 2
    assert await env.service.run_daily_summary(MONDAY) == 0
    assert await env.service.run_daily_summary(MONDAY + timedelta(days=1)) == 2


@pytest.mark.asyncio
async def test_the_summary_says_so_when_nothing_is_left_open() -> None:
    env = Env()

    await env.service.run_daily_summary(MONDAY)

    assert "No quedaron casos abiertos" in env.kinds(DAILY_SUMMARY)[0].body


# --- Overdue reminder ---


@pytest.mark.asyncio
async def test_overdue_cases_are_grouped_in_one_email_per_responsible() -> None:
    env = Env()
    carla_a = await env.new_case(assignee=env.carla)
    carla_b = await env.new_case(assignee=env.carla)
    on_time = await env.new_case(assignee=env.carla)
    for case in (carla_a, carla_b):
        env.s.cases.cases[case.id].due_on = MONDAY - timedelta(days=2)
    env.s.cases.cases[on_time.id].due_on = MONDAY + timedelta(days=5)

    await env.service.run_overdue_reminder(MONDAY)

    by_email = {i.recipient_email: i for i in env.kinds(OVERDUE_REMINDER)}
    carla = by_email["carla@jdh.com.co"]
    assert carla.subject == "PQRSF: 2 casos vencidos, dale seguimiento"
    assert "Tienes 2 casos vencidos a tu cargo" in carla.body
    assert carla_a.code in carla.body and carla_b.code in carla.body
    assert on_time.code not in carla.body
    assert "vencido hace 2 día(s)" in carla.body
    # Carla is not on the team and owns the only overdue cases: nobody else is emailed.
    assert set(by_email) == {"carla@jdh.com.co"}


@pytest.mark.asyncio
async def test_overdue_cases_without_a_responsible_go_to_the_whole_team() -> None:
    env = Env()
    orphan = await env.new_case()
    mine = await env.new_case(assignee=env.ana)
    for case in (orphan, mine):
        env.s.cases.cases[case.id].due_on = MONDAY - timedelta(days=1)

    await env.service.run_overdue_reminder(MONDAY)

    by_email = {i.recipient_email: i for i in env.kinds(OVERDUE_REMINDER)}
    assert set(by_email) == {"ana@jdh.com.co", "beto@jdh.com.co"}
    # Ana gets one email with her case and the orphan; Beto only the orphan.
    assert by_email["ana@jdh.com.co"].subject == "PQRSF: 2 casos vencidos, dale seguimiento"
    assert mine.code in by_email["ana@jdh.com.co"].body
    assert "sin responsable" in by_email["beto@jdh.com.co"].body
    assert mine.code not in by_email["beto@jdh.com.co"].body
    assert by_email["beto@jdh.com.co"].subject == "PQRSF: 1 caso vencido, dale seguimiento"


@pytest.mark.asyncio
async def test_no_overdue_cases_means_no_email_and_reruns_never_duplicate() -> None:
    env = Env()
    case = await env.new_case(assignee=env.ana)
    assert await env.service.run_overdue_reminder(MONDAY) == 0

    env.s.cases.cases[case.id].due_on = MONDAY - timedelta(days=1)
    assert await env.service.run_overdue_reminder(MONDAY) == 1
    assert await env.service.run_overdue_reminder(MONDAY) == 0
    assert await env.service.run_overdue_reminder(MONDAY + timedelta(days=1)) == 1


# --- Delivery ---


@pytest.mark.asyncio
async def test_queued_emails_leave_from_the_pqrsf_mailbox() -> None:
    env = Env()
    case = await env.new_case()
    await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)

    assert await env.service.dispatch() == 1

    mailbox, to, subject, body = env.mail.sent[0]
    assert (mailbox, to) == ("pqrsf@jdh.com.co", "carla@jdh.com.co")
    assert subject.startswith("Nuevo caso asignado") and case.code in body
    item = env.kinds()[0]
    assert item.status == "sent" and item.sent_at is not None
    assert await env.service.dispatch() == 0  # sent ones are not sent again


@pytest.mark.asyncio
async def test_a_failed_send_waits_and_retries_then_gives_up() -> None:
    env = Env()
    case = await env.new_case()
    await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)
    item = env.kinds()[0]
    env.mail.fail = 99

    assert await env.service.dispatch() == 0
    assert (item.status, item.attempts) == ("pending", 1)
    assert item.next_attempt_at == env.clock.moment + timedelta(minutes=1)
    assert await env.service.dispatch() == 0  # not due yet: untouched
    assert item.attempts == 1

    for _ in range(MAX_ATTEMPTS - 1):
        env.clock.moment = item.next_attempt_at or env.clock.moment
        await env.service.dispatch()
    assert (item.status, item.attempts) == ("failed", MAX_ATTEMPTS)

    env.mail.fail = 0
    env.clock.moment += timedelta(days=1)
    assert await env.service.dispatch() == 0 and not env.mail.sent


@pytest.mark.asyncio
async def test_a_recovered_send_is_delivered_on_the_next_try() -> None:
    env = Env()
    case = await env.new_case()
    await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)
    env.mail.fail = 1
    await env.service.dispatch()

    env.clock.moment += timedelta(minutes=2)

    assert await env.service.dispatch() == 1
    assert env.kinds()[0].status == "sent" and env.kinds()[0].error is None


@pytest.mark.asyncio
async def test_testing_redirect_sends_everything_to_one_inbox_and_names_the_recipient() -> None:
    env = Env(redirect="analista.ti@jdh.com.co")
    case = await env.new_case()
    await env.s.service.assign(case.id, env.carla.user_id, None, env.ana)

    await env.service.dispatch()

    _, to, subject, _ = env.mail.sent[0]
    assert to == "analista.ti@jdh.com.co"
    assert subject.startswith("[PRUEBA para carla@jdh.com.co] Nuevo caso asignado")


# --- Templates ---


def test_templates_use_singular_and_plural_and_skip_the_link_without_a_base_url() -> None:
    subject, body = templates.daily_summary("Ana", ["- P26GN0001"], 1, 0, MONDAY, None)
    assert "Quedaron 1 caso abierto sin resolver" in body and subject.endswith("1 caso abierto")
    assert "http" not in body

    subject, body = templates.overdue_reminder("Ana", ["- a"], [], None)
    assert subject == "PQRSF: 1 caso vencido, dale seguimiento"
    assert "Tienes 1 caso vencido a tu cargo" in body

    _, body = templates.overdue_reminder("Ana", [], ["- a"], None)
    assert "Hay 1 caso vencido sin responsable. Asígnalo o atiéndelo:" in body
    _, body = templates.overdue_reminder("Ana", [], ["- a", "- b"], None)
    assert "Hay 2 casos vencidos sin responsable. Asígnalos o atiéndelos:" in body


def test_the_actor_name_keeps_its_own_capitals() -> None:
    case = SimpleNamespace(
        code="P26GN0001",
        request_type=SimpleNamespace(name="Petición"),
        subject=None,
        requester_name=None,
        requester_email=None,
        customer_name=None,
        due_on=None,
    )

    _, body = templates.assignment(case, "Carla", "Analista TI", None, None)

    assert "Analista TI te asignó un caso" in body


def test_long_lists_are_cut_with_the_remainder_counted() -> None:
    lines = [f"- caso {n}" for n in range(45)]

    _, body = templates.daily_summary("Ana", lines, 45, 0, MONDAY, None)

    assert "- caso 29" in body and "- caso 30" not in body and "… y 15 más." in body


# --- Holidays API ---


def _nager(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NagerHolidayProvider(
        "CO", FallbackCalendar(), base_url="https://holidays.test/api", http=http
    )


class FallbackCalendar:
    def holidays(self, start, end):
        return {date(2026, 10, 12), date(2026, 12, 25)}


@pytest.mark.asyncio
async def test_holidays_come_from_the_api_and_are_cached_per_year() -> None:
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                {"date": "2026-10-12", "global": True},
                {"date": "2026-11-02", "global": False},  # one county only
            ],
        )

    provider = _nager(handler)

    assert await provider.is_business_day(date(2026, 10, 12)) is False
    assert await provider.is_business_day(date(2026, 11, 2)) is True
    assert await provider.is_business_day(date(2026, 10, 13)) is True
    assert await provider.is_business_day(SATURDAY) is False
    assert calls == ["https://holidays.test/api/PublicHolidays/2026/CO"]


@pytest.mark.asyncio
async def test_the_local_calendar_answers_when_the_api_is_down() -> None:
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    provider = _nager(handler)

    assert await provider.is_business_day(date(2026, 10, 12)) is False
    assert await provider.is_business_day(date(2026, 10, 13)) is True
    assert len(calls) == 1  # it does not hammer a failing API
