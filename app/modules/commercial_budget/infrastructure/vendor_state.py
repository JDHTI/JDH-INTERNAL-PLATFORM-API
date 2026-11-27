"""The rep master as the warehouse publishes it today, to compare an upload against."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.errors import DependencyUnavailableError
from app.infrastructure.data_provider import DataProviderConnection, DataProviderUnavailable
from app.modules.commercial_budget.domain.vendor_master import (
    CurrentCoordinator,
    CurrentRep,
    normalize,
    normalize_unit,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_REPS = """
select btrim(v.codigo::text) as codigo, v.nombre, v.estado, v.equipo, v.clase_equipo,
       v.regional, v.unidad_negocio, v.cod_tercero, v.nit
from {schema}.vendedores v
where coalesce(btrim(v.codigo::text), '') <> ''
"""
_COORDINATORS = """
select btrim(s.nit) as nit, max(s.nombre) as nombre, max(s.equipo) as equipo
from {schema}.supervisores s
where coalesce(btrim(s.nit), '') <> ''
  and lower(btrim(s.rol)) in ('coordinador', 'coordinador y director')
group by 1
"""
_CENTERS = """
select co.descripcion, co.desc_regional
from {schema}.centros_operacion co
where co.desc_regional is not null
"""


_TEAMS = """
select e.nombre, max(e.clase) as clase,
       count(v.codigo) filter (where v.estado = 'A') as reps
from {schema}.equipos e
left join {schema}.vendedores v on v.equipo = e.nombre
where coalesce(btrim(e.nombre), '') <> ''
group by 1
"""
# The master's reps (those the Excel classified: business unit or team set) with what the
# template carries; personal columns only go to the file BI downloads, never stored.
_TEMPLATE_REPS = """
select v.cod_tercero, v.nombre, v.equipo, v.clase_equipo, v.regional, v.unidad_negocio,
       v.correo, v.celular, v.cod_centro_costo
from {schema}.vendedores v
where v.estado = 'A' and (v.unidad_negocio is not null or v.equipo is not null)
order by v.equipo nulls last, v.nombre
"""
_TEMPLATE_SUPERVISORS = """
select btrim(s.nit) as nit, max(s.nombre) as nombre, max(s.equipo) as equipo,
       max(s.regional) as regional, max(s.correo) as correo, max(s.celular) as celular,
       bool_or(lower(btrim(s.rol)) in ('coordinador', 'coordinador y director')) as coordinador,
       bool_or(lower(btrim(s.rol)) in ('director', 'coordinador y director')) as director
from {schema}.supervisores s
where coalesce(btrim(s.nit), '') <> ''
group by 1
order by 3 nulls last, 2
"""


@dataclass(frozen=True)
class WarehouseMaster:
    reps: list[CurrentRep]
    coordinators: list[CurrentCoordinator]
    # Operation center description → its regional (both normalized).
    centers: dict[str, str]
    regionals: set[str]
    # Team name → (class, active reps), as ``consumo.equipos`` publishes them.
    teams: dict[str, tuple[str | None, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class TemplateRep:
    document: str | None
    name: str | None
    team: str | None
    regional: str | None
    business_unit: str | None
    email: str | None
    phone: str | None
    cost_center: str | None


@dataclass(frozen=True)
class TemplateSupervisor:
    document: str
    name: str | None
    team: str | None
    regional: str | None
    email: str | None
    phone: str | None
    is_coordinator: bool
    is_director: bool


@dataclass(frozen=True)
class TemplateData:
    reps: list[TemplateRep]
    supervisors: list[TemplateSupervisor]


class WarehouseVendorState:
    """``consumo.vendedores``, ``supervisores`` and ``centros_operacion``."""

    def __init__(
        self, connection: DataProviderConnection, schema: str = "consumo", timeout: float = 30.0
    ) -> None:
        if not _IDENTIFIER.fullmatch(schema):
            raise ValueError(f"Invalid schema: {schema}")
        self.connection = connection
        self.schema = schema
        self.timeout = timeout

    async def _fetch(self, query: str):
        try:
            return await self.connection.fetch(
                query.format(schema=self.schema), timeout=self.timeout
            )
        except DataProviderUnavailable as exc:
            raise DependencyUnavailableError(
                "No se pudo leer el maestro de vendedores del DWH; intenta de nuevo en un momento"
            ) from exc

    async def current(self) -> WarehouseMaster:
        reps = [
            CurrentRep(
                code=row["codigo"],
                name=normalize(row["nombre"]),
                documents=tuple(
                    dict.fromkeys(
                        doc for doc in (normalize(row["cod_tercero"]), normalize(row["nit"])) if doc
                    )
                ),
                team=normalize(row["equipo"]),
                team_class=normalize(row["clase_equipo"]),
                regional=normalize(row["regional"]),
                business_unit=normalize_unit(row["unidad_negocio"]),
                is_active=normalize(row["estado"]) == "A",
            )
            for row in await self._fetch(_REPS)
        ]
        coordinators = [
            CurrentCoordinator(row["nit"], normalize(row["nombre"]), normalize(row["equipo"]))
            for row in await self._fetch(_COORDINATORS)
        ]
        centers: dict[str, str] = {}
        ambiguous: set[str] = set()
        regionals: set[str] = set()
        for row in await self._fetch(_CENTERS):
            name, regional = normalize(row["descripcion"]), normalize(row["desc_regional"])
            if not name or not regional:
                continue
            regionals.add(regional)
            if centers.setdefault(name, regional) != regional:
                ambiguous.add(name)
        return WarehouseMaster(
            reps,
            coordinators,
            {key: value for key, value in centers.items() if key not in ambiguous},
            regionals,
            await self.teams(),
        )

    async def teams(self) -> dict[str, tuple[str | None, int]]:
        """The team master: name → (class, active reps)."""

        found: dict[str, tuple[str | None, int]] = {}
        for row in await self._fetch(_TEAMS):
            name = normalize(row["nombre"])
            if name:
                found[name] = (normalize(row["clase"]), int(row["reps"] or 0))
        return found

    async def template(self) -> TemplateData:
        """The master as the template carries it: reps, coordinators and directors."""

        reps = [
            TemplateRep(
                document=_text(row["cod_tercero"]),
                name=_text(row["nombre"]),
                team=normalize(row["equipo"]),
                regional=normalize(row["regional"]),
                business_unit=normalize_unit(row["unidad_negocio"]),
                email=_text(row["correo"]),
                phone=_text(row["celular"]),
                cost_center=_text(row["cod_centro_costo"]),
            )
            for row in await self._fetch(_TEMPLATE_REPS)
        ]
        supervisors = [
            TemplateSupervisor(
                document=row["nit"],
                name=_text(row["nombre"]),
                team=normalize(row["equipo"]),
                regional=normalize(row["regional"]),
                email=_text(row["correo"]),
                phone=_text(row["celular"]),
                is_coordinator=bool(row["coordinador"]),
                is_director=bool(row["director"]),
            )
            for row in await self._fetch(_TEMPLATE_SUPERVISORS)
        ]
        return TemplateData(reps, supervisors)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "TemplateData",
    "TemplateRep",
    "TemplateSupervisor",
    "WarehouseMaster",
    "WarehouseVendorState",
]
