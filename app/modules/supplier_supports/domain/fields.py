"""Field catalog of a support: one description shared by the creation form, the bulk template
and the bulk file reader, so the three never drift apart.

``aliases`` are extra spellings of the header a supplier may send; headers are matched folded
(lower-case ASCII, single spaces), so accents and case do not matter.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.supplier_supports.domain import vocabulary

# How a value is typed in the form and read from a file.
TEXT, SKU, NIT, DATE, NUMBER, FRACTION, CHOICE, LIST = (
    "text",
    "sku",
    "nit",
    "date",
    "number",
    "fraction",
    "choice",
    "list",
)

GROUP_IDENTITY = "Identidad"
GROUP_SCOPE = "Alcance"
GROUP_BENEFIT = "Beneficio"
GROUP_REFERENCE = "Referencia"
GROUP_CONDITIONS = "Condiciones"
GROUP_VALIDITY = "Vigencia"
GROUP_SETTLEMENT = "Liquidación"
GROUP_TRACE = "Trazabilidad"


@dataclass(frozen=True)
class FieldSpec:
    """One field a person can fill for a support."""

    name: str
    label: str
    group: str
    type: str
    aliases: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    help: str = ""
    # Whether the bulk template carries a column for it.
    in_template: bool = True
    max_length: int | None = None
    # Whether the short general template (the default download) carries it.
    core: bool = False


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "supplier_code",
        "Cod. Proveedor",
        GROUP_IDENTITY,
        TEXT,
        ("codigo proveedor", "cod proveedor", "proveedor codigo"),
        help="Código del proveedor comercial. Puede omitirse si se elige en la carga.",
        max_length=20,
    ),
    FieldSpec(
        "kind",
        "Clase de apoyo",
        GROUP_IDENTITY,
        CHOICE,
        ("clase apoyo", "clase", "tipo"),
        vocabulary.KINDS,
        "Clase de apoyo. En la carga, si falta: excepcion_margen con margen mínimo, "
        "tactico_cliente si hay NIT y actividad_temporal si no.",
    ),
    FieldSpec(
        "funding",
        "Quién financia",
        GROUP_IDENTITY,
        CHOICE,
        ("financia", "financiador", "fuente"),
        vocabulary.FUNDINGS,
        "Quién asume el apoyo. Por defecto: proveedor.",
    ),
    FieldSpec(
        "program",
        "Programa",
        GROUP_IDENTITY,
        TEXT,
        ("nombre actividad", "actividad", "campana", "nombre programa"),
        max_length=200,
    ),
    FieldSpec(
        "external_ref",
        "Referencia externa",
        GROUP_IDENTITY,
        TEXT,
        ("ref externa", "consecutivo proveedor", "no apoyo", "n acuerdo", "no acuerdo"),
        help="Número o código con el que el proveedor identifica el apoyo (acuerdo).",
        max_length=200,
    ),
    FieldSpec(
        "customer_nit",
        "NIT Cliente",
        GROUP_SCOPE,
        NIT,
        ("nit", "nit cliente", "nit del cliente"),
        help="Sin dígito de verificación. Vacío = todos los clientes.",
        max_length=30,
        core=True,
    ),
    FieldSpec("customer_branch_id", "Sucursal Cliente", GROUP_SCOPE, TEXT, ("sucursal",)),
    FieldSpec(
        "customer_name",
        "Cliente",
        GROUP_SCOPE,
        TEXT,
        ("razon social", "nombre cliente"),
        help="Se completa desde el maestro de clientes si se deja vacío.",
        max_length=300,
    ),
    FieldSpec(
        "customer_group",
        "Grupo Cliente",
        GROUP_SCOPE,
        TEXT,
        ("grupo empresarial", "grupo"),
        max_length=200,
    ),
    FieldSpec(
        "product_sku",
        "SKU JDH",
        GROUP_SCOPE,
        SKU,
        ("referencia jdh", "sku jdh", "ref jdh", "ref. jdh", "referencia", "sku"),
        help="Referencia (SKU) del maestro de productos. Vacío = todo el portafolio.",
        max_length=50,
        core=True,
    ),
    FieldSpec(
        "supplier_sku",
        "SKU Proveedor",
        GROUP_SCOPE,
        SKU,
        ("referencia proveedor", "sku proveedor", "ref proveedor", "codigo proveedor sku", "ref"),
        help="Código del producto en el proveedor; se usa si no hay SKU JDH.",
        max_length=100,
        core=True,
    ),
    FieldSpec("brand", "Marca", GROUP_SCOPE, TEXT, max_length=100),
    FieldSpec(
        "regionals",
        "Regional",
        GROUP_SCOPE,
        LIST,
        ("regionales", "c o", "centro de operacion"),
        help="Varias separadas por coma, barra o punto y coma. TODOS = sin restricción.",
    ),
    FieldSpec(
        "business_units",
        "Unidad de Negocio",
        GROUP_SCOPE,
        LIST,
        ("unidades de negocio", "un", "u n"),
        help="Varias separadas por coma o barra. TODOS = sin restricción.",
    ),
    FieldSpec(
        "channels",
        "Canal",
        GROUP_SCOPE,
        LIST,
        ("canales",),
        help="Varios separados por coma o barra. TODOS = sin restricción.",
    ),
    FieldSpec("warehouse", "Bodega", GROUP_SCOPE, TEXT, max_length=50),
    FieldSpec(
        "calc_method",
        "Tipo de apoyo",
        GROUP_BENEFIT,
        CHOICE,
        (
            "tipo apoyo",
            "tipo de apoyo",
            "metodo",
            "metodo calculo",
            "metodo de calculo",
            "base del apoyo",
        ),
        vocabulary.CALC_METHODS,
        "Cómo se lee el valor del apoyo: "
        + ", ".join(vocabulary.CALC_METHOD_LABELS.values())
        + " (o su código).",
        core=True,
    ),
    FieldSpec(
        "supplier_value",
        "Valor apoyo",
        GROUP_BENEFIT,
        NUMBER,
        (
            "valor apoyo proveedor",
            "valor proveedor",
            "apoyo",
            "% apoyo",
            "% dcto",
            "descuento",
            "costo apoyado",
        ),
        help="Porcentaje (5 %, 0,05 o 5) o dinero, según el tipo de apoyo.",
        core=True,
    ),
    FieldSpec(
        "jdh_pct",
        "% Apoyo JDH",
        GROUP_BENEFIT,
        FRACTION,
        ("apoyo jdh", "% jdh", "descuento jdh", "% dcto jdh"),
        help="Descuento que pone JDH, aparte del proveedor. Si se deja vacío al actualizar, "
        "se conserva el aprobado.",
        core=True,
    ),
    FieldSpec(
        "value_unit",
        "Unidad del valor",
        GROUP_BENEFIT,
        CHOICE,
        ("unidad del costo", "unidad", "unidad costo", "costo por"),
        vocabulary.VALUE_UNITS,
        "Si el valor en pesos está por caja o por unidad. Vacío = se infiere del DWH.",
        core=True,
    ),
    FieldSpec(
        "target_margin_pct",
        "Margen garantizado",
        GROUP_BENEFIT,
        FRACTION,
        ("margen objetivo", "margen optimo", "% margen optimo", "% margen garantizado"),
        help="Obligatorio con precio apoyado sin IVA.",
        core=True,
    ),
    FieldSpec(
        "min_margin_pct",
        "Margen Mínimo",
        GROUP_BENEFIT,
        FRACTION,
        ("margen minimo autorizado", "margen min pedido autorizado"),
        help="Solo con Tipo de apoyo = Margen mínimo: el margen de pedido autorizado.",
        core=True,
    ),
    FieldSpec("list_margin_pct", "Margen Lista", GROUP_BENEFIT, FRACTION, ("margen lista 1",)),
    FieldSpec(
        "pack_factor",
        "Factor de empaque",
        GROUP_REFERENCE,
        NUMBER,
        ("factor u e", "u e", "unidades por caja", "empaque", "factor"),
        help="Unidades por caja. Si falta, se toma del maestro de productos.",
    ),
    FieldSpec(
        "reference_cost",
        "Costo Referencia",
        GROUP_REFERENCE,
        NUMBER,
        ("costo", "costo regular", "costo caja", "costo und"),
    ),
    FieldSpec(
        "reference_list_price",
        "Precio Lista Referencia",
        GROUP_REFERENCE,
        NUMBER,
        ("precio lista", "precio lista sin iva", "pvp", "lista"),
    ),
    FieldSpec(
        "reference_date",
        "Fecha Referencia",
        GROUP_REFERENCE,
        DATE,
        ("fecha costo", "fecha lista"),
    ),
    FieldSpec(
        "min_quantity",
        "Cantidad Mínima",
        GROUP_CONDITIONS,
        NUMBER,
        ("minimo", "cantidad minima compra"),
    ),
    FieldSpec(
        "committed_volume",
        "Volumen Comprometido",
        GROUP_CONDITIONS,
        NUMBER,
        ("volumen", "volumen compromiso"),
    ),
    FieldSpec("budget_amount", "Presupuesto", GROUP_CONDITIONS, NUMBER, ("presupuesto valor",)),
    FieldSpec(
        "budget_units",
        "Presupuesto Unidades",
        GROUP_CONDITIONS,
        NUMBER,
        ("unidades presupuesto",),
    ),
    FieldSpec("units_used", "Unidades Usadas", GROUP_CONDITIONS, NUMBER, ("consumo",)),
    FieldSpec(
        "stock_quantity",
        "Inventario",
        GROUP_CONDITIONS,
        NUMBER,
        ("stock", "cantidad inventario"),
    ),
    FieldSpec(
        "conditions",
        "Condiciones",
        GROUP_CONDITIONS,
        TEXT,
        ("condicion", "condiciones comerciales"),
        max_length=2000,
    ),
    FieldSpec(
        "valid_from",
        "Fecha Inicio",
        GROUP_VALIDITY,
        DATE,
        ("vigencia inicial", "fecha inicial", "vigencia desde", "inicio", "desde"),
        help="dd/mm/aaaa o aaaa-mm-dd. Forma parte de la llave del apoyo.",
        core=True,
    ),
    FieldSpec(
        "valid_to",
        "Fecha Fin",
        GROUP_VALIDITY,
        DATE,
        ("vigencia final", "fecha final", "vigencia hasta", "fin", "hasta"),
        help="INDEFINIDO o UFN = sin fecha final.",
        core=True,
    ),
    FieldSpec(
        "end_condition",
        "Condición de fin",
        GROUP_VALIDITY,
        TEXT,
        ("hasta nivelar costo",),
        max_length=300,
    ),
    FieldSpec(
        "settlement_method",
        "Forma de liquidación",
        GROUP_SETTLEMENT,
        CHOICE,
        ("liquidacion", "metodo liquidacion", "forma de cobro", "metodologia cobro"),
        vocabulary.SETTLEMENTS,
    ),
    FieldSpec(
        "settlement_frequency",
        "Frecuencia de liquidación",
        GROUP_SETTLEMENT,
        TEXT,
        ("frecuencia",),
        max_length=100,
    ),
    FieldSpec(
        "authorized_by",
        "Autorizado por",
        GROUP_TRACE,
        TEXT,
        ("autoriza", "autorizado"),
        max_length=200,
    ),
    FieldSpec(
        "supplier_contact",
        "Contacto Proveedor",
        GROUP_TRACE,
        TEXT,
        ("contacto", "asesor proveedor"),
        max_length=200,
    ),
    FieldSpec(
        "sales_rep",
        "Vendedor",
        GROUP_TRACE,
        TEXT,
        ("ejecutivo", "ejecutivo comercial"),
        max_length=200,
    ),
    FieldSpec(
        "notes",
        "Observaciones",
        GROUP_TRACE,
        TEXT,
        ("observacion", "notas", "comentarios", "obervaciones"),
        max_length=2000,
        core=True,
    ),
)

FIELDS: dict[str, FieldSpec] = {spec.name: spec for spec in FIELD_SPECS}

# Calc methods whose ``supplier_value`` is a fraction of something (typed as a percentage).
PERCENT_METHODS = ("pct_costo", "pct_precio_lista", "puntos_margen", "pct_ventas")
FRACTION_FIELDS = tuple(spec.name for spec in FIELD_SPECS if spec.type == FRACTION)


@dataclass(frozen=True)
class MethodRule:
    """What a calc method asks for and how its value is read."""

    value_kind: str  # percent | money | none
    requires: tuple[str, ...]
    shows: tuple[str, ...]
    help: str = ""


_COMMON = ("reference_cost", "reference_list_price", "pack_factor", "value_unit")

METHOD_RULES: dict[str, MethodRule] = {
    "pct_costo": MethodRule(
        "percent", ("supplier_value",), _COMMON, "Fracción del costo: costo × valor."
    ),
    "pct_precio_lista": MethodRule(
        "percent",
        ("supplier_value",),
        _COMMON,
        "Fracción del precio lista, acreditada contra el costo.",
    ),
    "valor_unidad": MethodRule(
        "money", ("supplier_value",), _COMMON, "Monto fijo por unidad (o caja)."
    ),
    "costo_neto": MethodRule(
        "money", ("supplier_value",), _COMMON, "Costo apoyado pactado: costo − valor."
    ),
    "precio_neto": MethodRule(
        "money",
        ("supplier_value", "target_margin_pct"),
        (*_COMMON, "target_margin_pct"),
        "Precio neto pactado; el margen objetivo es el margen garantizado.",
    ),
    "puntos_margen": MethodRule(
        "percent",
        ("supplier_value",),
        ("list_margin_pct",),
        "Puntos de margen que se restan al margen lista.",
    ),
    "pct_ventas": MethodRule(
        "percent", ("supplier_value",), (), "Rebate sobre ventas; se liquida por periodo."
    ),
    "margen_minimo": MethodRule(
        "none", ("min_margin_pct",), ("min_margin_pct",), "No usa valor del proveedor."
    ),
}


def form_schema() -> dict[str, object]:
    """Fields (grouped, typed, with choices) and the rules of each calc method."""

    return {
        "fields": [
            {
                "name": spec.name,
                "label": spec.label,
                "group": spec.group,
                "type": spec.type,
                "choices": list(spec.choices),
                "help": spec.help,
                "max_length": spec.max_length,
            }
            for spec in FIELD_SPECS
        ],
        "methods": {
            name: {
                "value_kind": rule.value_kind,
                "requires": list(rule.requires),
                "shows": list(rule.shows),
                "help": rule.help,
            }
            for name, rule in METHOD_RULES.items()
        },
        "required_always": ["kind", "calc_method"],
        # How each calc method reads in the general template's "Tipo de apoyo" column.
        "calc_method_labels": dict(vocabulary.CALC_METHOD_LABELS),
        "tiers": {"threshold_types": list(vocabulary.THRESHOLD_TYPES), "max": 20},
    }


__all__ = [
    "FIELDS",
    "FIELD_SPECS",
    "FRACTION_FIELDS",
    "METHOD_RULES",
    "PERCENT_METHODS",
    "FieldSpec",
    "form_schema",
]
