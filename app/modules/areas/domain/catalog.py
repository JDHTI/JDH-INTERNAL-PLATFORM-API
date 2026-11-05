"""Modules of the workspace that an area role can grant, and the two actions.

``read`` lets a person look and download; ``manage`` lets them create, edit, approve
and configure, and implies ``read``. The wildcard ``*`` covers every module of the
area (what the area's leader role holds). Record rules (a case assigned to you, a
quotation you made) stay inside each module.

Mirrors the module catalog of the UI (``areas.config.ts``), limited to the modules
with an API. A module may live in several areas with a different meaning of
``manage`` (``cotizador``: comercial quotes, BI validates). Platform administration
(users, areas, the permissions panel) is not a module: an area role must never grant
it, so it stays on the global role.

Where a module is switched on is data (``area_modules``): the catalog's
``area_slugs`` are only the default for an area without a row for that module, so an
administrator turns any module on or off in any area without a deploy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidInputError

READ = "read"
MANAGE = "manage"
ACTIONS = (READ, MANAGE)
WILDCARD = "*"
# Every area has a document manager; it is granted per area like any other module.
EVERY_AREA = "*"


@dataclass(frozen=True)
class Module:
    """A grantable part of the workspace."""

    key: str
    label: str
    # Areas where the module is on by default; ``("*",)`` means every area.
    area_slugs: tuple[str, ...]
    description: str = ""
    # Whether switching it on in an area takes settings (which masters, for ``maestros``).
    configurable: bool = False

    def in_area(self, area_slug: str) -> bool:
        """Whether the module is on by default in the area."""

        return EVERY_AREA in self.area_slugs or area_slug in self.area_slugs


@dataclass(frozen=True)
class AreaModule:
    """An administrator's choice for one module in one area (a row of ``area_modules``)."""

    is_enabled: bool
    config: Mapping[str, Any] = field(default_factory=dict)


# Area slug -> module key -> the stored choice. Missing means the catalog default.
ModuleOverrides = Mapping[str, Mapping[str, AreaModule]]
MASTERS_MODULE = "maestros"

MODULES: tuple[Module, ...] = (
    Module("documentos", "Gestor documental", (EVERY_AREA,), "Carpetas y documentos del área"),
    Module("proyectos", "Proyectos", ("ti",), "Proyectos, épicas y tareas"),
    Module("monitoreo", "Tablero general y aplicativos", ("ti",), "Estado de los aplicativos"),
    Module("datawarehouse", "Datawarehouse", ("ti",), "Cargas y frescura del DWH"),
    Module("pqrsf", "PQRSF", ("calidad",), "Peticiones, quejas, reclamos y sugerencias"),
    Module("cotizador", "Cotizador", ("comercial", "bi"), "Cotizaciones a clientes"),
    Module(
        "validador-pedidos",
        "Validador de pedidos",
        ("bi", "comercial"),
        "Pedidos retenidos contra el maestro de apoyos",
    ),
    Module("apoyos", "Maestro de apoyos", ("bi", "comercial"), "Apoyos de proveedores"),
    Module(
        "solicitudes-apoyos",
        "Solicitudes de apoyo JDH",
        ("comercial", "bi"),
        "Solicitudes de apoyo a BI",
    ),
    Module("casos-especiales", "Casos especiales", ("bi",), "Condiciones especiales"),
    Module(
        MASTERS_MODULE,
        "Maestros",
        ("bi",),
        "Clientes, productos, proveedores y demás maestros del DWH",
        configurable=True,
    ),
)
_BY_KEY = {module.key: module for module in MODULES}


def get_module(key: str) -> Module | None:
    """The module with this key."""

    return _BY_KEY.get(key)


def is_enabled(area_slug: str, module_key: str, overrides: ModuleOverrides | None = None) -> bool:
    """Whether the module is switched on in the area.

    Args:
        area_slug (str): Area slug.
        module_key (str): Module key.
        overrides (ModuleOverrides | None): Stored choices; the default otherwise.

    Returns:
        bool: ``True`` when it is on.
    """

    module = _BY_KEY.get(module_key)
    if module is None:
        return False
    choice = (overrides or {}).get(area_slug, {}).get(module_key)
    return choice.is_enabled if choice is not None else module.in_area(area_slug)


def module_config(
    area_slug: str, module_key: str, overrides: ModuleOverrides | None = None
) -> Mapping[str, Any]:
    """The settings of a module in an area (empty when none were stored)."""

    choice = (overrides or {}).get(area_slug, {}).get(module_key)
    return choice.config if choice is not None else {}


def modules_for(area_slug: str, overrides: ModuleOverrides | None = None) -> list[Module]:
    """Modules switched on in an area.

    Args:
        area_slug (str): Area slug.
        overrides (ModuleOverrides | None): Stored choices; the catalog defaults otherwise.

    Returns:
        list[Module]: The modules, in catalog order.
    """

    return [module for module in MODULES if is_enabled(area_slug, module.key, overrides)]


def clean_module_config(module_key: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Check the settings of a module in an area.

    ``maestros`` takes ``{"masters": [slug, ...]}`` (``null`` or absent: every master);
    the other modules take none.

    Raises:
        InvalidInputError: When the module is unknown or the settings do not fit it.
    """

    module = _BY_KEY.get(module_key)
    if module is None:
        raise InvalidInputError(f"El módulo {module_key} no existe")
    config = dict(config or {})
    if not module.configurable:
        if config:
            raise InvalidInputError(f"El módulo {module_key} no tiene configuración")
        return {}
    unknown = sorted(set(config) - {"masters"})
    if unknown:
        raise InvalidInputError(f"Configuración desconocida: {', '.join(unknown)}")
    masters = config.get("masters")
    if masters is None:
        return {}
    if not isinstance(masters, list) or not all(isinstance(slug, str) for slug in masters):
        raise InvalidInputError("masters debe ser una lista de identificadores de maestro")
    cleaned = sorted({slug.strip().lower() for slug in masters if slug.strip()})
    if any(len(slug) > 80 for slug in cleaned):
        raise InvalidInputError("Identificador de maestro demasiado largo")
    return {"masters": cleaned}


def implies(granted: str | None, wanted: str) -> bool:
    """Whether a granted action covers the wanted one (``manage`` covers ``read``).

    Args:
        granted (str | None): Action held, or ``None``.
        wanted (str): Action needed.

    Returns:
        bool: ``True`` when it is covered.
    """

    if granted is None:
        return False
    return granted == MANAGE or granted == wanted


def validate_grant(
    area_slug: str, module_key: str, action: str, overrides: ModuleOverrides | None = None
) -> tuple[str, str]:
    """Check a grant against the modules switched on in the area.

    Args:
        area_slug (str): Area the role belongs to.
        module_key (str): Module key, or ``*``.
        action (str): ``read`` or ``manage``.
        overrides (ModuleOverrides | None): Stored module choices.

    Returns:
        tuple[str, str]: The normalized module and action.

    Raises:
        InvalidInputError: When the action or the module is not valid for the area.
    """

    module_key = module_key.strip().lower()
    action = action.strip().lower()
    if action not in ACTIONS:
        raise InvalidInputError(f"La acción debe ser {READ} o {MANAGE}")
    if module_key == WILDCARD:
        return module_key, action
    if not is_enabled(area_slug, module_key, overrides):
        raise InvalidInputError(f"El módulo {module_key} no está activo en el área {area_slug}")
    return module_key, action
