"""In-memory doubles of what the action plans service needs."""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.core.errors import ConflictError, InvalidInputError
from app.modules.action_plans.application.service import ActionPlanService
from app.modules.action_plans.domain.entities import Process
from app.modules.action_plans.domain.ports import CaseFacts
from tests.modules.fakes import principal

NOW = datetime(2026, 11, 17, 15, tzinfo=UTC)
PROCESSES = {code: Process(uuid.uuid4(), code, code.title()) for code in ("ALM", "CAL", "DIS")}


class Plans:
    """``ActionPlanRepository`` in memory, with the filters the service relies on."""

    def __init__(self) -> None:
        self.plans: dict = {}
        self.events: list = []
        self.files: dict = {}
        self.processes = {p.id: p for p in PROCESSES.values()}

    async def list_processes(self, include_inactive=False):
        return sorted(
            (p for p in self.processes.values() if include_inactive or p.is_active),
            key=lambda p: p.code,
        )

    async def get_process(self, process_id):
        return self.processes.get(process_id)

    async def get_process_by_code(self, code):
        return next((p for p in self.processes.values() if p.code == code), None)

    async def save_process(self, process):
        if any(p.code == process.code and p.id != process.id for p in self.processes.values()):
            raise ConflictError("repetido")
        self.processes[process.id] = process
        return process

    async def next_number(self):
        return max((p.number for p in self.plans.values()), default=200) + 1

    async def code_exists(self, code):
        return any(p.code == code for p in self.plans.values())

    async def create(self, plan, event):
        if await self.code_exists(plan.code):
            raise ConflictError("repetida")
        stored = copy.deepcopy(plan)
        stored.created_at = NOW
        self.plans[plan.id] = stored
        self.events.append(event)
        return copy.deepcopy(stored)

    async def save(self, plan, events):
        self.plans[plan.id] = copy.deepcopy(plan)
        self.events += list(events)
        return copy.deepcopy(plan)

    async def get(self, plan_id):
        plan = self.plans.get(plan_id)
        return copy.deepcopy(plan) if plan else None

    def _matches(self, plan, filters):
        if filters.visible_to and filters.visible_to not in plan.responsible_ids():
            return False
        if filters.status and plan.status != filters.status:
            return False
        if filters.case_id and plan.case_id != filters.case_id:
            return False
        return True

    async def list_plans(self, filters, offset, limit):
        found = [p for p in self.plans.values() if self._matches(p, filters)]
        return [copy.deepcopy(p) for p in found[offset : offset + limit]], len(found)

    async def all_plans(self, filters):
        return [copy.deepcopy(p) for p in self.plans.values() if self._matches(p, filters)]

    async def similar(self, reporter_nit, cause_label, exclude):
        return sum(
            1
            for p in self.plans.values()
            if p.id != exclude
            and p.reporter_nit == reporter_nit
            and (not cause_label or (p.cause_label or "").lower() == cause_label.lower())
        )

    async def delete(self, plan_id):
        self.plans.pop(plan_id, None)
        keys = [f.storage_key for f in self.files.values() if f.plan_id == plan_id]
        self.files = {k: f for k, f in self.files.items() if f.plan_id != plan_id}
        return keys

    async def add_event(self, event):
        self.events.append(event)

    async def list_events(self, plan_id):
        return [e for e in self.events if e.plan_id == plan_id]

    async def add_file(self, file):
        self.files[file.id] = file

    async def list_files(self, plan_id):
        return [f for f in self.files.values() if f.plan_id == plan_id]

    async def get_file(self, plan_id, file_id):
        found = self.files.get(file_id)
        return found if found and found.plan_id == plan_id else None

    async def delete_file(self, file_id):
        self.files.pop(file_id, None)


class Policy:
    """Who manages (Calidad) and who only reads the module."""

    def __init__(self) -> None:
        self.managers: set = set()
        self.readers: set = set()

    async def is_manager(self, p):
        return p.is_platform_admin or p.user_id in self.managers

    async def can_read(self, p):
        return await self.is_manager(p) or p.user_id in self.readers


class Cases:
    """The PQRSF case an action comes from."""

    def __init__(self) -> None:
        self.facts_value = CaseFacts(
            id=uuid.uuid4(),
            code="Q26BG0001",
            received_on=date(2026, 11, 3),
            customer_nit="860066942",
            customer_name="CAFAM",
            requester_name="Maribel Perilla",
            city="Funza",
            process_code="DIS",
            subject="Calidad del producto",
            description="Llegó producto vencido",
            cause_label="Producto vencido",
            has_photos=True,
            pickup_order="3847",
            om_codes=("1181BOG",),
        )
        self.linked: list = []

    async def facts(self, case_id, principal):
        return replace(self.facts_value, id=case_id)

    async def link(self, case_id, principal, plan_id, plan_code):
        self.linked.append((case_id, plan_code))


class Profiles:
    async def signer(self, user_id):
        return SimpleNamespace(name="Cristian Garzón", position="Analista de Calidad")


class Bucket:
    def __init__(self) -> None:
        self.objects: dict = {}

    async def put_object(self, key, body, content_type=None):
        self.objects[key] = body

    async def get_object(self, key):
        return self.objects[key]

    async def delete_object(self, key):
        self.objects.pop(key, None)


class Guard:
    max_attachment_bytes = 1_000

    def refuse(self, filename, size, count, total):
        return "tipo de archivo no permitido" if filename.endswith(".exe") else None

    async def clean(self, filename, content):
        if not content:
            raise InvalidInputError("archivo vacío")
        return filename, filename.rsplit(".", 1)[-1], "application/pdf", content


class Bell:
    def __init__(self) -> None:
        self.rung: list = []

    async def ring(self, user_ids, key, title, summary, path):
        self.rung.append((sorted(user_ids, key=str), title, path))


class World:
    """The service over the doubles, with Calidad, a reader, a responsible and a stranger."""

    def __init__(self) -> None:
        self.plans = Plans()
        self.policy = Policy()
        self.cases = Cases()
        self.bucket = Bucket()
        self.bell = Bell()
        self.service = ActionPlanService(
            repository=self.plans,
            policy=self.policy,
            cases=self.cases,
            profiles=Profiles(),
            storage=self.bucket,
            guard=Guard(),
            bell=self.bell,
            clock=lambda: NOW,
        )
        self.calidad = principal("calidad@jdh.com.co")
        self.reader = principal("lector@jdh.com.co")
        self.owner = principal("dueno@jdh.com.co")
        self.other = principal("otro@jdh.com.co")
        self.policy.managers.add(self.calidad.user_id)
        self.policy.readers.add(self.reader.user_id)
