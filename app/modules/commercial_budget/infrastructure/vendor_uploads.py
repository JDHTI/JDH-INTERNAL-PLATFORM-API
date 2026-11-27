"""The history of the rep master uploads in PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text

from app.infrastructure.database import Database
from app.modules.commercial_budget.application.vendor_master import VendorUpload
from app.modules.commercial_budget.domain.vendor_master import TeamOverride
from app.modules.commercial_budget.infrastructure.models import (
    VendorMasterTeamModel,
    VendorMasterUploadModel,
)

U = VendorMasterUploadModel
T = VendorMasterTeamModel
_RECENT = text(
    """
    select nullif(btrim(c.salesperson_code), '') as code, sum(s.lines) as lines
    from sales_month_customers s
    join customers c on c.cliente_key = s.customer_key
    where s.period = any(:months)
    group by 1
    """
)


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

    async def recent_sellers(self, months: Sequence[str]) -> dict[str, int]:
        """Sale lines per current rep in these months (the budget's copy of the sales)."""

        if not months:
            return {}
        async with self.database.session_factory() as session:
            rows = (await session.execute(_RECENT, {"months": list(months)})).all()
        return {row.code: int(row.lines) for row in rows if row.code}

    async def list(self, limit: int) -> list[VendorUpload]:
        async with self.database.session_factory() as session:
            models = (
                (await session.execute(select(U).order_by(U.at.desc()).limit(limit)))
                .scalars()
                .all()
            )
        return [_upload(model) for model in models]

    # --- the team master's platform rows (2026-11-27) ---

    async def team_overrides(self) -> list[TeamOverride]:
        async with self.database.session_factory() as session:
            models = (await session.execute(select(T))).scalars().all()
        return [
            TeamOverride(model.name, model.team_class, model.is_active, model.note)
            for model in models
        ]

    async def save_team(
        self,
        name: str,
        *,
        team_class: str | None,
        is_active: bool,
        note: str | None,
        actor: str,
        now: datetime,
    ) -> TeamOverride:
        """Create or replace the platform's row of a team."""

        async with self.database.session_factory() as session, session.begin():
            model = await session.get(T, name)
            if model is None:
                model = T(name=name, created_by=actor, created_at=now)
                session.add(model)
            model.team_class = team_class
            model.is_active = is_active
            model.note = note
            model.updated_by = actor
            model.updated_at = now
        return TeamOverride(name, team_class, is_active, note)


__all__ = ["SqlAlchemyVendorUploadRepository"]
