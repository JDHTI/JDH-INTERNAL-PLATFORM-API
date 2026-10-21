"""PostgreSQL repository for special cases."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import fields as dataclass_fields
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.supplier_supports.domain.special_cases import (
    SpecialCase,
    SpecialCaseData,
    SpecialCaseView,
)
from app.modules.supplier_supports.infrastructure.models import (
    SupplierSpecialCaseModel,
    SupplierSupportModel,
)

C = SupplierSpecialCaseModel
_FIELDS = tuple(item.name for item in dataclass_fields(SpecialCaseData))
_CODE = re.compile(r"^CE-(\d+)$")


def _case(model: SupplierSpecialCaseModel) -> SpecialCase:
    return SpecialCase(
        id=model.id,
        code=model.code,
        data=SpecialCaseData(**{name: getattr(model, name) for name in _FIELDS}),
        created_by=model.created_by,
        updated_by=model.updated_by,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemySpecialCaseRepository:
    """Store special cases with one short-lived session per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def _next_code(self, session: Any) -> str:
        codes = (await session.execute(select(C.code))).scalars().all()
        numbers = [int(match.group(1)) for code in codes if (match := _CODE.match(code))]
        return f"CE-{max(numbers, default=0) + 1:04d}"

    async def create(
        self, case_id: UUID, data: SpecialCaseData, actor: str, now: datetime
    ) -> SpecialCase:
        """Insert a case with the next ``CE-0001`` style code."""

        async with self.database.session_factory() as session:
            model = C(
                id=case_id,
                code=await self._next_code(session),
                created_by=actor,
                updated_by=actor,
                version=1,
                created_at=now,
                updated_at=now,
                **{name: getattr(data, name) for name in _FIELDS},
            )
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError(
                    "No se pudo guardar el caso (código o origen repetido)"
                ) from exc
            return _case(model)

    async def get(self, case_id: UUID) -> SpecialCase | None:
        """One case."""

        async with self.database.session_factory() as session:
            model = await session.get(C, case_id)
            return None if model is None else _case(model)

    async def get_by_source_key(self, source_key: str) -> SpecialCase | None:
        """The case created from a workbook source, if any."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(C).where(C.source_key == source_key))
            ).scalar_one_or_none()
            return None if model is None else _case(model)

    async def update(
        self,
        case_id: UUID,
        data: SpecialCaseData,
        expected_version: int,
        actor: str,
        now: datetime,
    ) -> SpecialCase:
        """Replace a case's data when its version still matches."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(select(C).where(C.id == case_id).with_for_update())
            ).scalar_one_or_none()
            if model is None:
                raise NotFoundError("Caso especial no encontrado")
            if model.version != expected_version:
                raise ConflictError("El caso cambió desde que lo abriste; recárgalo")
            for name in _FIELDS:
                setattr(model, name, getattr(data, name))
            model.version += 1
            model.updated_by = actor
            model.updated_at = now
            await session.commit()
            return _case(model)

    async def view(self, case_id: UUID) -> SpecialCaseView | None:
        """One case with its supplier and linked supports."""

        views, _ = await self.search(None, None, None, [case_id], 1, 1)
        return views[0] if views else None

    async def search(
        self,
        query: str | None,
        status: Sequence[str] | None,
        customer_nit: str | None,
        ids: Sequence[UUID] | None,
        page: int,
        size: int,
    ) -> tuple[list[SpecialCaseView], int]:
        """Page cases by text, status, client NIT or ids, newest first."""

        conditions = []
        if query:
            conditions.append(
                or_(
                    C.title.icontains(query, autoescape=True),
                    C.request.icontains(query, autoescape=True),
                    C.customer_name.icontains(query, autoescape=True),
                    C.code == query.upper(),
                )
            )
        if status:
            conditions.append(C.status.in_(status))
        if customer_nit:
            conditions.append(C.customer_nit == customer_nit)
        if ids:
            conditions.append(C.id.in_(ids))
        linked = (
            select(SupplierSupportModel.special_case_id, func.count().label("total"))
            .where(SupplierSupportModel.special_case_id.is_not(None))
            .group_by(SupplierSupportModel.special_case_id)
            .subquery()
        )
        async with self.database.session_factory() as session:
            total = (
                await session.execute(select(func.count()).select_from(C).where(*conditions))
            ).scalar_one()
            rows = (
                await session.execute(
                    select(
                        C,
                        CommercialSupplierModel.code,
                        CommercialSupplierModel.name,
                        func.coalesce(linked.c.total, 0),
                    )
                    .outerjoin(
                        CommercialSupplierModel,
                        CommercialSupplierModel.id == C.commercial_supplier_id,
                    )
                    .outerjoin(linked, linked.c.special_case_id == C.id)
                    .where(*conditions)
                    .order_by(C.created_at.desc(), C.code.desc())
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
        return (
            [
                SpecialCaseView(_case(model), code, name, int(count))
                for model, code, name, count in rows
            ],
            int(total),
        )


__all__ = ["SqlAlchemySpecialCaseRepository"]
