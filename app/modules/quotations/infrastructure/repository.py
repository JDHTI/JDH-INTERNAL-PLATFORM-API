"""PostgreSQL repository of quotations and their lines."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import fields
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, delete, func, or_, select, update

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.quotations.domain.entities import (
    AppliedSupport,
    PriceListLoad,
    Quotation,
    QuotationDetail,
    QuotationLine,
    QuotationPage,
    SellerContact,
    StagedItem,
    SupportCandidate,
    SupportOption,
    TemporaryCustomer,
)
from app.modules.quotations.domain.vocabulary import DRAFT, LIST_KINDS, QUOTATION, VALIDATED
from app.modules.quotations.infrastructure.models import (
    PriceListLoadModel,
    QuotationLineModel,
    QuotationModel,
)

Q = QuotationModel
L = QuotationLineModel
_HEADER_FIELDS = tuple(item.name for item in fields(Quotation))
_LINE_SPECIAL = {"support", "support_candidates", "support_options", "problems"}
_LINE_FIELDS = tuple(item.name for item in fields(QuotationLine) if item.name not in _LINE_SPECIAL)
_CHANGED = "La cotización cambió desde que la abriste; recárgala"
# Header fields stored as ``{center: [codes]}``.
_BY_CENTER = ("cost_warehouses", "promo_warehouses", "warehouse_groups")


def _header_values(quotation: Quotation) -> dict[str, Any]:
    values = {name: getattr(quotation, name) for name in _HEADER_FIELDS}
    values["warnings"] = list(quotation.warnings)
    values["conditions"] = list(quotation.conditions)
    values["cost_centers"] = list(quotation.cost_centers)
    values["excluded_skus"] = list(quotation.excluded_skus)
    for name in _BY_CENTER:
        values[name] = {center: list(codes) for center, codes in getattr(quotation, name).items()}
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
    values["excluded_skus"] = tuple(model.excluded_skus or ())
    for name in _BY_CENTER:
        values[name] = {
            center: tuple(codes) for center, codes in (getattr(model, name) or {}).items()
        }
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


def _load(model: PriceListLoadModel) -> PriceListLoad:
    return PriceListLoad(
        id=model.id,
        kind=model.kind,
        price_list_id=model.price_list_id,
        price_list_name=model.price_list_name,
        activation_date=model.activation_date,
        line_count=model.line_count,
        rows=tuple(model.rows or ()),
        created_by=model.created_by,
        created_at=model.created_at,
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

    async def get_lines(self, quotation_id: UUID, skus: Sequence[str]) -> QuotationDetail | None:
        """A quotation with only the lines of ``skus``: a few rows of a large list update
        (list 1 has thousands) without reading the rest.

        Args:
            quotation_id (UUID): Quotation.
            skus (Sequence[str]): The SKUs wanted; none reads only the header.

        Returns:
            QuotationDetail | None: ``None`` when it does not exist.
        """

        async with self.database.session_factory() as session:
            model = await session.get(Q, quotation_id)
            if model is None:
                return None
            rows = []
            if skus:
                rows = (
                    await session.scalars(
                        select(L)
                        .where(L.quotation_id == quotation_id, L.sku.in_(list(skus)))
                        .order_by(L.position)
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
        created_by_ids: Collection[UUID] | None = None,
    ) -> QuotationPage:
        """Quotations of one kind, newest first.

        Args:
            kind (str): ``cotizacion``, ``lista`` or ``principal`` (list updates).
            created_by_ids (Collection[UUID] | None): Only the ones these users created (the
                call center's leader and its members).
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
        if created_by_ids is not None:
            conditions.append(Q.created_by_id.in_(list(created_by_ids)))
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
        """Write the header and lines if nobody changed the quotation meanwhile, and read it
        back (see :meth:`write`).

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

        await self.write(quotation, lines, expected_version, replace_lines, inserted, removed)
        detail = await self.get(quotation.id)
        if detail is None:  # pragma: no cover - deleted right after the commit
            raise NotFoundError("Cotización no encontrada")
        return detail

    async def write(
        self,
        quotation: Quotation,
        lines: Sequence[QuotationLine],
        expected_version: int,
        replace_lines: bool = False,
        inserted: Sequence[QuotationLine] = (),
        removed: Sequence[UUID] = (),
    ) -> None:
        """:meth:`save` without reading the quotation back: for a few rows of a large list
        update, whose caller already has what it wrote.

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

    async def delete_stale_drafts(self, created_before: datetime) -> list[UUID]:
        """Delete the customer quotations still in draft that were never sent to validation
        (no ``confirmed_at``) and were created before ``created_before`` (2026-11-23).

        Returns:
            list[UUID]: The deleted quotations.
        """

        async with self.database.session_factory() as session, session.begin():
            result = await session.execute(
                delete(Q)
                .where(
                    Q.kind == QUOTATION,
                    Q.status == DRAFT,
                    Q.confirmed_at.is_(None),
                    Q.created_at < created_before,
                )
                .returning(Q.id)
            )
            return list(result.scalars())

    async def reserved_list_ids(self) -> list[str]:
        """Codes of the new lists already validated (pending load or loaded)."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(Q.export_price_list_id)
                .where(
                    Q.list_action == "creacion",
                    Q.status.in_(("pendiente_carga", "cargada")),
                    Q.export_price_list_id.is_not(None),
                )
                .distinct()
            )
            return [code for (code,) in rows.all()]

    async def staged(self, kind: str, price_list_id: str | None = None) -> list[StagedItem]:
        """Validated items not loaded yet of every update of a kind (the staging).

        Args:
            kind (str): ``lista`` or ``principal``.
            price_list_id (str | None): Only this list.

        Returns:
            list[StagedItem]: By list, then by update and position.
        """

        conditions = [
            Q.kind == kind,
            L.review_status == VALIDATED,
            L.load_id.is_(None),
            L.new_price.is_not(None),
        ]
        if price_list_id is not None:
            conditions.append(Q.price_list_id == price_list_id)
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(L, Q.id, Q.version, Q.customer_name, Q.price_list_id, Q.price_list_name)
                    .join(Q, Q.id == L.quotation_id)
                    .where(*conditions)
                    .order_by(Q.price_list_id, Q.created_at, L.position)
                )
            ).all()
        return [
            StagedItem(
                quotation_id=quotation_id,
                quotation_version=version,
                quotation_name=name,
                price_list_id=list_id or "",
                price_list_name=list_name,
                line=_line(line),
            )
            for line, quotation_id, version, name, list_id, list_name in rows
        ]

    async def staged_elsewhere(
        self, price_list_id: str, skus: Sequence[str], quotation_id: UUID
    ) -> dict[str, UUID]:
        """SKUs of ``skus`` already validated and not loaded in another update of the list.

        Returns:
            dict[str, UUID]: The other update of each such SKU.
        """

        if not skus:
            return {}
        async with self.database.session_factory() as session:
            rows = (
                await session.execute(
                    select(L.sku, L.quotation_id)
                    .join(Q, Q.id == L.quotation_id)
                    .where(
                        Q.price_list_id == price_list_id,
                        Q.id != quotation_id,
                        Q.kind.in_(LIST_KINDS),
                        L.sku.in_(list(skus)),
                        L.review_status == VALIDATED,
                        L.load_id.is_(None),
                        L.new_price.is_not(None),
                    )
                )
            ).all()
        return {sku: owner for sku, owner in rows}

    async def create_load(self, load: PriceListLoad, line_ids: Sequence[UUID]) -> PriceListLoad:
        """Store a load and take its items out of the staging, in one transaction.

        Every quotation touched gets a new version, so an editor holding the old one reloads
        before saving over the load.

        Raises:
            ConflictError: Some item is not in the staging any more (changed or loaded).
        """

        async with self.database.session_factory() as session, session.begin():
            session.add(
                PriceListLoadModel(
                    id=load.id,
                    kind=load.kind,
                    price_list_id=load.price_list_id,
                    price_list_name=load.price_list_name,
                    activation_date=load.activation_date,
                    line_count=load.line_count,
                    rows=list(load.rows),
                    created_by=load.created_by,
                    created_at=load.created_at,
                )
            )
            await session.flush()
            result = await session.execute(
                update(L)
                .where(
                    L.id.in_(list(line_ids)),
                    L.review_status == VALIDATED,
                    L.load_id.is_(None),
                    L.new_price.is_not(None),
                )
                .values(load_id=load.id)
                .returning(L.quotation_id)
            )
            updated = result.all()
            owners = {row[0] for row in updated}
            if not updated or len(updated) != len(set(line_ids)):
                raise ConflictError(
                    "El staging cambió desde que lo abriste: recárgalo y descarga los archivos "
                    "de nuevo"
                )
            await session.execute(
                update(Q).where(Q.id.in_(list(owners))).values(version=Q.version + 1)
            )
        return load

    async def loads(self, kind: str, limit: int = 30) -> list[PriceListLoad]:
        """The latest loads of a kind, newest first."""

        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(PriceListLoadModel)
                    .where(PriceListLoadModel.kind == kind)
                    .order_by(PriceListLoadModel.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [_load(row) for row in rows]

    async def get_load(self, load_id: UUID) -> PriceListLoad | None:
        async with self.database.session_factory() as session:
            row = await session.get(PriceListLoadModel, load_id)
        return _load(row) if row is not None else None

    async def fill_last_sales(
        self, quotation_id: UUID, by_sku: Mapping[str, Mapping[str, Any]], warning: str
    ) -> bool:
        """Write the last-sale columns of a list update's lines (by SKU) and drop ``warning``
        from its header, only while the header still has it. The version and ``updated_at``
        stay: it is not an edit, and the editor saving prices meanwhile gets no 409.

        Args:
            quotation_id (UUID): The list update.
            by_sku (Mapping[str, Mapping[str, Any]]): Columns by SKU
                (:func:`rules.list_sale_fields`); every line of the SKU takes them.
            warning (str): The warning the read clears.

        Returns:
            bool: Whether it wrote (``False``: gone, or the warning was already cleared).
        """

        async with self.database.session_factory() as session, session.begin():
            model = await session.get(Q, quotation_id, with_for_update=True)
            if model is None or warning not in (model.warnings or []):
                return False
            if by_sku:
                table = L.__table__
                columns = next(iter(by_sku.values())).keys()
                await session.execute(
                    update(table)
                    .where(
                        table.c.quotation_id == bindparam("owner_id"),
                        table.c.sku == bindparam("line_sku"),
                    )
                    .values({name: bindparam(f"v_{name}") for name in columns}),
                    [
                        {
                            "owner_id": quotation_id,
                            "line_sku": sku,
                            **{f"v_{name}": value for name, value in values.items()},
                        }
                        for sku, values in by_sku.items()
                    ],
                )
            model.warnings = [item for item in model.warnings if item != warning]
        return True

    async def has_warning(self, quotation_id: UUID, warning: str) -> bool:
        """Whether the quotation's header still carries ``warning``."""

        async with self.database.session_factory() as session:
            warnings = await session.scalar(select(Q.warnings).where(Q.id == quotation_id))
        return warning in (warnings or [])

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
