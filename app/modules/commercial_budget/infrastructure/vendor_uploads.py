"""The history of the rep master uploads in PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.infrastructure.database import Database
from app.modules.commercial_budget.application.vendor_master import VendorUpload
from app.modules.commercial_budget.infrastructure.models import VendorMasterUploadModel

U = VendorMasterUploadModel


def _upload(model: VendorMasterUploadModel) -> VendorUpload:
    return VendorUpload(
        id=model.id,
        filename=model.filename,
        size=model.size,
        status=model.status,
        http_status=model.http_status,
        rejected_rows=model.rejected_rows,
        warnings=model.warnings,
        summary=model.summary,
        actor=model.actor,
        at=model.at,
    )


class SqlAlchemyVendorUploadRepository:
    """Every upload of the rep master and what dwh-platform answered."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def add(
        self,
        *,
        filename: str,
        size: int,
        status: str,
        http_status: int | None,
        rejected_rows: int,
        warnings: int,
        summary: dict[str, Any],
        actor: str,
        at: datetime,
    ) -> VendorUpload:
        model = U(
            id=uuid4(),
            filename=filename,
            size=size,
            status=status,
            http_status=http_status,
            rejected_rows=rejected_rows,
            warnings=warnings,
            summary=summary,
            actor=actor,
            at=at,
        )
        async with self.database.session_factory() as session, session.begin():
            session.add(model)
        return _upload(model)

    async def list(self, limit: int) -> list[VendorUpload]:
        async with self.database.session_factory() as session:
            models = (
                (await session.execute(select(U).order_by(U.at.desc()).limit(limit)))
                .scalars()
                .all()
            )
        return [_upload(model) for model in models]


__all__ = ["SqlAlchemyVendorUploadRepository"]
