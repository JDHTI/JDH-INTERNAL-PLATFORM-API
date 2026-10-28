"""PostgreSQL repository for supplier support profiles."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.supplier_supports.domain.profiles import Profile, ProfileData
from app.modules.supplier_supports.infrastructure.models import SupplierSupportProfileModel

P = SupplierSupportProfileModel


def _profile(model: P, code: str, name: str) -> Profile:
    return Profile(
        id=model.id,
        commercial_supplier_id=model.commercial_supplier_id,
        supplier_code=code,
        supplier_name=name,
        data=ProfileData(
            defaults=dict(model.defaults or {}),
            column_map={key: list(value) for key, value in (model.column_map or {}).items()},
            sheet=model.sheet,
            notes=model.notes,
        ),
        created_by=model.created_by,
        updated_by=model.updated_by,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyProfileRepository:
    """Profiles of the commercial suppliers."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Database whose sessions are used.
        """

        self.database = database

    async def list(self) -> list[Profile]:
        """Every profile, by supplier name."""

        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(P, CommercialSupplierModel.code, CommercialSupplierModel.name)
                .join(
                    CommercialSupplierModel,
                    CommercialSupplierModel.id == P.commercial_supplier_id,
                )
                .order_by(CommercialSupplierModel.name)
            )
            return [_profile(model, code, name) for model, code, name in rows.all()]

    async def get(self, supplier_id: UUID) -> Profile | None:
        """The profile of a commercial supplier, if it has one."""

        async with self.database.session_factory() as session:
            row = (
                await session.execute(
                    select(P, CommercialSupplierModel.code, CommercialSupplierModel.name)
                    .join(
                        CommercialSupplierModel,
                        CommercialSupplierModel.id == P.commercial_supplier_id,
                    )
                    .where(P.commercial_supplier_id == supplier_id)
                )
            ).one_or_none()
            return None if row is None else _profile(*row)

    async def _supplier(self, session, supplier_id: UUID) -> tuple[str, str]:
        found = (
            await session.execute(
                select(CommercialSupplierModel.code, CommercialSupplierModel.name).where(
                    CommercialSupplierModel.id == supplier_id
                )
            )
        ).one_or_none()
        if found is None:
            raise NotFoundError("Proveedor comercial no encontrado")
        return found[0], found[1]

    async def create(
        self, profile_id: UUID, supplier_id: UUID, data: ProfileData, actor: str, now: datetime
    ) -> Profile:
        """Insert the profile of a supplier that has none."""

        model = P(
            id=profile_id,
            commercial_supplier_id=supplier_id,
            defaults=data.defaults,
            column_map=data.column_map,
            sheet=data.sheet,
            notes=data.notes,
            created_by=actor,
            updated_by=actor,
            version=1,
            created_at=now,
            updated_at=now,
        )
        async with self.database.session_factory() as session:
            code, name = await self._supplier(session, supplier_id)
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("El proveedor ya tiene un perfil; edítalo") from exc
            return _profile(model, code, name)

    async def update(
        self, supplier_id: UUID, data: ProfileData, expected_version: int, actor: str, now: datetime
    ) -> Profile:
        """Replace a profile's data when its version still matches."""

        async with self.database.session_factory() as session:
            model = (
                await session.execute(
                    select(P).where(P.commercial_supplier_id == supplier_id).with_for_update()
                )
            ).scalar_one_or_none()
            if model is None:
                raise NotFoundError("El proveedor no tiene perfil")
            if model.version != expected_version:
                raise ConflictError("El perfil cambió desde que lo abriste; recárgalo")
            code, name = await self._supplier(session, supplier_id)
            model.defaults = data.defaults
            model.column_map = data.column_map
            model.sheet = data.sheet
            model.notes = data.notes
            model.version += 1
            model.updated_by = actor
            model.updated_at = now
            await session.commit()
            return _profile(model, code, name)

    async def delete(self, supplier_id: UUID) -> bool:
        """Remove the profile of a supplier; the supports already created are not touched."""

        async with self.database.session_factory() as session:
            result = await session.execute(delete(P).where(P.commercial_supplier_id == supplier_id))
            await session.commit()
            return bool(result.rowcount)


__all__ = ["SqlAlchemyProfileRepository"]
