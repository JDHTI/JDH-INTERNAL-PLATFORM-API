"""PostgreSQL repository of quotations and their lines."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    SupportCandidate,
)
from app.modules.quotations.infrastructure.models import QuotationLineModel, QuotationModel

Q = QuotationModel
L = QuotationLineModel
_HEADER_FIELDS = tuple(item.name for item in fields(Quotation))
_LINE_SPECIAL = {"support", "support_candidates", "problems"}
_LINE_FIELDS = tuple(item.name for item in fields(QuotationLine) if item.name not in _LINE_SPECIAL)
_CHANGED = "La cotización cambió desde que la abriste; recárgala"


def _header_values(quotation: Quotation) -> dict[str, Any]:
    values = {name: getattr(quotation, name) for name in _HEADER_FIELDS}
    values["warnings"] = list(quotation.warnings)
    return values


def _quotation(model: QuotationModel) -> Quotation:
    values = {name: getattr(model, name) for name in _HEADER_FIELDS}
    values["warnings"] = tuple(model.warnings or ())
    return Quotation(**values)


def _line_values(quotation_id: UUID, line: QuotationLine) -> dict[str, Any]:
    values = {name: getattr(line, name) for name in _LINE_FIELDS}
    values["quotation_id"] = quotation_id
    values["support"] = line.support.as_dict() if line.support else None
    values["support_candidates"] = [item.as_dict() for item in line.support_candidates]
    values["problems"] = list(line.problems)
    return values


def _line(model: QuotationLineModel) -> QuotationLine:
    return QuotationLine(
        **{name: getattr(model, name) for name in _LINE_FIELDS},
        support=AppliedSupport.from_dict(model.support) if model.support else None,
        support_candidates=tuple(
            SupportCandidate.from_dict(item) for item in model.support_candidates or ()
        ),
        problems=tuple(model.problems or ()),
    )


class SqlAlchemyQuotationRepository:
    """Quotations with optimistic locking on ``version``."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def create(self, quotation: Quotation, lines: Sequence[QuotationLine]) -> QuotationDetail:
        """Store a new quotation with its lines in one transaction.

        Args:
            quotation (Quotation): The header (version 1).
            lines (Sequence[QuotationLine]): Its lines.

        Returns:
            QuotationDetail: What was stored.
        """

        async with self.database.session_factory() as session, session.begin():
            session.add(Q(**_header_values(quotation)))
            await session.flush()
            if lines:
                await session.execute(
                    L.__table__.insert(), [_line_values(quotation.id, line) for line in lines]
                )
        return QuotationDetail(quotation, tuple(sorted(lines, key=lambda item: item.position)))

    async def get(self, quotation_id: UUID) -> QuotationDetail | None:
        """A quotation with its lines in position order.

        Args:
            quotation_id (UUID): Quotation.

        Returns:
            QuotationDetail | None: ``None`` when it does not exist.
        """

        async with self.database.session_factory() as session:
            model = await session.get(Q, quotation_id)
            if model is None:
                return None
            rows = (
                await session.scalars(
                    select(L).where(L.quotation_id == quotation_id).order_by(L.position)
                )
            ).all()
        return QuotationDetail(_quotation(model), tuple(_line(row) for row in rows))

    async def list(
        self,
        *,
        created_by_id: UUID | None,
        status: str | None,
        customer_nit: str | None,
        q: str | None,
        page: int,
        size: int,
    ) -> QuotationPage:
        """Quotations, newest first.

        Args:
            created_by_id (UUID | None): Only the ones this user created.
            status (str | None): Only this status.
            customer_nit (str | None): Only this customer.
            q (str | None): NIT prefix or part of the customer's name.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            QuotationPage: The page.
        """

        conditions = []
        if created_by_id is not None:
            conditions.append(Q.created_by_id == created_by_id)
        if status:
            conditions.append(Q.status == status)
        if customer_nit:
            conditions.append(Q.customer_nit == customer_nit)
        if q:
            term = q.strip()
            conditions.append(
                or_(
                    Q.customer_nit.startswith(term, autoescape=True),
                    Q.customer_name.icontains(term, autoescape=True),
                )
            )
        async with self.database.session_factory() as session:
            total = (
                await session.execute(select(func.count()).select_from(Q).where(*conditions))
            ).scalar_one()
            rows = (
                await session.scalars(
                    select(Q)
                    .where(*conditions)
                    .order_by(Q.updated_at.desc(), Q.id)
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
        return QuotationPage(tuple(_quotation(row) for row in rows), int(total), page, size)

    async def save(
        self,
        quotation: Quotation,
        lines: Sequence[QuotationLine],
        expected_version: int,
        replace_lines: bool = False,
    ) -> QuotationDetail:
        """Write the header and lines if nobody changed the quotation meanwhile.

        Args:
            quotation (Quotation): The new header; its ``version`` is stored as given.
            lines (Sequence[QuotationLine]): Lines to update by id, or every line when
                ``replace_lines``.
            expected_version (int): The version the caller read.
            replace_lines (bool): Delete the current lines and insert ``lines``.

        Returns:
            QuotationDetail: The quotation as stored.

        Raises:
            NotFoundError: It does not exist.
            ConflictError: Its version is not ``expected_version`` any more.
        """

        async with self.database.session_factory() as session, session.begin():
            values = _header_values(quotation)
            values.pop("id")
            result = await session.execute(
                update(Q)
                .where(Q.id == quotation.id, Q.version == expected_version)
                .values(**values)
            )
            if result.rowcount == 0:
                exists = await session.get(Q, quotation.id)
                if exists is None:
                    raise NotFoundError("Cotización no encontrada")
                raise ConflictError(_CHANGED)
            if replace_lines:
                await session.execute(delete(L).where(L.quotation_id == quotation.id))
                if lines:
                    await session.execute(
                        L.__table__.insert(), [_line_values(quotation.id, line) for line in lines]
                    )
            else:
                for line in lines:
                    data = _line_values(quotation.id, line)
                    data.pop("id")
                    await session.execute(
                        update(L)
                        .where(L.id == line.id, L.quotation_id == quotation.id)
                        .values(**data)
                    )
        detail = await self.get(quotation.id)
        if detail is None:  # pragma: no cover - deleted right after the commit
            raise NotFoundError("Cotización no encontrada")
        return detail

    async def delete(self, quotation_id: UUID) -> bool:
        """Delete a quotation and its lines.

        Args:
            quotation_id (UUID): Quotation.

        Returns:
            bool: Whether it existed.
        """

        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(delete(Q).where(Q.id == quotation_id))
        return bool(result.rowcount)


__all__ = ["SqlAlchemyQuotationRepository"]
