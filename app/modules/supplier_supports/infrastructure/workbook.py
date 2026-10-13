"""Read ``1. MAESTRO APOYOS PROVEEDORES.xlsx`` into normalized supports.

Each sheet of the workbook has its own layout, so every table is described by a
:class:`Source`: which supplier it belongs to, what kind of support a row is,
how the supplier value is applied and which header feeds which field. The
generic reader turns rows into :class:`SupportData`; hooks handle the few places
where a sheet needs more than a header mapping.

Nothing is guessed silently: every repaired or unreadable cell becomes an
:class:`Issue` on the support, and rows are never dropped for bad data.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl

from app.modules.commercial_masters.domain.entities import SupplierProductCode
from app.modules.supplier_supports.application import normalize as n
from app.modules.supplier_supports.domain.entities import Issue, SupportData

# Supplier codes (Cod. Proveedor of the "Margenes Lista1" sheet). A value starting
# with ``name:`` is resolved against the commercial supplier names at import time,
# because those suppliers have no code in the workbook.
FAMILIA, UNILEVER, TEAM, TEAM_PANADERIA = "233", "399", "8", "585"
KIMBERLY, MCCAIN, DODEKA, DIVERSEY = "1098", "1002", "1109", "323"
RUBBERMAID, DICORP, UPFIELD, ESTRA = "575", "546", "1053", "295"
GRIFFITH, LEMON_FRESH, PACANDE, GRB = "258", "905", "1234", "1241"
ADEREZOS, AZTECA, RED_BULL = "name:ASCENDER", "name:UN SOLO PROVEEDOR", "name:POSTOBON"

_FRACTION_FIELDS = {"jdh_pct", "target_margin_pct", "min_margin_pct", "list_margin_pct"}
_NUMBER_FIELDS = {
    "reference_cost",
    "reference_list_price",
    "pack_factor",
    "min_quantity",
    "committed_volume",
    "budget_amount",
    "budget_units",
    "units_used",
    "stock_quantity",
}
_TEXT_FIELDS = {
    "customer_name",
    "customer_group",
    "program",
    "external_ref",
    "conditions",
    "notes",
    "authorized_by",
    "sales_rep",
    "supplier_contact",
    "warehouse",
    "settlement_frequency",
    "brand",
}
_SKU_FIELDS = {"product_sku", "supplier_sku"}
_PCT_METHODS = {"pct_costo", "pct_precio_lista", "puntos_margen", "pct_ventas"}
MAX_EXPANSION = 200


@dataclass
class Row:
    """A table row: its sheet row number and its cells by folded header."""

    number: int
    cells: dict[str, Any]

    def get(self, *headers: str) -> Any:
        """First non-empty cell among the given headers (matched case/accent-insensitively)."""

        for header in headers:
            value = self.cells.get(n.fold(header))
            if value is not None and not (isinstance(value, str) and not value.strip()):
                return value
        return None


@dataclass
class Draft:
    """Fields and issues of a support being built."""

    kind: str
    calc: str
    values: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    # A discount over the supplier's whole portfolio ("5pp"): it has no product.
    portfolio: bool = False

    def put(self, name: str, raw: Any, *, check_digit: bool = False) -> None:
        """Convert and store one cell according to the type of the field."""

        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return
        if name in ("valid_from", "valid_to", "reference_date"):
            if name == "valid_to" and n.fold(raw).startswith("hasta"):
                self.values["end_condition"] = n.clean_text(raw)
                return
            parsed, found = n.parse_date(raw, name)
            self._store(name, parsed, found)
        elif name == "customer_nit":
            if n.is_excel_error(raw):
                self.issues.append(Issue("nit_sin_cruce", str(raw).strip(), name))
                return
            self.values[name] = n.parse_nit(raw, check_digit=check_digit)
        elif name in _SKU_FIELDS:
            if n.is_excel_error(raw):
                self.issues.append(Issue("sku_sin_cruce", str(raw).strip(), name))
                return
            self._store(name, n.clean_sku(raw), [])
        elif name in _TEXT_FIELDS or name in ("conditions", "end_condition"):
            self._store(name, n.clean_text(raw), [])
        elif name == "supplier_value":
            if (points := n.parse_pp(raw)) is not None:
                self.calc, self.portfolio = "pct_costo", True
                self.values[name] = points
            elif self.calc in _PCT_METHODS:
                self._store(name, *n.parse_fraction(raw, name))
            else:
                self._store(name, *n.parse_number(raw, name))
        elif name in _FRACTION_FIELDS:
            self._store(name, *n.parse_fraction(raw, name))
        elif name in _NUMBER_FIELDS:
            self._store(name, *n.parse_number(raw, name))
        elif name == "regionals":
            self.values[name] = n.parse_regions(raw)
        elif name == "channels":
            self.values[name] = n.parse_channels(raw)
        elif name == "business_units":
            units, channels = n.parse_units_and_channels(raw)
            self.values[name] = units
            if channels:
                self.values["channels"] = tuple(
                    dict.fromkeys((*self.values.get("channels", ()), *channels))
                )
        else:  # pragma: no cover - guarded by the sources below
            raise KeyError(name)

    def _store(self, name: str, value: Any, issues: Sequence[Issue]) -> None:
        self.issues.extend(issues)
        if value is not None:
            self.values[name] = value


Hook = Callable[[Row, Draft], None]


@dataclass(frozen=True)
class Source:
    """How one workbook table becomes supports."""

    sheet: str
    table: str
    supplier: str | None
    kind: str
    calc: str
    cols: Mapping[str, str | tuple[str, ...]]
    fixed: Mapping[str, Any] = field(default_factory=dict)
    # A row whose required fields are all empty only holds a formula and is skipped.
    required: tuple[str, ...] = ()
    check_digit: bool = False
    hook: Hook | None = None
    # Flag runs of consecutive NITs under one name (drag-fill errors).
    detect_sequential_nits: bool = True


@dataclass(frozen=True)
class ImportedSupport:
    """A support read from the workbook, with the supplier it belongs to."""

    data: SupportData
    supplier: str | None
    sheet: str
    table: str


# --------------------------------------------------------------------- hooks


def _calc_from_base(row: Row, draft: Draft) -> None:
    """Template sheets: ``DESCUENTO APLICA EN`` says whether the % is on list price or cost."""

    base = n.fold(row.get("DESCUENTO PROVEEDOR APLICA EN", "DESCUENTO APLICA EN"))
    draft.calc = "pct_costo" if base == "costo" else "pct_precio_lista"
    if "supplier_value" not in draft.values and row.get(
        "$ DESCUENTO PROVEEDOR POR UNIDAD", "$ DESCUENTO POR UNIDAD"
    ):
        draft.calc = "valor_unidad"
        draft.values["value_unit"] = "unidad"
        draft.put(
            "supplier_value", row.get("$ DESCUENTO PROVEEDOR POR UNIDAD", "$ DESCUENTO POR UNIDAD")
        )
    draft.values["settlement_method"] = "nota_credito"


def _diversey(row: Row, draft: Draft) -> None:
    """Diversey: ``Npp`` (e.g. ``5pp``) is N % over cost for the whole supplier portfolio.

    It can come in the % or $ support cells or in the SKU cell; either way the row has no
    product and the percentage becomes the support over cost.
    """

    for header in ("% APOYO DIVERSEY", "$ APOYO DIVERSEY", "SKU JDH", "DESCRIPCION REFERENCIA"):
        raw = row.get(header)
        points = n.parse_pp(raw)
        if points is None:
            continue
        draft.calc, draft.portfolio = "pct_costo", True
        draft.values["supplier_value"] = points
        draft.values["program"] = "DESCUENTO PORTAFOLIO DIVERSEY"
        draft.values["conditions"] = (
            f"{str(raw).strip()}: descuento sobre el costo, todo el portafolio"
        )
        draft.issues = [issue for issue in draft.issues if issue.field != "product_sku"]
        return


def _ohra(row: Row, draft: Draft) -> None:
    # The client price applies the % to PVP but the margin applies it to cost.
    draft.issues.append(Issue("base_ambigua", "% sobre PVP en precio y sobre costo en margen"))


def _familia_authorizer(row: Row, draft: Draft) -> None:
    observation = row.get("OBSERVACION")
    approver = n.authorizer_in_text(observation)
    if approver:
        draft.values["authorized_by"] = approver


def _team_regions(row: Row, draft: Draft) -> None:
    cost_region = n.clean_text(row.get("REGIONAL COSTO"))
    if cost_region:
        note = f"Regional costo: {cost_region}"
        draft.values["notes"] = (
            f"{draft.values['notes']} | {note}" if "notes" in draft.values else note
        )


def _kimberly(row: Row, draft: Draft) -> None:
    kind = n.fold(row.get("TIPO DE ACUERDO DE PRECIOS"))
    if kind == "grupo":
        draft.values["customer_group"] = n.clean_text(row.get("NOMBRE DEL ACUERDO DE PRECIOS"))
    # The export pads the name with the address ("NAME  STREET  CO"): keep the name only.
    raw = str(row.get("NOMBRE DE USUARIO FINAL") or "").replace("\xa0", " ").strip()
    name = n.clean_text(re.split(r"\s{2,}", raw)[0]) if raw else None
    if name:
        draft.values["customer_name"] = name[:200]
    draft.values["settlement_method"] = "costo_neto_facturado"
    if draft.values.get("product_sku") is None:
        raw = row.get("REF JDH")
        if raw is not None:
            draft.issues.append(Issue("sku_sin_cruce", str(raw), "product_sku"))
    draft.values["supplier_sku"] = n.clean_sku(row.get("CÓDIGO PRODUCTO"))


def _rubbermaid_client_list(nit: str | None, name: str) -> Hook:
    def hook(row: Row, draft: Draft) -> None:
        if nit:
            draft.values["customer_nit"] = nit
        else:
            # The workbook never says which Sodexo branch this list is for: it is configured
            # by hand from the special case.
            draft.issues.append(Issue("cliente_por_definir", name, "customer_nit"))
        draft.values["customer_name"] = name
        draft.values["program"] = f"LISTA {name}"

    return hook


def _tercerizado(row: Row, draft: Draft) -> None:
    text = row.get("Observacion", "Observación")
    share = n.percent_in_text(text)
    if share is not None:
        draft.values["supplier_value"] = share
    else:
        draft.issues.append(Issue("valor_faltante", "porcentaje no escrito", "supplier_value"))
    draft.values["conditions"] = n.clean_text(text)
    draft.values["settlement_method"] = "nota_credito"


def _recovery(row: Row, draft: Draft) -> None:
    draft.values["supplier_value"] = Decimal("0.5")
    draft.values["conditions"] = "50% en el primer pedido y 15% durante los 3 meses siguientes"
    draft.values["settlement_method"] = "nota_credito"


SOURCES: tuple[Source, ...] = (
    Source(
        "TEAM -PANADERIA",
        "Tabla41",
        TEAM_PANADERIA,
        "actividad_temporal",
        "pct_costo",
        {
            "valid_from": "Fecha Inicio",
            "valid_to": "Fecha Fin",
            "product_sku": "Sku Jdh",
            "supplier_sku": "Sku Team",
            "pack_factor": "U.E",
            "reference_cost": "Costo x Caja Jdh",
        },
        {
            "program": "TEAM PANADERIA 4,8% EN COSTO",
            "regionals": ("ANTIOQUIA",),
            "supplier_value": Decimal("0.048"),
            "value_unit": "caja",
            "target_margin_pct": Decimal("0.10"),
        },
        required=("supplier_sku",),
    ),
    Source(
        "Familia",
        "Apoyos_Familia",
        FAMILIA,
        "tactico_cliente",
        "costo_neto",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "customer_nit": "NIT",
            "customer_name": "RAZON SOCIAL",
            "customer_group": "GRUPO EMPRESARIAL",
            "product_sku": "REF JDH",
            "supplier_sku": "REF FAMILIA",
            "reference_list_price": "PVP X CAJA SIN IVA",
            "reference_cost": "COSTO CAJA",
            "supplier_value": "COSTO APOYADO CAJA",
            "pack_factor": "FACTOR U.E",
            "target_margin_pct": "MARGEN OBJETIVO",
            "business_units": "UN",
            "notes": "OBSERVACION",
        },
        {"value_unit": "caja", "settlement_method": "costo_neto_facturado"},
        required=("customer_nit", "supplier_sku"),
        hook=_familia_authorizer,
    ),
    Source(
        "Familia",
        "Tabla40",
        FAMILIA,
        "actividad_temporal",
        "valor_unidad",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "product_sku": "REF JDH",
            "supplier_sku": "REF FAMILIA",
            "pack_factor": "FACTOR U.E",
            "reference_cost": "COSTO U.E",
            "reference_list_price": "PRECIO REGULAR MAYORISTA",
            "supplier_value": "VALOR DSCTO X UND",
            "business_units": "CANAL",
            "conditions": "OBSERVACION",
        },
        {"value_unit": "unidad", "program": "DESCUENTO MAYORISTAS POR VOLUMEN"},
        required=("supplier_sku",),
    ),
    Source(
        "Unilever",
        "Apoyos_Unilever",
        UNILEVER,
        "tactico_cliente",
        "pct_precio_lista",
        {
            "valid_from": "FECHA INICIO",
            "valid_to": "FECHA FIN",
            "customer_nit": "NIT",
            "customer_name": "RAZON SOCIAL",
            "product_sku": "REF JDH",
            "supplier_sku": "SKU UNILEVER",
            "supplier_value": "% DCTO APOYADO",
            "reference_cost": "COSTO",
            "reference_list_price": "PRECIO LISTA SIN IVA",
            "target_margin_pct": "%MARGEN OPTIMO",
            "notes": "OBSERVACION",
        },
        {"settlement_method": "nota_credito"},
        required=("customer_nit", "supplier_sku"),
        hook=_familia_authorizer,
    ),
    Source(
        "Unilever",
        "Actividades_Unilever",
        UNILEVER,
        "actividad_temporal",
        "pct_precio_lista",
        {
            "valid_from": "FECHA INICIO",
            "valid_to": "FECHA FINAL",
            "program": "NOMBRE ACTIVIDAD",
            "product_sku": "REF. JDH",
            "supplier_sku": "Sku Unilever",
            "supplier_value": "% Dcto",
            "regionals": "C.O",
            "business_units": "CANAL/U.N",
            "reference_list_price": "PVP",
            "notes": "OBERVACIONES",
        },
        {"settlement_method": "pie_factura"},
        required=("program",),
    ),
    Source(
        "Unilever",
        "Tercerizados_Unilever",
        UNILEVER,
        "rebate",
        "pct_ventas",
        {"customer_nit": "NIT Cliente", "customer_name": "RAZON SOCIAL JDH"},
        {"program": "CLIENTES TERCERIZADOS"},
        required=("customer_nit",),
        hook=_tercerizado,
    ),
    Source(
        "Unilever",
        "Actividad_Kilo_My_Unilever",
        UNILEVER,
        "actividad_temporal",
        "pct_costo",
        {
            "program": "Nombre actividad",
            "product_sku": "Referencia",
            "reference_cost": "Costo sin Icui",
            "supplier_value": "Dcto a Costo",
        },
        {
            "business_units": (),
            "channels": ("MAYORISTAS Y DISTRIBUIDORES",),
            "target_margin_pct": Decimal("0.13"),
        },
        required=("program",),
    ),
    Source(
        "Unilever",
        "Tabla37",
        UNILEVER,
        "tactico_cliente",
        "valor_unidad",
        {
            "customer_nit": "NIT",
            "customer_name": "CLIENTE",
            "product_sku": "Cod.",
            "reference_cost": "Costo",
            "supplier_value": "$Dscto Unilever",
            "notes": "Observacion",
        },
        {"program": "NEGOCIACION ANCHETERIA EXPORTACIONES", "value_unit": "unidad"},
        required=("customer_nit", "product_sku"),
    ),
    Source(
        "Unilever",
        "Tercerizados_Unilever20",
        UNILEVER,
        "recuperacion_cliente",
        "pct_ventas",
        {
            "customer_nit": "NIT Cliente",
            "customer_name": "RAZON SOCIAL JDH",
            "regionals": "C.O",
            "business_units": "U.N",
            "valid_from": "Fecha Inicial Dscto 50%",
            "valid_to": "Fecha Final Dscto 15%",
            "notes": "Observacion",
        },
        {"program": "CLIENTES RECUPERADOS"},
        required=("customer_nit",),
        hook=_recovery,
    ),
    Source(
        "Team (Desactualizada)",
        "Apoyo_Team",
        TEAM,
        "tactico_cliente",
        "pct_costo",
        {
            "valid_from": "FECHA INICIO",
            "valid_to": "FECHA FIN",
            "regionals": "REGIONAL FACTURACIÓN",
            "supplier_contact": "RESPONSABLE TEAM",
            "sales_rep": "EJECUTIVO",
            "customer_group": "GRUPO EMPRESARIAL",
            "customer_nit": "NIT",
            "customer_name": "RAZÓN SOCIAL",
            "product_sku": "SKU JDH",
            "supplier_sku": "SKU TEAM",
            "reference_cost": "COSTO DISTRIBUIDOR",
            "list_margin_pct": "MARGEN JDH",
            "reference_list_price": "PVP SIN IVA",
            "supplier_value": "% DCTO TEAM (En Costo)",
            "jdh_pct": "% DCTO JDH (En PVP)",
            "pack_factor": "U.E",
            "notes": "OBSERVACION",
        },
        {"value_unit": "caja", "settlement_method": "nota_credito"},
        required=("customer_nit", "supplier_sku"),
        hook=_team_regions,
    ),
    Source(
        "Team (Desactualizada)",
        "Dscto_My_Team",
        TEAM,
        "actividad_temporal",
        "pct_costo",
        {
            "product_sku": "SKU JDH",
            "supplier_sku": "SKU TEAM",
            "reference_cost": "COSTO DISTRIB.",
            "reference_list_price": "PVP MARGEN 15%",
            "supplier_value": "%DCTO TEAM (En Costo)",
            "jdh_pct": "DCTO JDH",
            "pack_factor": "U.E",
            "target_margin_pct": "Margen Real",
        },
        {
            "program": "APOYOS TACTICOS TEAM MAYORISTAS Y AUTOSERVICIOS",
            "channels": ("MAYORISTAS Y DISTRIBUIDORES", "AUTOSERVICIO"),
            "value_unit": "caja",
        },
        required=("supplier_sku",),
    ),
    Source(
        "Kimberly",
        "Apoyos_Kimberly",
        KIMBERLY,
        "tactico_cliente",
        "costo_neto",
        {
            "external_ref": "N° ACUERDO DE PRECIOS",
            "program": "NOMBRE DEL ACUERDO DE PRECIOS",
            "customer_nit": "NIT SIN DIGITO VERIFICACION",
            "valid_from": "FECHA INICIO ACUERDO",
            "valid_to": "FECHA FIN ACUERDO",
            "product_sku": "REF JDH",
            "supplier_value": "COSTO APOYADO",
            "reference_cost": "COSTO CAJA KCP",
            "pack_factor": "U.E",
            "committed_volume": "VOLUMEN COMPROMETIDO",
        },
        {"value_unit": "caja"},
        required=("external_ref", "supplier_value"),
        hook=_kimberly,
    ),
    Source(
        "Mccain",
        "Apoyo_Mccain",
        MCCAIN,
        "tactico_cliente",
        "pct_costo",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "regionals": "REGIONAL",
            "customer_nit": "NIT CLIENTE",
            "customer_name": "RAZON SOCIAL CLIENTE",
            "product_sku": "REF. JDH",
            "supplier_sku": "SKU MCCAIN",
            "supplier_value": "%DCTO MCCAIN",
            "jdh_pct": "%DCTO JDH",
            "reference_cost": "COSTO",
            "list_margin_pct": "MARGEN LISTA",
            "notes": "OBSERVACIONES",
        },
        {"settlement_method": "nota_credito"},
        required=("customer_nit", "product_sku"),
        hook=_familia_authorizer,
    ),
    Source(
        "Dodeka (Ohra)",
        "Apoyos_Ohra",
        DODEKA,
        "tactico_cliente",
        "pct_precio_lista",
        {
            "regionals": "REGIONAL",
            "sales_rep": "VENDEDOR",
            "valid_from": "FECHA INICIO OFERTA",
            "valid_to": "FECHA VALIDEZ OFERTA",
            "customer_nit": "NIT (Sin Digito Verificación)",
            "customer_name": "RAZON SOCIAL",
            "product_sku": "REF. JDH",
            "pack_factor": "U.E",
            "reference_cost": "COSTO CAJA",
            "reference_list_price": "PVP CAJA SIN IVA",
            "supplier_value": "% APOYO DODEKA",
            "jdh_pct": "% DCTO JDH",
            "notes": "Observaciones",
        },
        {"value_unit": "caja"},
        required=("customer_nit", "product_sku"),
        hook=_ohra,
    ),
    Source(
        "Diversey",
        "Apoyos_Diversey",
        DIVERSEY,
        "tactico_cliente",
        "pct_costo",
        {
            "valid_from": "Fecha Inicial",
            "valid_to": "Fecha Final",
            "customer_nit": "NIT",
            "customer_name": "RAZÓN SOCIAL",
            "regionals": "REGIONAL",
            "product_sku": "SKU JDH",
            "reference_cost": "COSTO UND",
            "supplier_value": "% APOYO DIVERSEY",
            "notes": "OBSERVACION",
        },
        {
            "target_margin_pct": Decimal("0.18"),
            "value_unit": "unidad",
            "settlement_method": "nota_credito",
        },
        required=("customer_nit", "product_sku"),
        hook=_diversey,
    ),
    Source(
        "Rubbermaid",
        "Apoyos_Rubbermaid",
        RUBBERMAID,
        "tactico_cliente",
        "puntos_margen",
        {
            "customer_nit": "NIT",
            "customer_name": "RAZON SOCIAL",
            "customer_group": "GRUPOS EMPRESARIALES",
            "channels": "CANAL",
            "list_margin_pct": "MARGEN LISTA",
            "supplier_value": "% APOYO PROVEEDOR",
            "jdh_pct": "% APOYO JDH",
            "notes": "OBSERVACION",
        },
        {"brand": "RUBBERMAID"},
        required=("customer_nit", "supplier_value"),
    ),
    Source(
        "Rubbermaid",
        "Lista_Summar_RCP",
        RUBBERMAID,
        "lista_especial",
        "costo_neto",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "product_sku": "SKU JDH",
            "supplier_sku": "SKU RUBBERMAID",
            "reference_cost": "COSTO REGULAR",
            "reference_list_price": "PVP SIN IVA",
            "supplier_value": "COSTO APOYADO",
            "target_margin_pct": "%MARGEN GARANTIZADO",
        },
        {"brand": "RUBBERMAID", "settlement_method": "costo_neto_facturado"},
        required=("supplier_sku",),
        hook=_rubbermaid_client_list("800125313", "SUMMAR"),
    ),
    Source(
        "Rubbermaid",
        "Lista_SodexoCedi_RCP",
        RUBBERMAID,
        "lista_especial",
        "costo_neto",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "product_sku": "SKU JDH",
            "supplier_sku": "SKU RUBBERMAID",
            "reference_cost": "COSTO REGULAR",
            "reference_list_price": "PVP SIN IVA",
            "supplier_value": "COSTO APOYADO",
            "target_margin_pct": "%MARGEN GARANTIZADO",
        },
        {"brand": "RUBBERMAID", "settlement_method": "costo_neto_facturado"},
        required=("supplier_sku",),
        hook=_rubbermaid_client_list(None, "SODEXO CEDI"),
    ),
    Source(
        "Aderezos",
        "Apoyos_Aderezos",
        ADEREZOS,
        "tactico_cliente",
        "pct_precio_lista",
        {
            "business_units": "U.N.",
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "regionals": "REGIONAL",
            "customer_nit": "NIT",
            "customer_name": "RAZÓN SOCIAL COMO APARECE EN SIESA",
            "supplier_sku": "COD. SKU ADEREZOS",
            "product_sku": "COD. SKU JDH",
            "reference_cost": "COSTO UNT",
            "reference_list_price": "PVP SIN IVA",
            "supplier_value": "% DCTO ADEREZOS",
            "jdh_pct": "% DCTO JDH",
            "program": "OBSERVACION",
        },
        {"brand": "ADEREZOS", "value_unit": "unidad", "settlement_method": "nota_credito"},
        required=("customer_nit", "product_sku"),
    ),
    Source(
        "Aderezos",
        "Actividades_Descuentos",
        ADEREZOS,
        "actividad_temporal",
        "pct_precio_lista",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "regionals": "REGIONAL",
            "business_units": "U.N.",
            "supplier_sku": "COD. SKU ADEREZOS",
            "product_sku": "COD. SKU JDH",
            "reference_cost": "COSTO UNT",
            "reference_list_price": "PVP SIN IVA",
            "supplier_value": "% DCTO ADEREZOS",
            "jdh_pct": "% DCTO JDH",
            "program": "OBSERVACION",
        },
        {"brand": "ADEREZOS", "value_unit": "unidad", "settlement_method": "pie_factura"},
        required=("supplier_value",),
    ),
    *(
        Source(
            sheet,
            table,
            supplier,
            "tactico_cliente",
            "pct_precio_lista",
            {
                "valid_from": "FECHA INICIAL",
                "valid_to": "FECHA FINAL",
                "regionals": "REGIONAL",
                "business_units": "UNIDAD NEGOCIO",
                "customer_nit": "NIT CLIENTE (Sin Digito Verificacion)",
                "customer_name": "RAZÓN SOCIAL CLIENTE",
                "product_sku": "SKU JDH",
                "reference_cost": "COSTO JDH",
                "reference_list_price": "PRECIO LISTA AL PUBLICO SIN IVA",
                "supplier_value": "% DESCUENTO PROVEEDOR",
                "jdh_pct": "% DESCUENTO JDH",
                **({"supplier_sku": "SKU PROVEEDOR"} if use_supplier_sku else {}),
            },
            {},
            required=("product_sku", "supplier_value"),
            hook=_calc_from_base,
        )
        for sheet, table, supplier, use_supplier_sku in (
            ("Dicorp", "Tabla211", DICORP, False),
            ("Redbull", "Tabla2", RED_BULL, False),
            ("Griffith", "Tabla212", GRIFFITH, True),
            ("Lemon Fresh", "Tabla2122", LEMON_FRESH, False),
            ("Molino Pacande", "Tabla21226", PACANDE, False),
        )
    ),
    Source(
        "Azteca",
        "Tacticos_Azteca",
        AZTECA,
        "tactico_cliente",
        "pct_precio_lista",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "regionals": "REGIONAL",
            "business_units": "U.N.",
            "customer_nit": "NIT",
            "customer_name": "RAZÓN SOCIAL COMO APARECE EN SIESA",
            "product_sku": "SKU JDH",
            "reference_cost": "COSTO JDH",
            "reference_list_price": "PVP SIN IVA",
            "supplier_value": "% DSCTO",
            "notes": "Observaciones",
        },
        {"brand": "AZTECA", "settlement_method": "nota_credito"},
        required=("customer_nit", "product_sku"),
    ),
    Source(
        "Upfield",
        "Apoyos_Upfield",
        UPFIELD,
        "tactico_cliente",
        "puntos_margen",
        {
            "regionals": "C.O",
            "customer_nit": "NIT",
            "customer_name": "RAZON SOCIAL",
            "supplier_value": "% APOYO EN PRECIO",
        },
        {"list_margin_pct": Decimal("0.16")},
        required=("customer_nit",),
    ),
    Source(
        "Upfield",
        "Control_Upfield22",
        UPFIELD,
        "presupuesto",
        "valor_unidad",
        {
            "product_sku": "Referencia",
            "reference_cost": "Costo",
            "supplier_value": "Dif",
            "budget_amount": "Total Apoyo Sin Iva",
            "budget_units": "Cant. Und Apoyadas",
            "units_used": "Cant. Utilizada",
            "sales_rep": "Vendedor",
            "reference_date": "Fecha Ultimo pedido",
        },
        {"value_unit": "unidad", "program": "CONTROL PRESUPUESTO UPFIELD"},
        required=("product_sku",),
    ),
    Source(
        "Grupo Grb (Camarones)",
        "Tabla2122626",
        GRB,
        "excepcion_margen",
        "margen_minimo",
        {
            "valid_from": "FECHA INICIAL",
            "valid_to": "FECHA FINAL",
            "product_sku": "SKU JDH",
            "customer_nit": "NIT CLIENTE (Sin Digito Verificacion)",
            "customer_name": "RAZÓN SOCIAL CLIENTE",
            "min_margin_pct": "MARGEN MIN. PEDIDO AUTORIZADO",
        },
        {"program": "DESCUENTOS TACTICOS AUTORIZADOS"},
        required=("customer_nit", "min_margin_pct"),
    ),
)


# ------------------------------------------------------------------- reading


class Workbook:
    """Cached values of the supports workbook, read table by table."""

    def __init__(self, path: Path) -> None:
        """Open the workbook read-only.

        Args:
            path (Path): The ``.xlsx`` file; it is never modified.

        Raises:
            FileNotFoundError: If the file does not exist.
        """

        if not path.is_file():
            raise FileNotFoundError(path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.book = openpyxl.load_workbook(path, data_only=True)

    def table(self, sheet: str, table: str) -> list[Row]:
        """Rows of one Excel table, keyed by folded header; empty rows are skipped."""

        if sheet not in self.book.sheetnames:
            raise KeyError(f"Falta la hoja {sheet!r}")
        worksheet = self.book[sheet]
        if table not in worksheet.tables:
            raise KeyError(f"Falta la tabla {table!r} en la hoja {sheet!r}")
        cells = worksheet[worksheet.tables[table].ref]
        headers = [n.fold(cell.value) for cell in cells[0]]
        rows = []
        for line in cells[1:]:
            values = {
                header: cell.value
                for header, cell in zip(headers, line, strict=True)
                if header and cell.value is not None
            }
            if values:
                rows.append(Row(line[0].row, values))
        return rows

    def block(self, sheet: str, header_row: int, first: str, last: str) -> list[Row]:
        """Rows under a header row of an untabled range (``Estra``, Azteca rebate list)."""

        worksheet = self.book[sheet]
        cells = worksheet[f"{first}{header_row}:{last}{worksheet.max_row}"]
        headers = [n.fold(cell.value) for cell in cells[0]]
        rows = []
        for line in cells[1:]:
            values = {
                header: cell.value
                for header, cell in zip(headers, line, strict=True)
                if header and cell.value is not None
            }
            if values:
                rows.append(Row(line[0].row, values))
        return rows


def _draft(source: Source, row: Row) -> Draft | None:
    draft = Draft(source.kind, source.calc)
    for name, value in source.fixed.items():
        draft.values[name] = value
    for name, headers in source.cols.items():
        candidates = (headers,) if isinstance(headers, str) else headers
        raw = row.get(*candidates)
        draft.put(name, raw, check_digit=source.check_digit and name == "customer_nit")
    if source.hook is not None:
        source.hook(row, draft)
    if draft.portfolio:
        for name in (
            "product_sku",
            "supplier_sku",
            "reference_cost",
            "reference_list_price",
            "value_unit",
            "pack_factor",
        ):
            draft.values.pop(name, None)
    if source.required and all(
        draft.values.get(name) is None and not any(issue.field == name for issue in draft.issues)
        for name in source.required
    ):
        return None
    return draft


def _to_data(origin: str, draft: Draft, key: str) -> SupportData:
    values = dict(draft.values)
    for name in ("regionals", "business_units", "channels"):
        if name in values:
            values[name] = tuple(values[name])
    return SupportData(
        kind=draft.kind,
        calc_method=draft.calc,
        unit_basis_source="hoja" if values.get("value_unit") else "desconocido",
        source=origin,
        source_key=key,
        issues=tuple(dict.fromkeys(draft.issues)),
        **values,
    )


def read_supports(book: Workbook, only: Sequence[str] | None = None) -> Iterator[ImportedSupport]:
    """Yield every support of the workbook, normalized, in sheet order.

    Args:
        book (Workbook): The opened workbook.
        only (Sequence[str] | None): Sheet names to read; all when ``None``.

    Yields:
        ImportedSupport: One support per row (several for expanded exceptions).
    """

    selected = {name.casefold() for name in only} if only else None
    for source in SOURCES:
        if selected is not None and source.sheet.casefold() not in selected:
            continue
        rows = book.table(source.sheet, source.table)
        drafts = [(row, _draft(source, row)) for row in rows]
        drafts = [(row, draft) for row, draft in drafts if draft is not None]
        if source.detect_sequential_nits and "customer_nit" in source.cols:
            pairs = [
                (draft.values.get("customer_nit"), draft.values.get("customer_name"))
                for _, draft in drafts
            ]
            for index in n.flag_sequential_nits(pairs):
                drafts[index][1].issues.append(
                    Issue("nit_secuencial_sospechoso", "NIT consecutivo del renglón anterior")
                )
        seen: set[tuple[Any, ...]] = set()
        for row, draft in drafts:
            values = draft.values
            signature = (
                values.get("customer_nit"),
                values.get("product_sku") or values.get("supplier_sku"),
                values.get("valid_from"),
                values.get("valid_to"),
                values.get("supplier_value"),
                values.get("jdh_pct"),
                values.get("program"),
            )
            if signature in seen and signature[0] is not None:
                draft.issues.append(Issue("duplicado", "renglón repetido en la misma hoja"))
            seen.add(signature)
            key = f"{source.sheet}/{source.table}:{row.number}"
            yield ImportedSupport(
                _to_data(f"{source.sheet}/{source.table}", draft, key),
                source.supplier,
                source.sheet,
                source.table,
            )
    if selected is None or "estra" in selected:
        yield from _estra(book)
    if selected is None or "azteca" in selected:
        yield from _azteca_rebates(book)
    if selected is None or "condiciones especiales" in selected:
        yield from _special_conditions(book)


def _estra(book: Workbook) -> Iterator[ImportedSupport]:
    """Estra: clearance credit over stock; the sheet has no Excel table."""

    for row in book.block("Estra", 1, "A", "K"):
        sku = n.clean_sku(row.get("Referencia"))
        if sku is None:
            continue  # the totals row
        draft = Draft("liquidacion_inventario", "pct_costo")
        draft.values.update(
            {
                "program": "LIQUIDACION INVENTARIO ESTRA",
                "settlement_method": "nota_credito",
                "value_unit": "unidad",
            }
        )
        draft.put("product_sku", sku)
        draft.put("warehouse", row.get("Bodega"))
        draft.put("reference_cost", row.get("Costo Unitario"))
        draft.put("supplier_value", row.get("Dscto Estra"))
        draft.put("stock_quantity", row.get("Cantidad Actual Bodega"))
        draft.put("notes", row.get("Observaciones"))
        draft.put("conditions", row.get("Liq"))
        yield ImportedSupport(
            _to_data("Estra", draft, f"Estra:{row.number}"), ESTRA, "Estra", "(sin tabla)"
        )


def _azteca_rebates(book: Workbook) -> Iterator[ImportedSupport]:
    """Azteca: clients with a rebate whose percentage and rules are not in the workbook."""

    sheet = book.book["Azteca"]
    for line in sheet["X2:Y40"]:
        nit = n.parse_nit(line[0].value)
        if nit is None:
            continue
        draft = Draft("rebate", "pct_ventas")
        draft.values.update(
            {
                "program": "CLIENTES CON REBATE AZTECA",
                "brand": "AZTECA",
                "settlement_method": "rebate_periodico",
                "customer_nit": nit,
            }
        )
        draft.put("customer_name", line[1].value)
        draft.issues.append(Issue("valor_faltante", "porcentaje y reglas no están en el Excel"))
        yield ImportedSupport(
            _to_data("Azteca/rebate", draft, f"Azteca/rebate:{line[0].row}"),
            AZTECA,
            "Azteca",
            "(rebate)",
        )


def _special_conditions(book: Workbook) -> Iterator[ImportedSupport]:
    """Condiciones Especiales: authorized minimum margins, expanded by supplier x SKU x client."""

    for row in book.table("Condiciones Especiales", "Condiciones_Especiales"):
        suppliers = [
            line for line in str(row.get("PROVEEDOR") or "").splitlines() if n.clean_text(line)
        ] or [None]
        skus = n.skus_in_text(row.get("REFERENCIAS")) or [None]
        nits = n.nits_in_text(row.get("CLIENTES")) or [None]
        if len(suppliers) * len(skus) * len(nits) > MAX_EXPANSION:
            suppliers, skus, nits = suppliers[:1], [None], [None]
        regions = row.get("C.O")
        scope = row.get("U.N/CANAL")
        text_refs = n.clean_text(row.get("REFERENCIAS"))
        text_clients = n.clean_text(row.get("CLIENTES"))
        margin_raw = row.get("MARGEN MINIMO")
        index = 0
        for supplier_line in suppliers:
            for sku in skus:
                for nit in nits:
                    index += 1
                    draft = Draft("excepcion_margen", "margen_minimo")
                    draft.values["program"] = "CONDICIONES ESPECIALES"
                    draft.put("regionals", regions)
                    draft.put("business_units", scope)
                    draft.put("valid_from", row.get("VIGENCIA INICIAL"))
                    draft.put("valid_to", row.get("VIGENCIA FINAL"))
                    draft.put("authorized_by", row.get("AUTORIZA"))
                    draft.put("notes", row.get("OBSERVACIONES"))
                    if sku:
                        draft.put("product_sku", sku)
                    if nit:
                        draft.put("customer_nit", nit)
                    detail = []
                    if supplier_line:
                        detail.append(f"Proveedor (Excel): {n.clean_text(supplier_line)}")
                    if sku is None and text_refs and n.fold(text_refs) not in {"todas", "todos"}:
                        detail.append(f"Referencias: {text_refs}")
                    if nit is None and text_clients and n.fold(text_clients) != "todos":
                        detail.append(f"Clientes: {text_clients}")
                    if detail:
                        draft.values["conditions"] = " | ".join(detail)
                    margin, found = n.parse_fraction(margin_raw, "min_margin_pct")
                    if margin is not None:
                        draft.values["min_margin_pct"] = margin
                    elif margin_raw is not None:
                        percent = n.percent_in_text(margin_raw)
                        if percent is not None:
                            draft.values["min_margin_pct"] = percent
                        draft.values["notes"] = " | ".join(
                            filter(
                                None, [draft.values.get("notes"), f"Margen (Excel): {margin_raw}"]
                            )
                        )
                    draft.issues.extend(found)
                    key = f"Condiciones Especiales:{row.number}#{index}"
                    supplier = f"name:{n.clean_text(supplier_line)}" if supplier_line else None
                    yield ImportedSupport(
                        _to_data("Condiciones Especiales", draft, key),
                        supplier,
                        "Condiciones Especiales",
                        "Condiciones_Especiales",
                    )


# ---------------------------------------------------- supplier catalogs/margins

# (sheet, table, product column, supplier-SKU column, pack column, kg column, supplier code)
_SKU_TABLES: tuple[tuple[str, str, str, str | None, str | None, str | None, str], ...] = (
    ("Familia", "Maestro_Sku_Familia", "Ref. JDH", "Ref. Familia", "Factor U.E", None, FAMILIA),
    ("Unilever", "Maestro_Sku_Unilever", "Cod. Jdh", "Cod. Unilever", None, None, UNILEVER),
    ("Team (Desactualizada)", "Maestro_Sku_Team", "COD JDH", "COD TEAM", "U.E", None, TEAM),
    ("Kimberly", "Maestro_Sku_Kimberly", "Sku JDH", "Sku KCP", "Factor U.E", None, KIMBERLY),
    ("Mccain", "Maestro_Sku_Mccain", "REF. JDH", "SKU MCCAIN", "FACTOR U.E", "KG X UND.", MCCAIN),
    ("Diversey", "Maestro_Sku_Diversey36", "Cod. Jdh", "Cod. Diversey", None, None, DIVERSEY),
    ("Aderezos", "MaestroSku", "COD. SKU JDH", "COD. SKU ADEREZOS", "Factor U.E", None, ADEREZOS),
)


def read_supplier_codes(
    book: Workbook, only: Sequence[str] | None = None
) -> list[tuple[str, SupplierProductCode]]:
    """Supplier SKU catalogs (``Maestro_Sku_*``) as ``(supplier ref, code)`` pairs."""

    selected = {name.casefold() for name in only} if only else None
    found: list[tuple[str, SupplierProductCode]] = []
    for sheet, table, product, supplier_sku, pack, kg, supplier in _SKU_TABLES:
        if selected is not None and sheet.casefold() not in selected:
            continue
        for row in book.table(sheet, table):
            sku, code = n.clean_sku(row.get(product)), n.clean_sku(row.get(supplier_sku or ""))
            if sku is None or code is None:
                continue
            pack_value, _ = n.parse_number(row.get(pack or ""), "pack_factor")
            kg_value, _ = n.parse_number(row.get(kg or ""), "kg_per_unit")
            found.append(
                (
                    supplier,
                    SupplierProductCode(
                        commercial_supplier_code=supplier,
                        supplier_sku=code,
                        product_sku=sku,
                        pack_factor=pack_value if pack_value and pack_value > 0 else None,
                        kg_per_unit=kg_value if kg_value and kg_value > 0 else None,
                        notes=f"{sheet}/{table}",
                    ),
                )
            )
    return found
