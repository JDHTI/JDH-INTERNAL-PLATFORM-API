"""Use cases of the special cases (requests for manual configuration and future code)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain import vocabulary
from app.modules.supplier_supports.domain.ports import ManagerPolicy, MasterLookup
from app.modules.supplier_supports.domain.special_cases import (
    SpecialCase,
    SpecialCaseData,
    SpecialCasePage,
    SpecialCaseView,
)

MAX_PAGE_SIZE = 200
IMPORT_ACTOR = "importacion"
EDITABLE = frozenset(
    {
        "title",
        "request",
        "rule_to_program",
        "status",
        "priority",
        "assignee",
        "resolution",
        "customer_nit",
        "customer_name",
    }
)


class SpecialCaseRepository(Protocol):
    """Persistence of special cases."""

    async def create(
        self, case_id: UUID, data: SpecialCaseData, actor: str, now: datetime
    ) -> SpecialCase: ...

    async def get(self, case_id: UUID) -> SpecialCase | None: ...

    async def get_by_source_key(self, source_key: str) -> SpecialCase | None: ...

    async def update(
        self,
        case_id: UUID,
        data: SpecialCaseData,
        expected_version: int,
        actor: str,
        now: datetime,
    ) -> SpecialCase: ...

    async def view(self, case_id: UUID) -> SpecialCaseView | None: ...

    async def search(
        self,
        query: str | None,
        status: Sequence[str] | None,
        customer_nit: str | None,
        ids: Sequence[UUID] | None,
        page: int,
        size: int,
    ) -> tuple[list[SpecialCaseView], int]: ...


def _clean(value: str | None) -> str | None:
    return " ".join(value.split()) or None if value else None


class SpecialCaseService:
    """Create, follow and search special cases."""

    def __init__(
        self,
        repository: SpecialCaseRepository,
        masters: MasterLookup,
        managers: ManagerPolicy,
    ) -> None:
        """Create the service.

        Args:
            repository (SpecialCaseRepository): Case persistence port.
            masters (MasterLookup): Resolves suppliers and customers.
            managers (ManagerPolicy): Decides who may write.
        """

        self.repository = repository
        self.masters = masters
        self.managers = managers

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar casos especiales")

    @staticmethod
    def _check(data: SpecialCaseData) -> None:
        if not data.title or not data.request:
            raise InvalidInputError("El caso requiere título y petición")
        if data.status not in vocabulary.SPECIAL_CASE_STATUSES:
            raise InvalidInputError(f"Estado inválido: {data.status!r}")
        if data.priority not in vocabulary.SPECIAL_CASE_PRIORITIES:
            raise InvalidInputError(f"Prioridad inválida: {data.priority!r}")

    async def _resolve(
        self, data: SpecialCaseData, supplier_code: str | None, *, strict: bool
    ) -> SpecialCaseData:
        supplier_id = data.commercial_supplier_id
        if supplier_code:
            found = await self.masters.commercial_suppliers_by_code([supplier_code.strip()])
            supplier_id = found.get(supplier_code.strip())
            if supplier_id is None and strict:
                raise InvalidInputError(f"Proveedor comercial desconocido: {supplier_code}")
        nit = clean_nit(data.customer_nit)
        name = _clean(data.customer_name)
        if nit:
            known = await self.masters.customer_names([nit])
            if nit not in known and strict:
                raise InvalidInputError(f"NIT de cliente desconocido: {nit}")
            name = name or known.get(nit) or None
        return replace(
            data,
            commercial_supplier_id=supplier_id,
            customer_nit=nit,
            customer_name=name,
            title=_clean(data.title) or "",
            request=_clean(data.request) or "",
            rule_to_program=_clean(data.rule_to_program),
            assignee=_clean(data.assignee),
            resolution=_clean(data.resolution),
        )

    async def search(
        self,
        *,
        q: str | None = None,
        status: Sequence[str] | None = None,
        customer_nit: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> SpecialCasePage:
        """Page cases by text, status and client NIT."""

        if page < 1 or not 1 <= size <= MAX_PAGE_SIZE:
            raise InvalidInputError(f"Paginación inválida (tamaño 1 a {MAX_PAGE_SIZE})")
        unknown = [item for item in status or () if item not in vocabulary.SPECIAL_CASE_STATUSES]
        if unknown:
            raise InvalidInputError(f"Estado inválido: {', '.join(unknown)}")
        items, total = await self.repository.search(
            _clean(q), status, clean_nit(customer_nit), None, page, size
        )
        return SpecialCasePage(items=items, total=total, page=page, size=size)

    async def get(self, case_id: UUID) -> SpecialCaseView:
        """One case with its supplier and linked supports."""

        view = await self.repository.view(case_id)
        if view is None:
            raise NotFoundError("Caso especial no encontrado")
        return view

    async def create(
        self, principal: Principal, data: SpecialCaseData, *, supplier_code: str | None = None
    ) -> SpecialCaseView:
        """Open a case. Managers only."""

        await self._require_manager(principal)
        prepared = await self._resolve(data, supplier_code, strict=True)
        self._check(prepared)
        saved = await self.repository.create(uuid4(), prepared, principal.email, datetime.now(UTC))
        return await self.get(saved.id)

    async def update(
        self,
        principal: Principal,
        case_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, Any],
        supplier_code: str | None = None,
    ) -> SpecialCaseView:
        """Change only the fields sent; a stale version is a conflict."""

        await self._require_manager(principal)
        unknown = sorted(set(changes) - EDITABLE)
        if unknown:
            raise InvalidInputError(f"Campos no editables: {', '.join(unknown)}")
        current = await self.repository.get(case_id)
        if current is None:
            raise NotFoundError("Caso especial no encontrado")
        if current.version != expected_version:
            raise ConflictError("El caso cambió desde que lo abriste; recárgalo")
        merged = replace(current.data, **changes)
        prepared = await self._resolve(merged, supplier_code, strict=True)
        self._check(prepared)
        saved = await self.repository.update(
            case_id, prepared, expected_version, principal.email, datetime.now(UTC)
        )
        return await self.get(saved.id)

    async def ensure(
        self, data: SpecialCaseData, *, supplier_code: str | None, dry_run: bool = False
    ) -> UUID | None:
        """Create the case of a workbook source if it does not exist; used by the importer.

        Returns:
            UUID | None: The case id (``None`` in a dry run that would create it).
        """

        if not data.source_key:
            raise InvalidInputError("El caso importado requiere source_key")
        existing = await self.repository.get_by_source_key(data.source_key)
        if existing is not None:
            return existing.id
        if dry_run:
            return None
        prepared = await self._resolve(data, supplier_code, strict=False)
        self._check(prepared)
        saved = await self.repository.create(uuid4(), prepared, IMPORT_ACTOR, datetime.now(UTC))
        return saved.id
