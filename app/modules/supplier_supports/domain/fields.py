"""Field catalog of a support: one description shared by the creation form, the bulk template
and the bulk file reader, so the three never drift apart.

``aliases`` are extra spellings of the header a supplier may send; headers are matched folded
(lower-case ASCII, single spaces), so accents and case do not matter.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.supplier_supports.domain import vocabulary

# How a value is typed in the form and read from a file.
TEXT, SKU, NIT, DATE, NUMBER, FRACTION, CHOICE, LIST, FLAG = (
    "text",
    "sku",
    "nit",
    "date",
    "number",
    "fraction",
    "choice",
    "list",
    "flag",
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
    # Whether ``form-schema`` lists it (the creation form has its own control otherwise).
    in_form: bool = True


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
        "Clase apoyo",
        GROUP_IDENTITY,
        CHOICE,
        ("clase de apoyo", "clase", "tipo"),
        vocabulary.KINDS,
        "Apoyo táctico, Actividad descuento temporal o Excepción margen JDH. En la carga, si "
        "falta: excepción con margen mínimo, táctico si hay NIT y actividad si no.",
        core=True,
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
    FieldSpec(
        "customer_branch_id",
        "Sucursal",
        GROUP_SCOPE,
        TEXT,
        ("sucursal cliente", "id sucursal"),
        help="Solo esa sucursal del cliente. Vacío = todas.",
        max_length=20,
        core=True,
    ),
    FieldSpec(
        "customer_name",
        "Cliente",
        GROUP_SCOPE,
        TEXT,
        ("razon social", "nombre cliente"),
        help="Se completa desde el maestro de clientes si se deja vacío.",
        max_length=300,
        core=True,
    ),
    FieldSpec(
        "customer_group",
        "Grupo Empresarial",
        GROUP_SCOPE,
        TEXT,
        ("grupo cliente", "grupo"),
        help="Nombre del grupo empresarial: crea un apoyo por cada cliente activo del grupo "
        "(sin NIT).",
        max_length=200,
        core=True,
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
        help="Se toma del maestro de productos con el SKU JDH. Solo si no hay SKU JDH, se usa "
        "para encontrarlo. No es parte de la llave.",
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
        core=True,
    ),
    FieldSpec(
        "business_units",
        "Unidad Negocio",
        GROUP_SCOPE,
        LIST,
        ("unidad de negocio", "unidades de negocio", "un", "u n"),
        help="Varias separadas por coma o barra. TODOS = sin restricción.",
        core=True,
    ),
    FieldSpec(
        "channels",
        "Canal",
        GROUP_SCOPE,
        LIST,
        ("canales",),
        help="Varios separados por coma o barra. TODOS = sin restricción.",
        core=True,
    ),
    FieldSpec("warehouse", "Bodega", GROUP_SCOPE, TEXT, max_length=50, core=True),
    FieldSpec(
        "promo_warehouses",
        "Bodega promocional",
        GROUP_SCOPE,
        FLAG,
        ("bodegas promocionales", "incluir bodegas promocionales", "aplica en promocion"),
        ("Sí", "No"),
        "Sí = el apoyo también aplica en las bodegas promocionales (grupo 23); No = no hay "
        "doble apoyo sobre un producto en promoción. Vacía = el valor elegido para la carga "
        "(No si no se eligió).",
        in_form=False,
    ),
    FieldSpec(
        "calc_method",
        "Tipo apoyo",
        GROUP_BENEFIT,
        CHOICE,
        (
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
    ),
    FieldSpec(
        "supported_margin_pct",
        "Excepción margen mínimo autorizado JDH",
        GROUP_BENEFIT,
        FRACTION,
        (
            "margen apoyado",
            "margen autorizado",
            "margen minimo apoyado",
            "margen apoyado autorizado",
            "excepcion margen minimo",
            "margen minimo autorizado jdh",
        ),
        help="Obligatorio en una Excepción margen JDH: es su margen mínimo autorizado. En los "
        "demás tipos es opcional y da el Precio Apoyado Mínimo = (costo − apoyo) / (1 − margen). "
        "En la plantilla, con % Margen Garantizado es el margen garantizado.",
        core=True,
    ),
    FieldSpec(
        "value_unit",
        "Unidad valor",
        GROUP_BENEFIT,
        CHOICE,
        ("unidad del valor", "unidad del costo", "unidad", "unidad costo", "costo por"),
        vocabulary.VALUE_UNITS,
        "Unidad o Caja: un valor en pesos por caja se divide por la unidad de empaque del "
        "maestro de productos (vacía = 1). Vacío = unidad.",
        core=True,
    ),
    FieldSpec(
        "target_margin_pct",
        "Margen garantizado",
        GROUP_BENEFIT,
        FRACTION,
        ("margen objetivo", "margen optimo", "% margen optimo", "% margen garantizado"),
        help="Obligatorio con % Margen Garantizado (el Valor apoyo es el precio apoyado).",
    ),
    FieldSpec(
        "min_margin_pct",
        "Margen Mínimo",
        GROUP_BENEFIT,
        FRACTION,
        ("margen minimo autorizado", "margen min pedido autorizado"),
        help="Solo con Tipo de apoyo = Margen mínimo: el margen de pedido autorizado.",
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
        help="dd/mm/aaaa o aaaa-mm-dd. Forma parte de la llave del apoyo (proveedor, NIT, "
        "SKU JDH y fecha inicio).",
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
        help="Solo en la descarga con datos: el motivo de una Validación No.",
        max_length=2000,
    ),
)

# Rules v2: the order of the creation form and of the template (BI, 2026-11-17); the other
# fields follow in their catalog order.
_ORDER = (
    "kind",
    "calc_method",
    "supplier_value",
    "value_unit",
    "supported_margin_pct",
    "valid_from",
    "valid_to",
    "customer_nit",
    "customer_name",
    "customer_branch_id",
    "customer_group",
    "product_sku",
    "supplier_sku",
    "regionals",
    "business_units",
    "channels",
    "warehouse",
    "promo_warehouses",
)
FIELD_SPECS = tuple(
    sorted(
        FIELD_SPECS,
        key=lambda spec: _ORDER.index(spec.name) if spec.name in _ORDER else len(_ORDER),
    )
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
        "percent",
        ("supplier_value",),
        _COMMON,
        "$ Apoyo = costo × %. Precio apoyado sugerido = precio lista − $ Apoyo.",
    ),
    "pct_precio_lista": MethodRule(
        "percent",
        ("supplier_value",),
        _COMMON,
        "$ Apoyo = precio lista × % (el valor absoluto se resta al costo). Precio apoyado "
        "sugerido = precio lista − $ Apoyo.",
    ),
    "valor_unidad": MethodRule(
        "money",
        ("supplier_value",),
        _COMMON,
        "$ Apoyo en costo, por unidad o por caja (la caja se divide por la unidad de empaque).",
    ),
    "valor_precio_lista": MethodRule(
        "money",
        ("supplier_value",),
        _COMMON,
        "$ Apoyo que se descuenta del precio lista, por unidad o por caja.",
    ),
    "costo_neto": MethodRule(
        "money",
        ("supplier_value",),
        _COMMON,
        "$ Costo apoyado pactado: $ Apoyo = costo − costo apoyado.",
    ),
    "precio_neto": MethodRule(
        "none",
        ("target_margin_pct",),
        ("reference_cost", "reference_list_price", "pack_factor", "target_margin_pct"),
        "% Margen Garantizado: el proveedor garantiza ese margen a cualquier precio de venta y "
        "pone lo que falte: $ Apoyo = máx(0, costo − precio × (1 − margen)). No lleva valor; "
        "el margen con el apoyo queda en máx(margen sin apoyo, margen garantizado).",
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
    # The exception's margin is written in «Excepción margen mínimo autorizado JDH», the one JDH
    # margin the form and the template show; min_margin_pct is still accepted (kept equal).
    "margen_minimo": MethodRule(
        "none",
        ("supported_margin_pct",),
        ("supported_margin_pct",),
        "Excepción margen JDH: no usa valor del proveedor; el margen mínimo autorizado es "
        "obligatorio.",
    ),
}


# Required in every support: the form asks them and the bulk load rejects a row without them
# (it deduces the class only for a file with no Clase apoyo column).
REQUIRED_ALWAYS = ("kind", "calc_method")


def active_methods() -> tuple[str, ...]:
    """The calc methods of the classes that are still created, in their order."""

    return tuple(dict.fromkeys(m for ms in vocabulary.METHODS_BY_KIND.values() for m in ms))


def methods_requiring() -> dict[str, tuple[str, ...]]:
    """``field -> active calc methods that require it`` (from :data:`METHOD_RULES`)."""

    found: dict[str, list[str]] = {}
    for method in active_methods():
        for name in METHOD_RULES[method].requires:
            found.setdefault(name, []).append(method)
    return {name: tuple(methods) for name, methods in found.items()}


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
            if spec.in_form
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
        "required_always": list(REQUIRED_ALWAYS),
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
    "REQUIRED_ALWAYS",
    "FieldSpec",
    "active_methods",
    "form_schema",
    "methods_requiring",
]
