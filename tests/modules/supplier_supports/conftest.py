"""In-memory ports for the supplier supports service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    CustomerRef,
    ProductRef,
    TargetMargin,
)
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.application.attachments import SupportAttachmentService
from app.modules.supplier_supports.application.bulk import SupportBulkService
from app.modules.supplier_supports.application.profiles import SupportProfileService
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.domain.attachments import (
    Attachment,
    FileUpload,
    StoredDocument,
    ViewLink,
)
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    LoadPlan,
    LoadResult,
    Support,
    SupportData,
    SupportRow,
    Trace,
)
from app.modules.supplier_supports.domain.filters import Group, SearchSpec
from app.modules.supplier_supports.domain.overlap import overlaps
from app.modules.supplier_supports.domain.profiles import Profile
from app.modules.supplier_supports.infrastructure.bulk_file import SpreadsheetSupportReader

TODAY = date(2026, 10, 9)
FAMILIA_ID = uuid4()
UNILEVER_ID = uuid4()
SKU_1128 = ProductRef(uuid4(), "1128", "Ph Jumbo Blanco HD 4x250mt", "FAMILIA", "233", True)
SKU_8749 = ProductRef(uuid4(), "8749", "Arroz Diana x 1Kg", "DIANA", "546", True)
# Pack factor and the warehouse's average unit cost, to infer the unit of a workbook cost.
SKU_3474 = ProductRef(
    uuid4(), "3474", "Maizena x720g", "MAIZENA", "399", True, Decimal("24"), Decimal("13782.70")
)
SKU_1001 = ProductRef(
    uuid4(),
    "1001",
    "Salsa de Tomate Fruco Gx4350g",
    "FRUCO",
    "399",
    True,
    Decimal("4"),
    Decimal("60245.58"),
)


class FakeLookup:
    """Suppliers, products and customers that exist in the masters."""

    def __init__(self) -> None:
        self.suppliers = {"233": FAMILIA_ID, "399": UNILEVER_ID}
        self.names = {FAMILIA_ID: ("233", "FAMILIA SANCELA"), UNILEVER_ID: ("399", "UNILEVER")}
        self.products = {
            "1128": SKU_1128,
            "8749": SKU_8749,
            "3474": SKU_3474,
            "1001": SKU_1001,
        }
        self.names_by_nit = {
            "800116299": "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS",
            "900430148": "GODDARD CATERING GROUP COLOMBIA",
            "830012863": "GODDARD CATERING GROUP BOGOTA",
        }
        # Branch ids by NIT as the master writes them; a NIT not listed has only 001.
        self.branches = {"800116299": ("001", "002", "015")}
        # Product margins by (SKU, list) and the list of each customer (Food service if not).
        self.margins = {
            ("1128", "margin_list"): Decimal("0.18"),
            ("8749", "margin_list"): Decimal("0.18"),
            ("1128", "margin_wholesale"): Decimal("0.10"),
            ("3474", "margin_list"): Decimal("0.20"),
        }
        self.margin_fields = {"900430148": "margin_wholesale"}
        # A supplier's own codes: Familia's 72163 is JDH 1128; 99999 maps to two products.
        self.supplier_codes = {
            (FAMILIA_ID, "72163"): [SKU_1128],
            (FAMILIA_ID, "99999"): [SKU_1128, SKU_8749],
        }
        self.groups = {
            "GRUPO GODDARD": [
                ("900430148", "GODDARD CATERING GROUP COLOMBIA"),
                ("830012863", "GODDARD CATERING GROUP BOGOTA"),
            ]
        }

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]:
        return {sku: self.products[sku] for sku in skus if sku in self.products}

    async def products_by_supplier_sku(self, supplier_id, supplier_skus):
        found = {}
        for sku in supplier_skus:
            products = self.supplier_codes.get((supplier_id, sku.strip().upper()))
            if products:
                found[sku.strip().upper()] = products
        return found

    async def business_group_members(self, groups):
        return {
            group: self.groups[group.upper()] for group in groups if group.upper() in self.groups
        }

    async def business_groups(self, query, limit):
        return [(name, len(items)) for name, items in self.groups.items() if query.upper() in name][
            :limit
        ]

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]:
        return {code: self.suppliers[code] for code in codes if code in self.suppliers}

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]:
        return []

    async def commercial_supplier_names(self, ids: Sequence[UUID]) -> dict[UUID, tuple[str, str]]:
        return {item: self.names[item] for item in ids if item in self.names}

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]:
        return {nit: self.names_by_nit[nit] for nit in nits if nit in self.names_by_nit}

    async def target_margins(self, nit, skus) -> dict[str, TargetMargin]:
        # Mayoristas for the wholesale customer, Food service for every other.
        field = self.margin_fields.get(nit or "", "margin_list")
        return {
            sku: TargetMargin(
                sku,
                self.products[sku].description,
                self.products[sku].commercial_supplier_code,
                field,
                self.margins.get((sku, field)),
                "UND",
            )
            for sku in skus
            if sku in self.products
        }

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerRef]:
        groups = {nit: name.title() for name, items in self.groups.items() for nit, _ in items}
        return {
            nit: CustomerRef(
                nit, self.names_by_nit[nit], groups.get(nit), self.branches.get(nit, ("001",))
            )
            for nit in nits
            if nit in self.names_by_nit
        }


def _matches(node, data: SupportData) -> bool:
    """A tiny evaluator of the filter tree: ``eq``/``in`` on the fields tests use."""

    if isinstance(node, Group):
        results = [_matches(item, data) for item in node.items]
        return all(results) if node.match == "all" else any(results)
    value = "233" if node.field == "supplier.code" else getattr(data, node.field)
    wanted = node.value if isinstance(node.value, list) else [node.value]
    return str(value).lower() in [str(item).lower() for item in wanted]


class FakeSupportRepository:
    """Keeps supports in a dict, with the version check of the real repository."""

    def __init__(self) -> None:
        self.items: dict[UUID, Support] = {}
        self.last_search: SearchSpec | None = None
        # What wrote the last version of each support (the real one queues it for the DWH).
        self.traces: dict[UUID, Trace] = {}
        self.loads: list[LoadPlan] = []

    async def live_in_groups(self, kinds):
        return [
            item
            for item in self.items.values()
            if item.data.status != "anulado" and item.data.is_enabled and item.data.kind in kinds
        ]

    async def pricing_context(self, product_sku, customer_nit):
        margins = {"margin_list": Decimal("0.20"), "margin_wholesale": Decimal("0.12")}
        return margins, Decimal("12"), "margin_list" if customer_nit else None, None

    async def overlapping(self, data: SupportData, exclude=()) -> list[Support]:
        return [
            item
            for item in self.items.values()
            if item.id not in set(exclude) and overlaps(data, item.data)
        ]

    async def supersede(self, items, actor: str, now: datetime) -> list[Support]:
        changed = []
        for item in items:
            current = self.items.get(item.support_id)
            if current is None:
                raise NotFoundError("Apoyo no encontrado")
            if item.version is not None and current.version != item.version:
                raise ConflictError("versión")
            if current.data.status == "anulado":
                raise ConflictError("ya está anulado")
            starts = current.data.valid_from
            if item.valid_to is not None and (starts is None or starts <= item.valid_to):
                data = replace(current.data, valid_to=item.valid_to)
                saved = replace(current, data=data, version=current.version + 1)
                self.traces[item.support_id] = Trace("vencimiento")
            else:
                data = replace(current.data, status="anulado")
                saved = replace(
                    current,
                    data=data,
                    void_reason=item.reason,
                    voided_at=now,
                    version=current.version + 1,
                )
                self.traces[item.support_id] = Trace("anulacion")
            self.items[item.support_id] = saved
            changed.append(saved)
        return changed

    async def create(
        self,
        support_id: UUID,
        data: SupportData,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
        supersede=(),
    ):
        await self.supersede(supersede, actor, now)
        if data.source_key and any(
            item.data.source_key == data.source_key for item in self.items.values()
        ):
            raise ConflictError("duplicado")
        support = Support(
            id=support_id,
            data=data,
            void_reason=None,
            voided_at=None,
            created_by=actor,
            updated_by=actor,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self.items[support_id] = support
        self.traces[support_id] = trace or Trace("manual")
        return support

    async def create_many(
        self, items, actor: str, now: datetime, trace: Trace | None = None, supersede=()
    ):
        keys = [data.source_key for _, data in items if data.source_key]
        if len(keys) != len(set(keys)):
            raise ConflictError("duplicado")
        await self.supersede(supersede, actor, now)
        # All or none, like the real transaction.
        return [
            await self.create(support_id, data, actor, now, trace or Trace("lote"))
            for support_id, data in items
        ]

    async def live_for_suppliers(self, supplier_ids) -> list[Support]:
        return [
            item
            for item in self.items.values()
            if item.data.commercial_supplier_id in set(supplier_ids)
            and item.data.status != "anulado"
        ]

    async def apply_load(self, plan: LoadPlan, actor: str, now: datetime) -> LoadResult:
        for support_id, version, _ in (*plan.updates, *plan.expirations):
            current = self.items.get(support_id)
            if current is None or current.version != version:
                raise ConflictError("versión")
        self.loads.append(plan)
        trace = Trace("carga", plan.load_id)
        await self.supersede(plan.retirements, actor, now)
        for support_id, data in plan.creates:
            await self.create(support_id, data, actor, now, trace)
        for support_id, version, data in plan.updates:
            await self.update(support_id, data, version, actor, now, trace)
        for support_id, version, last_day in plan.expirations:
            current = self.items[support_id]
            await self.update(
                support_id,
                replace(current.data, valid_to=last_day),
                version,
                actor,
                now,
                Trace("vencimiento", plan.load_id),
            )
        return LoadResult(
            load_id=plan.load_id,
            created=tuple(support_id for support_id, _ in plan.creates),
            updated=tuple(support_id for support_id, _, _ in plan.updates),
            expired=tuple(support_id for support_id, _, _ in plan.expirations),
            retired=tuple(item.support_id for item in plan.retirements),
        )

    async def get(self, support_id: UUID) -> Support | None:
        return self.items.get(support_id)

    async def get_row(self, support_id: UUID) -> SupportRow | None:
        support = self.items.get(support_id)
        return None if support is None else SupportRow(support=support, supplier_code="233")

    async def update(
        self, support_id, data, expected_version, actor, now, trace=None, supersede=()
    ):
        current = self.items.get(support_id)
        if current is None:
            raise NotFoundError("Apoyo no encontrado")
        if current.version != expected_version:
            raise ConflictError("versión")
        await self.supersede(supersede, actor, now)
        saved = replace(
            current, data=data, version=current.version + 1, updated_by=actor, updated_at=now
        )
        self.items[support_id] = saved
        self.traces[support_id] = trace or Trace("edicion")
        return saved

    async def void(self, support_id, reason, expected_version, actor, now, trace=None):
        self.traces[support_id] = trace or Trace("anulacion")
        current = self.items[support_id]
        saved = replace(
            current,
            data=replace(current.data, status="anulado"),
            void_reason=reason,
            voided_at=now,
            version=current.version + 1,
            updated_by=actor,
        )
        self.items[support_id] = saved
        return saved

    async def search(self, spec: SearchSpec):
        self.last_search = spec
        rows = [
            SupportRow(support=item, supplier_code="233")
            for item in self.items.values()
            if spec.include_voided or item.data.status != "anulado"
        ]
        return rows, len(rows)

    async def search_ids(self, spec: SearchSpec, limit: int) -> list[UUID]:
        self.last_search = spec
        return [
            item.id
            for item in self.items.values()
            if item.data.status != "anulado" and _matches(spec.group, item.data)
        ][:limit]

    async def get_by_source_key(self, source_key: str) -> Support | None:
        return next(
            (item for item in self.items.values() if item.data.source_key == source_key), None
        )

    async def existing_source_keys(self, keys: Sequence[str]) -> set[str]:
        return {
            item.data.source_key
            for item in self.items.values()
            if item.data.source_key in keys and item.data.source_key
        }

    async def conflicts(self, support_id: UUID) -> list[Conflict]:
        return []

    async def scope_values(self) -> dict[str, list[str]]:
        return {"regionals": ["ANTIOQUIA"], "business_units": [], "channels": [], "programs": []}


class FakeProfileRepository:
    """Keeps profiles by supplier id, with the version check of the real repository."""

    def __init__(self, lookup: FakeLookup) -> None:
        self.lookup = lookup
        self.items: dict[UUID, Profile] = {}

    def _named(self, supplier_id: UUID, **changes) -> dict:
        code, name = self.lookup.names[supplier_id]
        return {"supplier_code": code, "supplier_name": name, **changes}

    async def list(self) -> list[Profile]:
        return list(self.items.values())

    async def get(self, supplier_id: UUID) -> Profile | None:
        return self.items.get(supplier_id)

    async def create(self, profile_id, supplier_id, data, actor, now):
        if supplier_id in self.items:
            raise ConflictError("El proveedor ya tiene un perfil; edítalo")
        profile = Profile(
            id=profile_id,
            commercial_supplier_id=supplier_id,
            data=data,
            created_by=actor,
            updated_by=actor,
            version=1,
            created_at=now,
            updated_at=now,
            **self._named(supplier_id),
        )
        self.items[supplier_id] = profile
        return profile

    async def update(self, supplier_id, data, expected_version, actor, now):
        current = self.items[supplier_id]
        if current.version != expected_version:
            raise ConflictError("El perfil cambió desde que lo abriste; recárgalo")
        saved = replace(
            current, data=data, version=current.version + 1, updated_by=actor, updated_at=now
        )
        self.items[supplier_id] = saved
        return saved

    async def delete(self, supplier_id: UUID) -> bool:
        return self.items.pop(supplier_id, None) is not None


class FakeDocumentStore:
    """The supports folder of the document manager, in memory."""

    def __init__(self) -> None:
        self.documents: dict[UUID, tuple[str, FileUpload, list[str]]] = {}
        self.foreign: set[UUID] = set()  # documents that exist in another folder
        self.fail_on: str | None = None
        self.tags: list[list[str]] = []

    async def store(self, principal, upload, *, name, description, tags):
        if self.fail_on and upload.filename == self.fail_on:
            raise InvalidInputError("Tipo de archivo no permitido")
        document = StoredDocument(uuid4(), name or upload.filename or "soporte")
        self.documents[document.id] = (document.name, upload, list(tags))
        self.tags.append(list(tags))
        return document

    async def fetch(self, principal, document_id):
        if document_id in self.foreign:
            raise InvalidInputError("El documento no está en la carpeta soporte_apoyos")
        if document_id not in self.documents:
            raise NotFoundError("Documento no encontrado")
        return StoredDocument(document_id, self.documents[document_id][0])

    async def discard(self, principal, document_id):
        self.documents.pop(document_id, None)

    async def view_link(self, principal, document_id, *, inline):
        name = self.documents[document_id][0]
        suffix = "inline" if inline else "attachment"
        return ViewLink(
            url=f"https://s3.test/{document_id}?{suffix}", filename=name, expires_in=300
        )


class FakeAttachmentRepository:
    """Links in a set; metadata comes from the fake document store."""

    def __init__(self, repo: FakeSupportRepository, store: FakeDocumentStore) -> None:
        self.repo = repo
        self.store = store
        self.links: dict[tuple[UUID, UUID], str] = {}

    async def link_many(self, pairs, actor, now):
        new = [pair for pair in pairs if pair not in self.links]
        self.links.update({pair: actor for pair in new})
        return len(new)

    async def unlink(self, support_id, document_id):
        return self.links.pop((support_id, document_id), None) is not None

    async def get_link(self, support_id, document_id):
        return next(
            (
                item
                for item in await self.list_for_support(support_id)
                if item.document_id == document_id
            ),
            None,
        )

    async def list_for_support(self, support_id):
        found = []
        for (support, document_id), actor in self.links.items():
            if support != support_id:
                continue
            name, upload, _ = self.store.documents[document_id]
            found.append(
                Attachment(
                    document_id=document_id,
                    name=name,
                    original_filename=upload.filename or "",
                    extension=(upload.filename or "x.png").rsplit(".", 1)[-1],
                    content_type="image/png",
                    size_bytes=len(upload.content),
                    uploaded_by=actor,
                    uploaded_at=datetime(2026, 10, 9, tzinfo=UTC),
                    linked_by=actor,
                    linked_at=datetime(2026, 10, 9, tzinfo=UTC),
                    support_count=sum(1 for (_, doc) in self.links if doc == document_id),
                )
            )
        return found

    async def existing_supports(self, ids):
        return {
            item
            for item in ids
            if item in self.repo.items and self.repo.items[item].data.status != "anulado"
        }

    async def supplier_codes(self, ids):
        return ["233"] if ids else []


class FakeManagers:
    """Admins and a fixed set of e-mails manage supports."""

    def __init__(self, emails: Sequence[str] = ()) -> None:
        self.emails = set(emails)

    async def is_manager(self, principal: Principal) -> bool:
        return principal.is_platform_admin or principal.email in self.emails

    async def can_read(self, principal: Principal) -> bool:
        return principal.is_moderator or await self.is_manager(principal)


class FixedCalendar:
    def today(self) -> date:
        return TODAY


def person(email: str = "ana@jdh.com.co", admin: bool = False) -> Principal:
    return Principal(user_id=uuid4(), email=email, display_name=email, is_platform_admin=admin)


@pytest.fixture
def repo() -> FakeSupportRepository:
    return FakeSupportRepository()


@pytest.fixture
def lookup() -> FakeLookup:
    return FakeLookup()


@pytest.fixture
def service(repo, lookup) -> SupplierSupportService:
    return SupplierSupportService(
        repository=repo,
        masters=lookup,
        managers=FakeManagers(["comercial@jdh.com.co"]),
        calendar=FixedCalendar(),
    )


@pytest.fixture
def profile_repo(lookup) -> FakeProfileRepository:
    return FakeProfileRepository(lookup)


@pytest.fixture
def profiles(profile_repo, lookup) -> SupportProfileService:
    return SupportProfileService(
        repository=profile_repo, masters=lookup, managers=FakeManagers(["comercial@jdh.com.co"])
    )


@pytest.fixture
def bulk(service, profiles) -> SupportBulkService:
    return SupportBulkService(
        supports=service, profiles=profiles, reader=SpreadsheetSupportReader(), max_rows=50
    )


@pytest.fixture
def doc_store() -> FakeDocumentStore:
    return FakeDocumentStore()


@pytest.fixture
def attachments(service, repo, doc_store) -> SupportAttachmentService:
    return SupportAttachmentService(
        supports=service,
        attachments=FakeAttachmentRepository(repo, doc_store),
        store=doc_store,
        max_supports=5,
        max_file_bytes=1024,
    )


@pytest.fixture
def manager() -> Principal:
    return person("comercial@jdh.com.co")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 10, 9, 12, tzinfo=UTC)
