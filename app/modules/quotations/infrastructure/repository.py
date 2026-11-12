"""PostgreSQL repository of quotations and their lines."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, delete, func, or_, select, update

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    SellerContact,
    SupportCandidate,
    SupportOption,
    TemporaryCustomer,
)
from app.modules.quotations.domain.vocabulary import QUOTATION
from app.modules.quotations.infrastructure.models import QuotationLineModel, QuotationModel

Q = QuotationModel
L = QuotationLineModel
_HEADER_FIELDS = tuple(item.name for item in fields(Quotation))
_LINE_SPECIAL = {"support", "support_candidates", "support_options", "problems"}
_LINE_FIELDS = tuple(item.name for item in fields(QuotationLine) if item.name not in _LINE_SPECIAL)
_CHANGED = "La cotización cambió desde que la abriste; recárgala"


def _header_values(quotation: Quotation) -> dict[str, Any]:
    values = {name: getattr(quotation, name) for name in _HEADER_FIELDS}
    values["warnings"] = list(quotation.warnings)
    values["conditions"] = list(quotation.conditions)
    values["cost_centers"] = list(quotation.cost_centers)
    values["seller_contact"] = (
        quotation.seller_contact.as_dict() if quotation.seller_contact else None
    )
    values["temporary_customer"] = (
        quotation.temporary_customer.as_dict() if quotation.temporary_customer else None
    )
    return values


def _quotation(model: QuotationModel) -> Quotation:
    values = {name: getattr(model, name) for name in _HEADER_FIELDS}
    values["warnings"] = tuple(model.warnings or ())
    values["conditions"] = tuple(model.conditions or ())
    values["cost_centers"] = tuple(model.cost_centers or ())
    values["seller_contact"] = (
        SellerContact.from_dict(model.seller_contact) if model.seller_contact else None
    )
    values["temporary_customer"] = (
        TemporaryCustomer.from_dict(model.temporary_customer) if model.temporary_customer else None
    )
    return Quotation(**values)


def _line_values(quotation_id: UUID, line: QuotationLine) -> dict[str, Any]:
    values = {name: getattr(line, name) for name in _LINE_FIELDS}
    values["quotation_id"] = quotation_id
    values["support"] = line.support.as_dict() if line.support else None
    values["support_candidates"] = [item.as_dict() for item in line.support_candidates]
    values["support_options"] = [item.as_dict() for item in line.support_options]
    values["problems"] = list(line.problems)
    return values


def _line(model: QuotationLineModel) -> QuotationLine:
    return QuotationLine(
        **{name: getattr(model, name) for name in _LINE_FIELDS},
        support=AppliedSupport.from_dict(model.support) if model.support else None,
        support_candidates=tuple(
            SupportCandidate.from_dict(item) for item in model.support_candidates or ()
        ),
        support_options=tuple(
            SupportOption.from_dict(item) for item in model.support_options or ()
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
        visible_to: tuple[UUID, Sequence[str]] | None = None,
        kind: str = QUOTATION,
    ) -> QuotationPage:
        """Quotations of one kind, newest first.

        Args:
            kind (str): ``cotizacion`` or ``lista`` (list updates).
            visible_to (tuple[UUID, Sequence[str]] | None): ``(user, salesperson codes)``:
                only the ones the user created or whose customer is of those salespeople.
            created_by_id (UUID | None): Only the ones this user created.
            status (str | None): Only this status.
            customer_nit (str | None): Only this customer.
            q (str | None): NIT prefix, part of the customer's name or of the price list's
                name, or the price list code.
            page (int): 1-based page.
            size (int): Page size.

        Returns:
            QuotationPage: The page.
        """

        conditions = [Q.kind == kind]
        if visible_to is not None:
            owner, codes = visible_to
            conditions.append(
                or_(Q.created_by_id == owner, Q.salesperson_code.in_(list(codes)))
                if codes
                else Q.created_by_id == owner
            )
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
                    Q.price_list_name.icontains(term, autoescape=True),
                    func.upper(Q.price_list_id) == term.upper(),
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
        inserted: Sequence[QuotationLine] = (),
        removed: Sequence[UUID] = (),
    ) -> QuotationDetail:
        """Write the header and lines if nobody changed the quotation meanwhile.

        Args:
            quotation (Quotation): The new header; its ``version`` is stored as given.
            lines (Sequence[QuotationLine]): Lines to update by id, or every line when
                ``replace_lines``.
            expected_version (int): The version the caller read.
            replace_lines (bool): Delete the current lines and insert ``lines``.
            inserted (Sequence[QuotationLine]): New lines to insert.
            removed (Sequence[UUID]): Lines to delete.

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
            elif lines:
                # One executemany for every changed line, not a statement per line.
                rows = []
                for line in lines:
                    data = _line_values(quotation.id, line)
                    data["line_id"] = data.pop("id")
                    data["owner_id"] = data.pop("quotation_id")
                    rows.append(data)
                table = L.__table__
                await session.execute(
                    update(table).where(
                        table.c.id == bindparam("line_id"),
                        table.c.quotation_id == bindparam("owner_id"),
                    ),
                    rows,
                )
            if removed:
                await session.execute(
                    delete(L).where(L.quotation_id == quotation.id, L.id.in_(list(removed)))
                )
            if inserted:
                await session.execute(
                    L.__table__.insert(), [_line_values(quotation.id, line) for line in inserted]
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

    async def set_categories(self, quotation_id: UUID, categories: dict[UUID, str]) -> None:
        """Store the category of some lines, by line id. The header is not touched: no new
        version, no new ``updated_at``.

        Args:
            quotation_id (UUID): Quotation the lines belong to.
            categories (dict[UUID, str]): Category by line id.
        """

        if not categories:
            return
        table = L.__table__
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                update(table).where(
                    table.c.id == bindparam("line_id"),
                    table.c.quotation_id == bindparam("owner_id"),
                ),
                [
                    {"line_id": line_id, "owner_id": quotation_id, "category": category}
                    for line_id, category in categories.items()
                ],
            )


__all__ = ["SqlAlchemyQuotationRepository"]
