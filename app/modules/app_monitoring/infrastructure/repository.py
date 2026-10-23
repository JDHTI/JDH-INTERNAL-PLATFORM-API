"""PostgreSQL repository for the monitored applications and their checks."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, and_, delete, func, select

from app.infrastructure.database import Database
from app.modules.app_monitoring.domain.entities import (
    Application,
    CheckResult,
    CheckStatus,
    Target,
)
from app.modules.app_monitoring.infrastructure.models import AplicativoModel, ChequeoModel

# Entity attribute -> column; the table keeps the screens' Spanish names.
_COLUMNS = {
    "slug": "slug",
    "name": "nombre",
    "description": "descripcion",
    "base_url": "url_base",
    "health_path": "ruta_health",
    "ui_path": "ruta_ui",
    "check_ui": "verificar_ui",
    "enabled": "habilitado",
    "verify_tls": "verificar_tls",
    "timeout_s": "timeout_s",
    "slow_ms": "umbral_lento_ms",
    "sort_order": "orden",
}


def _application(model: AplicativoModel) -> Application:
    return Application(
        id=model.id,
        **{attribute: getattr(model, column) for attribute, column in _COLUMNS.items()},
        created_at=model.creado_en,
        updated_at=model.actualizado_en,
    )


def _result(model: ChequeoModel) -> CheckResult:
    return CheckResult(
        application_id=model.aplicativo_id,
        target=Target(model.objetivo),
        checked_at=model.chequeado_en,
        status=CheckStatus(model.estado),
        http_status=model.codigo_http,
        latency_ms=model.latencia_ms,
        error=model.error,
        detail=model.detalle,
    )


class SqlAlchemyApplicationRepository:
    """Applications edited from the screens and the append-only check history."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # ---------------------------------------------------------- applications

    async def list(self, only_enabled: bool = False) -> list[Application]:
        """Every application, in display order."""

        query = select(AplicativoModel).order_by(AplicativoModel.orden, AplicativoModel.nombre)
        if only_enabled:
            query = query.where(AplicativoModel.habilitado.is_(True))
        async with self.database.session_factory() as session:
            return [_application(model) for model in (await session.scalars(query)).all()]

    async def get(self, application_id: UUID) -> Application | None:
        """One application."""

        async with self.database.session_factory() as session:
            model = await session.get(AplicativoModel, application_id)
            return _application(model) if model else None

    async def slug_taken(self, slug: str, exclude: UUID | None = None) -> bool:
        """Whether another application already uses a slug."""

        query = select(AplicativoModel.id).where(AplicativoModel.slug == slug)
        if exclude is not None:
            query = query.where(AplicativoModel.id != exclude)
        async with self.database.session_factory() as session:
            return (await session.scalar(query.limit(1))) is not None

    async def add(self, application: Application) -> Application:
        """Store a new application."""

        model = AplicativoModel(
            id=application.id,
            **{column: getattr(application, attribute) for attribute, column in _COLUMNS.items()},
        )
        async with self.database.session_factory() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return _application(model)

    async def update(self, application: Application) -> Application:
        """Store the editable fields of an application."""

        async with self.database.session_factory() as session:
            model = await session.get(AplicativoModel, application.id)
            if model is None:
                raise LookupError(application.id)
            for attribute, column in _COLUMNS.items():
                setattr(model, column, getattr(application, attribute))
            await session.commit()
            await session.refresh(model)
            return _application(model)

    async def delete(self, application_id: UUID) -> bool:
        """Remove an application; its checks go with it (``ON DELETE CASCADE``)."""

        async with self.database.session_factory() as session:
            result = await session.execute(
                delete(AplicativoModel).where(AplicativoModel.id == application_id)
            )
            await session.commit()
            return bool(result.rowcount)

    # ---------------------------------------------------------------- checks

    async def save_results(self, results: Sequence[CheckResult]) -> None:
        """Append checks to the history."""

        if not results:
            return
        async with self.database.session_factory() as session:
            session.add_all(
                ChequeoModel(
                    aplicativo_id=result.application_id,
                    objetivo=result.target.value,
                    chequeado_en=result.checked_at,
                    estado=result.status.value,
                    codigo_http=result.http_status,
                    latencia_ms=result.latency_ms,
                    error=result.error,
                    detalle=result.detail,
                )
                for result in results
            )
            await session.commit()

    async def latest(self) -> dict[tuple[UUID, Target], CheckResult]:
        """The newest check of every application and target."""

        newest = (
            select(
                ChequeoModel.aplicativo_id,
                ChequeoModel.objetivo,
                func.max(ChequeoModel.chequeado_en).label("chequeado_en"),
            )
            .group_by(ChequeoModel.aplicativo_id, ChequeoModel.objetivo)
            .subquery()
        )
        query = select(ChequeoModel).join(
            newest,
            and_(
                ChequeoModel.aplicativo_id == newest.c.aplicativo_id,
                ChequeoModel.objetivo == newest.c.objetivo,
                ChequeoModel.chequeado_en == newest.c.chequeado_en,
            ),
        )
        async with self.database.session_factory() as session:
            rows = (await session.scalars(query)).all()
        return {(row.aplicativo_id, Target(row.objetivo)): _result(row) for row in rows}

    async def summary_since(self, since: datetime) -> dict[tuple[UUID, Target], dict[str, Any]]:
        """Per application and target: checks, how many were not down, mean latency."""

        answering = func.sum((ChequeoModel.estado != CheckStatus.DOWN.value).cast(Integer))
        query = (
            select(
                ChequeoModel.aplicativo_id,
                ChequeoModel.objetivo,
                func.count().label("total"),
                answering.label("up"),
                func.avg(ChequeoModel.latencia_ms).label("latencia_media_ms"),
            )
            .where(ChequeoModel.chequeado_en >= since)
            .group_by(ChequeoModel.aplicativo_id, ChequeoModel.objetivo)
        )
        async with self.database.session_factory() as session:
            rows = (await session.execute(query)).all()
        return {
            (row.aplicativo_id, Target(row.objetivo)): {
                "total": int(row.total),
                "up": int(row.up or 0),
                "latencia_media_ms": (
                    float(row.latencia_media_ms) if row.latencia_media_ms is not None else None
                ),
            }
            for row in rows
        }

    async def history(
        self,
        application_id: UUID,
        target: Target | None,
        start: datetime,
        end: datetime,
        page: int,
        size: int,
    ) -> tuple[list[CheckResult], int]:
        """Checks of one application in ``[start, end)``, newest first."""

        conditions = [
            ChequeoModel.aplicativo_id == application_id,
            ChequeoModel.chequeado_en >= start,
            ChequeoModel.chequeado_en < end,
        ]
        if target is not None:
            conditions.append(ChequeoModel.objetivo == target.value)
        query = (
            select(ChequeoModel)
            .where(*conditions)
            .order_by(ChequeoModel.chequeado_en.desc(), ChequeoModel.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        async with self.database.session_factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(ChequeoModel).where(*conditions)
            )
            rows = (await session.scalars(query)).all()
        return [_result(row) for row in rows], int(total or 0)

    async def purge(self, before: datetime) -> int:
        """Drop checks older than a moment."""

        async with self.database.session_factory() as session:
            result = await session.execute(
                delete(ChequeoModel).where(ChequeoModel.chequeado_en < before)
            )
            await session.commit()
            return int(result.rowcount or 0)
