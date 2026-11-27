"""PostgreSQL repository for retained orders files and their results."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import fields as dataclass_fields
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.order_validation.domain.entities import (
    AgreementCheck,
    Batch,
    BatchPage,
    LineResult,
    ManualApproval,
    OrderLine,
    OrderPolicy,
    StoredLine,
)
from app.modules.order_validation.domain.rules import DEFAULT_POLICY, summarize_batch
from app.modules.order_validation.infrastructure.models import (
    OrderValidationApprovalModel,
    OrderValidationBatchModel,
    OrderValidationLineModel,
)

A = OrderValidationApprovalModel
B = OrderValidationBatchModel
L = OrderValidationLineModel
_LINE_FIELDS = tuple(
    item.name for item in dataclass_fields(OrderLine) if item.name not in {"problems"}
)


def _batch(model: OrderValidationBatchModel) -> Batch:
    return Batch(
        id=model.id,
        file_name=model.file_name,
        uploaded_by=model.uploaded_by,
        uploaded_at=model.uploaded_at,
        validated_by=model.validated_by,
        validated_at=model.validated_at,
        line_count=model.line_count,
        order_count=model.order_count,
        ready_line_count=model.ready_line_count,
        ready_order_count=model.ready_order_count,
        margin_threshold=model.margin_threshold,
        released_order_count=model.released_order_count,
    )


def _result_columns(result: LineResult) -> dict[str, object]:
    return {
        "status": result.status,
        "reason": result.reason,
        "detail": result.detail,
        "support_id": result.support_id,
        "effective_margin_pct": result.effective_margin_pct,
        "required_margin_pct": result.required_margin_pct,
        "checks": [item.as_dict() for item in result.checks],
        "margin_basis": result.margin_basis,
        "liquor_tax": result.liquor_tax,
        "liquor_tax_amount": result.liquor_tax_amount,
        "inventory_cost": result.inventory_cost,
        "inventory_stock": result.inventory_stock,
        "inventory_date": result.inventory_date,
    }


def _stored(model: OrderValidationLineModel) -> StoredLine:
    line = OrderLine(
        **{name: getattr(model, name) for name in _LINE_FIELDS},
        problems=tuple(model.problems or ()),
    )
    result = LineResult(
        status=model.status,
        reason=model.reason,
        detail=model.detail,
        support_id=model.support_id,
        effective_margin_pct=model.effective_margin_pct,
        required_margin_pct=model.required_margin_pct,
        checks=tuple(AgreementCheck.from_dict(item) for item in model.checks or ()),
        margin_basis=model.margin_basis,
        liquor_tax=model.liquor_tax,
        liquor_tax_amount=model.liquor_tax_amount,
        inventory_cost=model.inventory_cost,
        inventory_stock=model.inventory_stock,
        inventory_date=model.inventory_date,
    )
    return StoredLine(id=model.id, line=line, result=result)


def _apply_counts(
    model: OrderValidationBatchModel,
    items: Sequence[tuple[OrderLine, LineResult]],
    approved: Collection[str],
    policy: OrderPolicy,
) -> None:
    """Counts of the file: an order is ready by its lines, a manual approval or its margin."""

    lines, orders, ready_lines, ready_orders, released_orders = summarize_batch(
        items, approved, model.margin_threshold or Decimal(0), policy
    )
    model.line_count = lines
    model.order_count = orders
    model.ready_line_count = ready_lines
    model.ready_order_count = ready_orders
    model.released_order_count = released_orders


async def _recount(session: Any, batch: OrderValidationBatchModel, policy: OrderPolicy) -> None:
    """Counts of a stored file from its lines, its manual approvals and its threshold.

    The whole result of each line is read: the order margin counts each line with its support,
    and the liquor tax flag holds an order back.
    """

    models = (await session.execute(select(L).where(L.batch_id == batch.id))).scalars().all()
    approved = (
        (await session.execute(select(A.document).where(A.batch_id == batch.id))).scalars().all()
    )
    items = [(stored.line, stored.result) for stored in map(_stored, models)]
    _apply_counts(batch, items, set(approved), policy)


def _approval(model: OrderValidationApprovalModel) -> ManualApproval:
    return ManualApproval(
        document=model.document,
        approved_by=model.approved_by,
        approved_at=model.approved_at,
        note=model.note,
    )


class SqlAlchemyOrderValidationRepository:
    """Store files, lines and results with one short-lived session per operation."""

    def __init__(self, database: Database, policy: OrderPolicy = DEFAULT_POLICY) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
            policy (OrderPolicy): Order-level limits used to count the ready orders; the
                service must use the same one.
        """

        self.database = database
        self.policy = policy

    async def create(
        self,
        batch_id: UUID,
        file_name: str,
        actor: str,
        now: datetime,
        lines: Sequence[tuple[OrderLine, LineResult]],
        margin_threshold: Decimal,
    ) -> Batch:
        """Insert a file with all its lines and their results in one transaction."""

        async with self.database.session_factory() as session:
            batch = B(
                id=batch_id,
                file_name=file_name,
                uploaded_by=actor,
                uploaded_at=now,
                validated_by=actor,
                validated_at=now,
                margin_threshold=margin_threshold,
            )
            _apply_counts(batch, lines, (), self.policy)
            session.add(batch)
            await session.flush()
            session.add_all(
                L(
                    id=uuid4(),
                    batch_id=batch_id,
                    problems=list(line.problems),
                    **{name: getattr(line, name) for name in _LINE_FIELDS},
                    **_result_columns(result),
                )
                for line, result in lines
            )
            await session.commit()
            return _batch(batch)

    async def get(self, batch_id: UUID) -> Batch | None:
        """One file."""

        async with self.database.session_factory() as session:
            model = await session.get(B, batch_id)
            return None if model is None else _batch(model)

    async def list(self, page: int, size: int) -> BatchPage:
        """Files, newest first."""

        async with self.database.session_factory() as session:
            total = (await session.execute(select(func.count()).select_from(B))).scalar_one()
            models = (
                (
                    await session.execute(
                        select(B)
                        .order_by(B.uploaded_at.desc(), B.id)
                        .offset((page - 1) * size)
                        .limit(size)
                    )
                )
                .scalars()
                .all()
            )
        return BatchPage(
            items=[_batch(item) for item in models], total=int(total), page=page, size=size
        )

    async def lines(self, batch_id: UUID) -> list[StoredLine]:
        """Every line of a file, in file order."""

        async with self.database.session_factory() as session:
            models = (
                (
                    await session.execute(
                        select(L).where(L.batch_id == batch_id).order_by(L.row_number, L.id)
                    )
                )
                .scalars()
                .all()
            )
        return [_stored(item) for item in models]

    async def save_results(
        self,
        batch_id: UUID,
        results: Sequence[tuple[UUID, LineResult]],
        actor: str,
        now: datetime,
        margin_threshold: Decimal,
    ) -> Batch:
        """Replace the result of each line, the threshold and the counts of the file."""

        async with self.database.session_factory() as session:
            batch = (
                await session.execute(select(B).where(B.id == batch_id).with_for_update())
            ).scalar_one_or_none()
            if batch is None:
                raise NotFoundError("Validación no encontrada")
            for line_id, result in results:
                await session.execute(
                    update(L)
                    .where(L.id == line_id, L.batch_id == batch_id)
                    .values(**_result_columns(result))
                )
            await session.flush()
            batch.margin_threshold = margin_threshold
            await _recount(session, batch, self.policy)
            batch.validated_by = actor
            batch.validated_at = now
            await session.commit()
            return _batch(batch)

    async def set_threshold(self, batch_id: UUID, margin_threshold: Decimal) -> Batch | None:
        """Save the margin threshold of a file and recount its orders; `None` if it is missing."""

        async with self.database.session_factory() as session:
            batch = (
                await session.execute(select(B).where(B.id == batch_id).with_for_update())
            ).scalar_one_or_none()
            if batch is None:
                return None
            batch.margin_threshold = margin_threshold
            await _recount(session, batch, self.policy)
            await session.commit()
            return _batch(batch)

    async def approvals(self, batch_id: UUID) -> dict[str, ManualApproval]:
        """Manual approvals of a file, by document."""

        async with self.database.session_factory() as session:
            models = (
                (await session.execute(select(A).where(A.batch_id == batch_id))).scalars().all()
            )
        return {item.document: _approval(item) for item in models}

    async def approve(self, batch_id: UUID, approvals: Sequence[ManualApproval]) -> Batch:
        """Store approvals and recount the file, all or nothing.

        Raises:
            NotFoundError: If the file does not exist.
            ConflictError: If one of the orders was approved meanwhile.
        """

        async with self.database.session_factory() as session:
            batch = (
                await session.execute(select(B).where(B.id == batch_id).with_for_update())
            ).scalar_one_or_none()
            if batch is None:
                raise NotFoundError("Validación no encontrada")
            session.add_all(
                A(
                    id=uuid4(),
                    batch_id=batch_id,
                    document=item.document,
                    approved_by=item.approved_by,
                    approved_at=item.approved_at,
                    note=item.note,
                )
                for item in approvals
            )
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Uno de los pedidos ya fue aprobado; recarga la lista") from exc
            await _recount(session, batch, self.policy)
            await session.commit()
            return _batch(batch)

    async def remove_approval(self, batch_id: UUID, document: str) -> bool:
        """Drop the manual approval of an order; ``False`` when there was none."""

        async with self.database.session_factory() as session:
            batch = (
                await session.execute(select(B).where(B.id == batch_id).with_for_update())
            ).scalar_one_or_none()
            if batch is None:
                return False
            removed = await session.execute(
                delete(A).where(A.batch_id == batch_id, A.document == document)
            )
            if not removed.rowcount:
                return False
            await _recount(session, batch, self.policy)
            await session.commit()
            return True

    async def delete(self, batch_id: UUID) -> bool:
        """Delete a file with its lines and approvals; ``False`` when it did not exist."""

        async with self.database.session_factory() as session:
            removed = await session.execute(delete(B).where(B.id == batch_id))
            await session.commit()
            return bool(removed.rowcount)


__all__ = ["SqlAlchemyOrderValidationRepository"]
