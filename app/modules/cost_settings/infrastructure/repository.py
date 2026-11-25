"""PostgreSQL repository of the cost settings: each write and its change in one transaction."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.cost_settings.domain.entities import BranchRule, Change
from app.modules.cost_settings.infrastructure.models import (
    CostParameterModel,
    CostSettingsChangeModel,
    SupplierBranchRuleModel,
)

_DUPLICATE = "Ya hay una regla para ese proveedor: edítala"


def _rule(model: SupplierBranchRuleModel) -> BranchRule:
    return BranchRule(
        id=model.id,
        supplier_nit=model.supplier_nit,
        supplier_name=model.supplier_name,
        general_branch=model.general_branch,
        center_branches=dict(model.center_branches or {}),
        is_active=model.is_active,
        note=model.note,
        created_by=model.created_by,
        created_at=model.created_at,
        updated_by=model.updated_by,
        updated_at=model.updated_at,
    )


def _change_model(change: Change) -> CostSettingsChangeModel:
    return CostSettingsChangeModel(
        id=change.id,
        entity=change.entity,
        ref=change.ref,
        before=change.before,
        after=change.after,
        note=change.note,
        actor=change.actor,
        at=change.at,
    )


class SqlAlchemyCostSettingsRepository:
    """The parameters, the supplier branch rules and their history."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def parameters(self) -> dict[str, tuple[Decimal, str | None, datetime | None]]:
        async with self.database.session_factory() as session:
            rows = (await session.scalars(select(CostParameterModel))).all()
        return {row.key: (Decimal(row.value), row.updated_by, row.updated_at) for row in rows}

    async def set_parameters(
        self, values: Mapping[str, Decimal], actor: str, at: datetime, changes: list[Change]
    ) -> None:
        async with self.database.session_factory() as session, session.begin():
            for key, value in values.items():
                statement = insert(CostParameterModel).values(
                    key=key, value=value, updated_by=actor, updated_at=at
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[CostParameterModel.key],
                        set_={"value": value, "updated_by": actor, "updated_at": at},
                    )
                )
            session.add_all([_change_model(change) for change in changes])

    async def rules(self) -> list[BranchRule]:
        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(SupplierBranchRuleModel).order_by(
                        SupplierBranchRuleModel.supplier_name, SupplierBranchRuleModel.supplier_nit
                    )
                )
            ).all()
        return [_rule(row) for row in rows]

    async def get_rule(self, rule_id: UUID) -> BranchRule | None:
        async with self.database.session_factory() as session:
            model = await session.get(SupplierBranchRuleModel, rule_id)
            return _rule(model) if model else None

    async def save_rule(self, rule: BranchRule, change: Change, create: bool = False) -> BranchRule:
        values = {
            "supplier_nit": rule.supplier_nit,
            "supplier_name": rule.supplier_name,
            "general_branch": rule.general_branch,
            "center_branches": dict(rule.center_branches),
            "is_active": rule.is_active,
            "note": rule.note,
            "updated_by": rule.updated_by,
            "updated_at": rule.updated_at,
        }
        async with self.database.session_factory() as session:
            try:
                async with session.begin():
                    if create:
                        model = SupplierBranchRuleModel(
                            id=rule.id,
                            created_by=rule.created_by,
                            created_at=rule.created_at,
                            **values,
                        )
                        session.add(model)
                    else:
                        model = await session.get(SupplierBranchRuleModel, rule.id)
                        if model is None:
                            raise NotFoundError("La regla ya no existe")
                        for name, value in values.items():
                            setattr(model, name, value)
                    session.add(_change_model(change))
            except IntegrityError as exc:
                raise ConflictError(_DUPLICATE) from exc
            await session.refresh(model)
            return _rule(model)

    async def delete_rule(self, rule_id: UUID, change: Change) -> None:
        async with self.database.session_factory() as session, session.begin():
            await session.execute(
                delete(SupplierBranchRuleModel).where(SupplierBranchRuleModel.id == rule_id)
            )
            session.add(_change_model(change))

    async def changes(self, limit: int = 50) -> list[Change]:
        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(CostSettingsChangeModel)
                    .order_by(CostSettingsChangeModel.at.desc())
                    .limit(limit)
                )
            ).all()
        return [
            Change(
                id=row.id,
                entity=row.entity,
                ref=row.ref,
                before=row.before,
                after=row.after,
                actor=row.actor,
                at=row.at,
                note=row.note,
            )
            for row in rows
        ]


__all__ = ["SqlAlchemyCostSettingsRepository"]
