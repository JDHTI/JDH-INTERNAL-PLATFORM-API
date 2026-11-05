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
"""

from __future__ import annotations

from dataclasses import dataclass

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
    # Areas where the module exists; ``("*",)`` means every area.
    area_slugs: tuple[str, ...]

    def in_area(self, area_slug: str) -> bool:
        """Whether the module exists in the area."""

        return EVERY_AREA in self.area_slugs or area_slug in self.area_slugs


MODULES: tuple[Module, ...] = (
    Module("documentos", "Gestor documental", (EVERY_AREA,)),
    Module("proyectos", "Proyectos", ("ti",)),
    Module("monitoreo", "Tablero general y aplicativos", ("ti",)),
    Module("datawarehouse", "Datawarehouse", ("ti",)),
    Module("pqrsf", "PQRSF", ("calidad",)),
    Module("cotizador", "Cotizador", ("comercial", "bi")),
    Module("validador-pedidos", "Validador de pedidos", ("bi", "comercial")),
    Module("apoyos", "Maestro de apoyos", ("bi", "comercial")),
    Module("solicitudes-apoyos", "Solicitudes de apoyo JDH", ("comercial", "bi")),
    Module("casos-especiales", "Casos especiales", ("bi",)),
    Module("maestros", "Maestros comerciales", ("bi",)),
)
_BY_KEY = {module.key: module for module in MODULES}


def get_module(key: str) -> Module | None:
    """The module with this key."""

    return _BY_KEY.get(key)


def modules_for(area_slug: str) -> list[Module]:
    """Modules that exist in an area.

    Args:
        area_slug (str): Area slug.

    Returns:
        list[Module]: The modules, in catalog order.
    """

    return [module for module in MODULES if module.in_area(area_slug)]


def areas_for(module_key: str) -> tuple[str, ...] | None:
    """Areas where a module exists.

    Args:
        module_key (str): Module key.

    Returns:
        tuple[str, ...] | None: Their slugs; ``None`` for every area or an unknown module.
    """

    module = _BY_KEY.get(module_key)
    if module is None or EVERY_AREA in module.area_slugs:
        return None
    return module.area_slugs


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


def validate_grant(area_slug: str, module_key: str, action: str) -> tuple[str, str]:
    """Check a grant against the catalog.

    Args:
        area_slug (str): Area the role belongs to.
        module_key (str): Module key, or ``*``.
        action (str): ``read`` or ``manage``.

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
    module = _BY_KEY.get(module_key)
    if module is None or not module.in_area(area_slug):
        raise InvalidInputError(f"El módulo {module_key} no existe en el área {area_slug}")
    return module_key, action
