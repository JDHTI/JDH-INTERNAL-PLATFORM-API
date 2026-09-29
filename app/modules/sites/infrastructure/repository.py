"""PostgreSQL repository for PQRSF sites."""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.infrastructure.database import Database
from app.modules.sites.domain.entities import Site
from app.modules.sites.infrastructure.models import SiteModel


def _site(model: SiteModel) -> Site:
    return Site(
        id=model.id,
        code=model.code,
        name=model.name,
        is_active=model.is_active,
        created_at=model.created_at,
        updated_at=model.updated_at,
        abbreviation=model.abbreviation,
    )


class SqlAlchemySiteRepository:
    """Persist sites with one transaction per operation."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def list_sites(self, include_inactive: bool = False) -> list[Site]:
        """List sites ordered case-insensitively by name."""

        query = select(SiteModel).order_by(SiteModel.name, SiteModel.id)
        if not include_inactive:
            query = query.where(SiteModel.is_active.is_(True))
        async with self.database.session_factory() as session:
            result = await session.execute(query)
            return [_site(model) for model in result.scalars()]

    async def get_by_id(self, site_id: UUID) -> Site | None:
        """Find one site by UUID."""

        async with self.database.session_factory() as session:
            model = await session.get(SiteModel, site_id)
            return _site(model) if model else None

    async def get_by_code(self, code: str) -> Site | None:
        """Find one site by stable code."""

        async with self.database.session_factory() as session:
            result = await session.execute(select(SiteModel).where(SiteModel.code == code))
            model = result.scalar_one_or_none()
            return _site(model) if model else None

    async def create_site(self, code: str, name: str, abbreviation: str, is_active: bool) -> Site:
        """Insert a site and translate a concurrent duplicate into conflict."""

        model = SiteModel(
            id=uuid.uuid4(),
            code=code,
            name=name,
            abbreviation=abbreviation,
            is_active=is_active,
        )
        async with self.database.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Ya existe una sede con ese código o esa sigla") from exc
            await session.refresh(model)
            return _site(model)

    async def save_site(self, site: Site) -> Site:
        """Persist a site's mutable fields."""

        async with self.database.session_factory() as session:
            model = await session.get(SiteModel, site.id)
            if model is None:
                raise ConflictError("La sede ya no existe")
            model.name = site.name
            model.abbreviation = site.abbreviation
            model.is_active = site.is_active
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("Otra sede ya usa esa sigla") from exc
            await session.refresh(model)
            return _site(model)

    async def delete_site(self, site_id: UUID) -> None:
        """Delete a site while preserving foreign-key protection."""

        async with self.database.session_factory() as session:
            await session.execute(delete(SiteModel).where(SiteModel.id == site_id))
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ConflictError("La sede tiene solicitudes asociadas") from exc
