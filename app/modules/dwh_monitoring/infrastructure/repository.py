"""PostgreSQL repository for the local copy of the ``monitoreo`` views."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Base, Database
from app.modules.dwh_monitoring.domain.entities import (
    CycleStatus,
    LoadCycle,
    RunFilters,
    ViewReport,
)
from app.modules.dwh_monitoring.domain.views import INCREMENTAL_TIME_COLUMN, ViewSpec
from app.modules.dwh_monitoring.infrastructure.models import (
    MODELS_BY_VIEW,
    AuditoriaExtraccionModel,
    AuditoriaTransformacionModel,
    CalidadUltimoResultadoModel,
    CargaModel,
    CorridaSecuenciaModel,
    EstadisticaSecuenciaModel,
    FrescuraDominioModel,
    FrescuraResumenModel,
    FrescuraTablaModel,
    HorarioTipicoModel,
    SecuenciaModel,
    SecuenciaTablaModel,
)

# Rows of an executemany insert; SQLAlchemy splits it under asyncpg's parameter cap.
_CHUNK = 1000
# An open run older than this is not re-read any more: it was abandoned for good.
_OPEN_MAX_AGE = timedelta(days=30)
_OPEN_STATES = ("running", "abandonada")

Row = dict[str, Any]


def _as_row(model: Base) -> Row:
    return {column.name: getattr(model, column.key) for column in model.__table__.columns}  # type: ignore[attr-defined]


def _cycle(model: CargaModel) -> LoadCycle:
    return LoadCycle(
        id=model.id,
        started_at=model.iniciado_en,
        finished_at=model.terminado_en,
        status=CycleStatus(model.estado) if model.estado else None,
        error=model.error,
        views={
            name: ViewReport(
                read=int(report.get("leidas") or 0),
                written=int(report.get("escritas") or 0),
                error=report.get("error"),
            )
            for name, report in (model.detalle or {}).items()
        },
    )


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class SqlAlchemyMonitoringRepository:
    """Write the copy cycle by cycle and read it for the screens."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    # ------------------------------------------------------------------ copy

    @staticmethod
    def _values(rows: Sequence[Mapping[str, Any]], loaded_at: datetime) -> list[Row]:
        return [{**row, "cargado_en": loaded_at} for row in rows]

    async def replace(
        self, view: ViewSpec, rows: Sequence[Mapping[str, Any]], loaded_at: datetime
    ) -> int:
        """Swap the whole copy of a snapshot view in one transaction.

        Args:
            view (ViewSpec): A snapshot view.
            rows (Sequence[Mapping[str, Any]]): Its rows, by local column name.
            loaded_at (datetime): When the cycle started.

        Returns:
            int: Rows written.
        """

        model = MODELS_BY_VIEW[view.name]
        values = self._values(rows, loaded_at)
        async with self.database.session_factory() as session:
            await session.execute(delete(model))
            for start in range(0, len(values), _CHUNK):
                await session.execute(insert(model), values[start : start + _CHUNK])
            await session.commit()
        return len(values)

    async def append(
        self, view: ViewSpec, rows: Sequence[Mapping[str, Any]], loaded_at: datetime
    ) -> int:
        """Add rows to a history view; one already stored is left as is.

        Args:
            view (ViewSpec): A history view.
            rows (Sequence[Mapping[str, Any]]): Its rows, by local column name.
            loaded_at (datetime): When the cycle started.

        Returns:
            int: Rows written.
        """

        if not rows:
            return 0
        model = MODELS_BY_VIEW[view.name]
        table = model.__table__  # type: ignore[attr-defined]
        statement = (
            insert(model)
            .values(self._values(rows, loaded_at))
            .on_conflict_do_nothing(index_elements=list(view.key))
            .returning(table.c[view.key[0]])
        )
        async with self.database.session_factory() as session:
            added = (await session.execute(statement)).scalars().all()
            await session.commit()
        return len(added)

    async def upsert(
        self, view: ViewSpec, rows: Sequence[Mapping[str, Any]], loaded_at: datetime
    ) -> int:
        """Insert or update rows of an incremental view by key.

        Args:
            view (ViewSpec): An incremental view.
            rows (Sequence[Mapping[str, Any]]): Its rows, by local column name.
            loaded_at (datetime): When the cycle started.

        Returns:
            int: Rows written.
        """

        if not rows:
            return 0
        model = MODELS_BY_VIEW[view.name]
        values = self._values(rows, loaded_at)
        async with self.database.session_factory() as session:
            for start in range(0, len(values), _CHUNK):
                statement = insert(model)
                updates = {
                    name: statement.excluded[name] for name in values[0] if name not in view.key
                }
                await session.execute(
                    statement.on_conflict_do_update(index_elements=list(view.key), set_=updates),
                    values[start : start + _CHUNK],
                )
            await session.commit()
        return len(values)

    async def latest_start(self, view: ViewSpec) -> datetime | None:
        """Newest ``inicio`` stored for an incremental view.

        Args:
            view (ViewSpec): An incremental view.

        Returns:
            datetime | None: ``None`` before the first load.
        """

        column = MODELS_BY_VIEW[view.name].__table__.c[INCREMENTAL_TIME_COLUMN]  # type: ignore[attr-defined]
        async with self.database.session_factory() as session:
            return (await session.execute(select(func.max(column)))).scalar_one_or_none()

    async def open_keys(self, view: ViewSpec, loaded_before: datetime) -> list[Any]:
        """Keys of rows still open that this cycle has not refreshed yet.

        A row is open while it is ``running`` (or ``abandonada``, which can still be
        closed) or has no end. Rows older than 30 days are left alone.

        Args:
            view (ViewSpec): An incremental view.
            loaded_before (datetime): Start of the current cycle.

        Returns:
            list[Any]: Keys to re-read.
        """

        table = MODELS_BY_VIEW[view.name].__table__  # type: ignore[attr-defined]
        (key,) = view.key
        query = select(table.c[key]).where(
            or_(table.c.estado.in_(_OPEN_STATES), table.c.fin.is_(None)),
            table.c.cargado_en < loaded_before,
            table.c[INCREMENTAL_TIME_COLUMN] > loaded_before - _OPEN_MAX_AGE,
        )
        async with self.database.session_factory() as session:
            return list((await session.execute(query)).scalars().all())

    async def save_cycle(self, cycle: LoadCycle) -> None:
        """Insert or update a load cycle.

        Args:
            cycle (LoadCycle): The cycle.
        """

        values = {
            "id": cycle.id,
            "iniciado_en": cycle.started_at,
            "terminado_en": cycle.finished_at,
            "estado": cycle.status.value if cycle.status else None,
            "error": cycle.error,
            "detalle": {name: report.as_dict() for name, report in cycle.views.items()},
        }
        statement = insert(CargaModel).values(**values)
        async with self.database.session_factory() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={name: statement.excluded[name] for name in values if name != "id"},
                )
            )
            await session.commit()

    async def last_cycle(self) -> LoadCycle | None:
        """The most recent load cycle.

        Returns:
            LoadCycle | None: ``None`` before the first cycle.
        """

        query = select(CargaModel).order_by(CargaModel.iniciado_en.desc()).limit(1)
        async with self.database.session_factory() as session:
            model = (await session.execute(query)).scalar_one_or_none()
        return _cycle(model) if model else None

    async def last_successful_load(self) -> datetime | None:
        """Start of the newest cycle that loaded every view.

        Returns:
            datetime | None: ``None`` when no cycle has fully succeeded.
        """

        query = select(func.max(CargaModel.iniciado_en)).where(
            CargaModel.estado == CycleStatus.SUCCESS.value
        )
        async with self.database.session_factory() as session:
            return (await session.execute(query)).scalar_one_or_none()

    # ----------------------------------------------------------------- reads

    async def _all(self, query: Select[Any]) -> list[Row]:
        async with self.database.session_factory() as session:
            return [_as_row(model) for model in (await session.execute(query)).scalars().all()]

    async def _page(self, query: Select[Any], page: int, size: int) -> tuple[list[Row], int]:
        count = select(func.count()).select_from(query.order_by(None).subquery())
        async with self.database.session_factory() as session:
            total = (await session.execute(count)).scalar_one()
            models = (
                (await session.execute(query.offset((page - 1) * size).limit(size))).scalars().all()
            )
        return [_as_row(model) for model in models], int(total)

    async def sequences(self) -> list[Row]:
        """Every sequence card, by first start time of the day."""

        return await self._all(
            select(SecuenciaModel).order_by(
                SecuenciaModel.hora_inicio.asc().nulls_last(), SecuenciaModel.secuencia
            )
        )

    async def sequence(self, name: str) -> Row | None:
        """One sequence card, or ``None``."""

        rows = await self._all(select(SecuenciaModel).where(SecuenciaModel.secuencia == name))
        return rows[0] if rows else None

    async def sequence_tables(
        self, secuencia: str | None, dominio: str | None, fase: int | None
    ) -> list[Row]:
        """Tables each sequence extracts, in run order."""

        query = select(SecuenciaTablaModel)
        if secuencia:
            query = query.where(SecuenciaTablaModel.secuencia == secuencia)
        if dominio:
            query = query.where(SecuenciaTablaModel.dominio == dominio)
        if fase is not None:
            query = query.where(SecuenciaTablaModel.fase == fase)
        return await self._all(
            query.order_by(
                SecuenciaTablaModel.secuencia,
                SecuenciaTablaModel.fase,
                SecuenciaTablaModel.carril,
                SecuenciaTablaModel.orden,
                SecuenciaTablaModel.pipeline_name,
            )
        )

    async def typical_schedule(self, secuencia: str | None = None) -> list[Row]:
        """Typical day timeline, by scheduled hour."""

        query = select(HorarioTipicoModel)
        if secuencia:
            query = query.where(HorarioTipicoModel.secuencia == secuencia)
        return await self._all(
            query.order_by(HorarioTipicoModel.hora_programada, HorarioTipicoModel.secuencia)
        )

    async def statistics(self, secuencia: str | None = None) -> list[Row]:
        """Medians and p90 behind the ETAs."""

        query = select(EstadisticaSecuenciaModel)
        if secuencia:
            query = query.where(EstadisticaSecuenciaModel.secuencia == secuencia)
        return await self._all(query.order_by(EstadisticaSecuenciaModel.secuencia))

    async def freshness_latest(self) -> Row | None:
        """The newest freshness KPI row."""

        rows = await self._all(
            select(FrescuraResumenModel).order_by(FrescuraResumenModel.calculado_en.desc()).limit(1)
        )
        return rows[0] if rows else None

    async def freshness_history(self, start: datetime, end: datetime) -> list[Row]:
        """Freshness KPI rows computed in ``[start, end)``, oldest first."""

        return await self._all(
            select(FrescuraResumenModel)
            .where(
                FrescuraResumenModel.calculado_en >= start,
                FrescuraResumenModel.calculado_en < end,
            )
            .order_by(FrescuraResumenModel.calculado_en)
        )

    async def freshness_domains(self) -> list[Row]:
        """Freshness by domain, least fresh first."""

        return await self._all(
            select(FrescuraDominioModel).order_by(
                FrescuraDominioModel.pct_fresca.asc().nulls_first(), FrescuraDominioModel.dominio
            )
        )

    async def freshness_tables(
        self,
        estados: Sequence[str],
        dominio: str | None,
        programacion: str | None,
        query_text: str | None,
    ) -> list[Row]:
        """Freshness per table, scheduled first and oldest first."""

        query = select(FrescuraTablaModel)
        if estados:
            query = query.where(FrescuraTablaModel.estado_frescura.in_(list(estados)))
        if dominio:
            query = query.where(FrescuraTablaModel.dominio == dominio)
        if programacion:
            query = query.where(FrescuraTablaModel.programacion == programacion)
        if query_text:
            pattern = _like(query_text)
            query = query.where(
                or_(
                    FrescuraTablaModel.tabla.ilike(pattern),
                    FrescuraTablaModel.pipeline_name.ilike(pattern),
                )
            )
        return await self._all(
            query.order_by(
                FrescuraTablaModel.programacion,
                FrescuraTablaModel.edad_horas.desc().nulls_first(),
                FrescuraTablaModel.pipeline_name,
            )
        )

    async def runs(self, filters: RunFilters, page: int, size: int) -> tuple[list[Row], int]:
        """Page sequence runs, newest first."""

        model = CorridaSecuenciaModel
        query = select(model)
        if filters.start:
            query = query.where(model.inicio >= filters.start)
        if filters.end:
            query = query.where(model.inicio < filters.end)
        if filters.secuencia:
            query = query.where(model.secuencia == filters.secuencia)
        if filters.estados:
            query = query.where(model.estado.in_(list(filters.estados)))
        return await self._page(query.order_by(model.inicio.desc(), model.corrida_id), page, size)

    async def runs_between(self, start: datetime, end: datetime) -> list[Row]:
        """Sequence runs that started in ``[start, end)``, oldest first."""

        model = CorridaSecuenciaModel
        return await self._all(
            select(model)
            .where(model.inicio >= start, model.inicio < end)
            .order_by(model.inicio, model.secuencia)
        )

    async def extractions(self, filters: RunFilters, page: int, size: int) -> tuple[list[Row], int]:
        """Page the extraction audit, newest first."""

        model = AuditoriaExtraccionModel
        query = select(model)
        if filters.start:
            query = query.where(model.inicio >= filters.start)
        if filters.end:
            query = query.where(model.inicio < filters.end)
        if filters.secuencia:
            query = query.where(model.secuencia == filters.secuencia)
        if filters.dominio:
            query = query.where(model.dominio == filters.dominio)
        if filters.estados:
            query = query.where(model.estado.in_(list(filters.estados)))
        if filters.disparo:
            query = query.where(model.disparo == filters.disparo)
        if filters.query:
            pattern = _like(filters.query)
            query = query.where(or_(model.tabla.ilike(pattern), model.pipeline_name.ilike(pattern)))
        return await self._page(query.order_by(model.inicio.desc(), model.run_id), page, size)

    async def transformations(
        self, filters: RunFilters, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Page the transformation audit, newest first."""

        model = AuditoriaTransformacionModel
        query = select(model)
        if filters.start:
            query = query.where(model.inicio >= filters.start)
        if filters.end:
            query = query.where(model.inicio < filters.end)
        if filters.secuencia:
            query = query.where(model.secuencia == filters.secuencia)
        if filters.estados:
            query = query.where(model.estado.in_(list(filters.estados)))
        if filters.query:
            query = query.where(model.selector.ilike(_like(filters.query)))
        return await self._page(query.order_by(model.inicio.desc(), model.run_id), page, size)

    async def quality(self, estados: Sequence[str], tipo: str | None) -> list[Row]:
        """Latest result of each test and model, failing ones first."""

        model = CalidadUltimoResultadoModel
        query = select(model)
        if estados:
            query = query.where(model.estado.in_(list(estados)))
        if tipo:
            query = query.where(model.tipo == tipo)
        failing_first = (model.estado != "fail").asc()
        return await self._all(query.order_by(failing_first, model.tipo, model.nombre))
