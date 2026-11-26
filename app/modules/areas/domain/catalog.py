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
    # Only its default areas may have it: an administrator cannot switch it on elsewhere.
    locked: bool = False

    def in_area(self, area_slug: str) -> bool:
        """Whether the module is on by default in the area."""

        return EVERY_AREA in self.area_slugs or area_slug in self.area_slugs

    def allowed_in(self, area_slug: str) -> bool:
        """Whether the module may be switched on in the area (a locked one, only its own)."""

        return not self.locked or self.in_area(area_slug)


@dataclass(frozen=True)
class AreaModule:
    """An administrator's choice for one module in one area (a row of ``area_modules``)."""

    is_enabled: bool
    config: Mapping[str, Any] = field(default_factory=dict)


# Area slug -> module key -> the stored choice. Missing means the catalog default.
ModuleOverrides = Mapping[str, Mapping[str, AreaModule]]
MASTERS_MODULE = "maestros"


@dataclass(frozen=True)
class GeneralMaster:
    """A master the ``maestros`` module lists in an area's menu, each with its own page."""

    slug: str
    label: str
    description: str


# The standard of general masters (2026-11-24): the four BI sees in its dropdown, in menu
# order. An area's ``maestros`` settings choose among these, and a new master is added here.
# The DWH views of ``/masters`` (centros de costo, equipos, ...) are no longer offered to areas.
GENERAL_MASTERS: tuple[GeneralMaster, ...] = (
    GeneralMaster(
        "proveedores-comerciales",
        "Proveedores comerciales",
        "Proveedores con los que se negocia, con sus productos y los NIT que los respaldan",
    ),
    GeneralMaster(
        "proveedores-legales",
        "Proveedores legales",
        "Terceros proveedores a quienes se les factura y paga, una fila por sucursal",
    ),
    GeneralMaster(
        "clientes",
        "Clientes",
        "Clientes con su canal, unidad de negocio y vendedor, una fila por sucursal",
    ),
    GeneralMaster(
        "productos",
        "Productos",
        "Catálogo de productos con su proveedor comercial, marca, clasificación y márgenes",
    ),
)
GENERAL_MASTER_SLUGS = tuple(master.slug for master in GENERAL_MASTERS)

MODULES: tuple[Module, ...] = (
    Module("documentos", "Gestor documental", (EVERY_AREA,), "Carpetas y documentos del área"),
    Module("proyectos", "Proyectos", ("ti",), "Proyectos, épicas y tareas"),
    Module("monitoreo", "Tablero general y aplicativos", ("ti",), "Estado de los aplicativos"),
    Module("datawarehouse", "Datawarehouse", ("ti",), "Cargas y frescura del DWH"),
    Module("pqrsf", "PQRSF", ("calidad",), "Peticiones, quejas, reclamos y sugerencias"),
    Module(
        "planes-accion",
        "Planes de acción",
        ("calidad",),
        "Acciones correctivas, preventivas y de mejora del SIG (P.04.F.02)",
    ),
    Module("cotizador", "Cotizador", ("comercial", "bi", "call-center"), "Cotizaciones a clientes"),
    # A permission without a screen of its own (2026-11-23): ``manage`` makes a director of
    # Comercial, who sees every customer, support and customer quotation, and edits only their
    # own quotations. Coordinators need no key: their SIESA supervisor link gives the team.
    Module(
        "comercial-director",
        "Director comercial",
        ("comercial",),
        "Ve todas las cotizaciones, clientes y apoyos de Comercial",
        locked=True,
    ),
    Module(
        "actualizacion-listas",
        "Actualización de listas",
        ("bi",),
        "Actualizaciones de listas de precios vigentes de SIESA",
    ),
    Module(
        "listas-principales",
        "Listas principales",
        ("bi",),
        "Listas generales, mayoristas y Market de SIESA, e impuesto licor; solo BI",
        locked=True,
    ),
    # A permission without a screen of its own: ``manage`` validates the items of the main
    # lists and the liquor tax changes, and loads them in SIESA (BI's analysts and leader).
    Module(
        "validacion-listas",
        "Validación de listas principales",
        ("bi",),
        "Valida los ítems de las listas principales y los carga en SIESA",
        locked=True,
    ),
    # A permission without a screen of its own: ``manage`` changes the liquor tax of every
    # list and generates its SIESA files at once, with no validation (a few collaborators).
    Module(
        "impuesto-licor",
        "Impuesto licor",
        ("bi",),
        "Cambia el impuesto licor de todas las listas y genera su plano para SIESA",
        locked=True,
    ),
    Module(
        "validador-pedidos",
        "Validador de pedidos",
        ("bi", "comercial"),
        "Pedidos retenidos contra el maestro de apoyos",
    ),
    Module("apoyos", "Maestro de apoyos", ("bi", "comercial"), "Apoyos de proveedores"),
    # Renamed «Excepciones de margen» on 2026-11-26; the key stays so grants keep working.
    Module(
        "solicitudes-apoyos",
        "Excepciones de margen",
        ("comercial", "bi"),
        "Excepciones de margen mínimo que Comercial pide a BI",
    ),
    # Gestión comercial (2026-11-26): what the ERP does not hold about the sales force.
    # ``manage`` fills the budget; closing a month is for the leaders of BI and Comercial.
    Module(
        "presupuesto-comercial",
        "Presupuesto comercial",
        ("bi", "comercial"),
        "Venta, margen y clientes por vendedor contra su presupuesto; cierre del mes",
    ),
    # ``manage`` uploads the rep master to the DWH (dwh-platform), moved from the supplier
    # platform; ``read`` downloads the template and sees the uploads.
    Module(
        "maestro-vendedores",
        "Maestro de vendedores",
        ("bi",),
        "Equipo, regional, unidad de negocio y coordinador de cada vendedor (carga al DWH)",
    ),
    # A permission without a screen of its own: ``manage`` approves or rejects the internal
    # support requests asked from a customer quotation (BI's supports managers decide too).
    # Turn it on in another area to designate reviewers there.
    Module(
        "aprobacion-apoyos-internos",
        "Aprobación de apoyos internos",
        ("bi",),
        "Aprueba o rechaza los apoyos internos pedidos desde la cotización",
    ),
    Module(
        "mapa-comercial",
        "Mapa comercial",
        ("comercial",),
        "Prospectos y clientes por zona, y rutas de visita",
    ),
    # Costos (Abastecimiento): the former «Control de Precios JDH», one key per view so a buyer
    # can hold only the novelties and an analyst all four (2026-11-23).
    Module(
        "analisis-entradas",
        "Análisis de entradas",
        ("abastecimiento",),
        "Entradas de compra contra la cotización vigente del proveedor",
    ),
    Module(
        "novedades-compras",
        "Novedades y notas crédito",
        ("abastecimiento",),
        "Novedades de las entradas de compra, su motivo y la nota crédito del proveedor",
    ),
    Module(
        "alzas-proveedor",
        "Alzas de proveedor",
        ("abastecimiento",),
        "Plantilla, carga y revisión de las alzas que envían los proveedores",
    ),
    Module(
        "acumulado-alzas",
        "Acumulado de alzas",
        ("abastecimiento",),
        "Histórico y análisis de las alzas de costo de los productos",
    ),
    # The month's settlement of the supplier supports (2026-11-24): BI only. ``read`` sees and
    # downloads it, ``manage`` recalculates and closes; the area's leader reopens.
    Module(
        "liquidacion-apoyos",
        "Liquidación de apoyos",
        ("bi",),
        "Cuánto reconoce cada proveedor por las ventas apoyadas del mes y el margen recompuesto",
        locked=True,
    ),
    # The cost rules of the quoter in one place (2026-11-25): the cost factor of each list
    # family, the negotiation threshold and the supplier branch per operation center. BI by
    # default; ``read`` sees them, ``manage`` changes them.
    Module(
        "ajuste-costos",
        "Ajuste de costos",
        ("bi",),
        "Factor de costo por familia, umbral de negociación y sucursal del proveedor por C.O.",
    ),
    Module(
        MASTERS_MODULE,
        "Maestros",
        ("bi",),
        "Proveedores comerciales y legales, clientes y productos",
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
    if module is None or not module.allowed_in(area_slug):
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


def area_masters(area_slug: str, overrides: ModuleOverrides | None = None) -> tuple[str, ...]:
    """The general masters an area's ``maestros`` lists, in menu order; none when it is off.

    Settings without a list open every general master. A stored slug that is no longer in
    :data:`GENERAL_MASTERS` gives nothing.
    """

    if not is_enabled(area_slug, MASTERS_MODULE, overrides):
        return ()
    chosen = module_config(area_slug, MASTERS_MODULE, overrides).get("masters")
    if chosen is None:
        return GENERAL_MASTER_SLUGS
    return tuple(slug for slug in GENERAL_MASTER_SLUGS if slug in chosen)


def clean_module_config(module_key: str, config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Check the settings of a module in an area.

    ``maestros`` takes ``{"masters": [slug, ...]}`` among :data:`GENERAL_MASTERS`
    (``null`` or absent: every one); the other modules take none.

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
    cleaned = {slug.strip().lower() for slug in masters if slug.strip()}
    unknown = sorted(cleaned - set(GENERAL_MASTER_SLUGS))
    if unknown:
        valid = ", ".join(GENERAL_MASTER_SLUGS)
        raise InvalidInputError(f"Maestro desconocido: {', '.join(unknown)} (válidos: {valid})")
    return {"masters": [slug for slug in GENERAL_MASTER_SLUGS if slug in cleaned]}


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
