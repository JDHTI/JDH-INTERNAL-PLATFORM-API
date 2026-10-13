"""PostgreSQL repository for retained orders files and their results."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields as dataclass_fields
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from app.core.errors import NotFoundError
from app.infrastructure.database import Database
from app.modules.order_validation.domain.entities import (
    AgreementCheck,
    Batch,
    BatchPage,
    LineResult,
    OrderLine,
    StoredLine,
)
from app.modules.order_validation.domain.rules import summarize
from app.modules.order_validation.infrastructure.models import (
    OrderValidationBatchModel,
    OrderValidationLineModel,
)

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
    )
    return StoredLine(id=model.id, line=line, result=result)


def _apply_counts(model: OrderValidationBatchModel, pairs: Sequence[tuple[str, str]]) -> None:
    lines, orders, ready_lines, ready_orders = summarize(pairs)
    model.line_count = lines
    model.order_count = orders
    model.ready_line_count = ready_lines
    model.ready_order_count = ready_orders


class SqlAlchemyOrderValidationRepository:
    """Store files, lines and results with one short-lived session per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def create(
        self,
        batch_id: UUID,
        file_name: str,
        actor: str,
        now: datetime,
        lines: Sequence[tuple[OrderLine, LineResult]],
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
            )
            _apply_counts(batch, [(line.document, result.status) for line, result in lines])
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
    ) -> Batch:
        """Replace the result of each line and the counts of the file."""

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
            documents = dict(
                (
                    await session.execute(select(L.id, L.document).where(L.batch_id == batch_id))
                ).all()
            )
            _apply_counts(
                batch,
                [(documents[line_id], result.status) for line_id, result in results],
            )
            batch.validated_by = actor
            batch.validated_at = now
            await session.commit()
            return _batch(batch)


__all__ = ["SqlAlchemyOrderValidationRepository"]
