"""Controlled vocabularies of supplier supports.

Each tuple is the closed set stored in a ``String`` column guarded by a
``CheckConstraint`` (the same pattern the rest of the platform uses for enums).
"""

from __future__ import annotations

KINDS = (
    "tactico_cliente",  # support for one client (usually x SKU)
    "actividad_temporal",  # named promotion, no client
    "lista_especial",  # special price list for one client (discontinued)
    "rebate",  # credit note / rebate on sales (kept apart for a later module)
    "recuperacion_cliente",  # client acquisition or recovery program (discontinued)
    "liquidacion_inventario",  # clearance / price protection over stock (discontinued)
    "presupuesto",  # capped budget with consumption (kept apart, like rebates)
    "excepcion_margen",  # authorized minimum margin, no supplier money
)

# The support classes (rules v2): the only ones that can be created, loaded or edited into.
# The others stay readable: rebates and budgets wait for their own module; the discontinued
# ones are purged (``scripts/purge-discontinued-supports.py``).
ACTIVE_KINDS = ("tactico_cliente", "actividad_temporal", "excepcion_margen")
KIND_LABELS = {
    "tactico_cliente": "Apoyo táctico",
    "actividad_temporal": "Actividad descuento temporal",
    "excepcion_margen": "Excepción margen JDH",
    "lista_especial": "Lista especial",
    "rebate": "Rebate",
    "recuperacion_cliente": "Recuperación de cliente",
    "liquidacion_inventario": "Liquidación de inventario",
    "presupuesto": "Presupuesto",
}
DISCONTINUED_KINDS = ("lista_especial", "recuperacion_cliente", "liquidacion_inventario")

FUNDINGS = ("proveedor", "jdh", "mixto")

SETTLEMENTS = ("nota_credito", "pie_factura", "costo_neto_facturado", "rebate_periodico")

CALC_METHODS = (
    "pct_costo",  # % Apoyo en Costo: supplier_value is a fraction of cost
    "pct_precio_lista",  # % Apoyo en Precio Lista: fraction of the list price
    "valor_unidad",  # $ Apoyo en Costo: fixed amount per unit (or box)
    "valor_precio_lista",  # $ Apoyo en Precio Lista: fixed amount off the list price
    "costo_neto",  # $ Costo Apoyado: supplier_value is the agreed net cost
    "precio_neto",  # % Margen Garantizado: supplier_value is the agreed price, target margin
    "puntos_margen",  # margin points off list margin (no longer offered)
    "pct_ventas",  # rebate over sales; no per-unit amount (rebates only)
    "margen_minimo",  # only a minimum order margin, no supplier money (JDH exception)
)

# How each calc method is written in the general template ("Tipo apoyo" column). The file
# may carry the label, an older label (see ``CALC_METHOD_ALIASES``) or the code.
CALC_METHOD_LABELS = {
    "pct_costo": "% Apoyo en Costo",
    "pct_precio_lista": "% Apoyo en Precio Lista",
    "valor_unidad": "$ Apoyo en Costo",
    "valor_precio_lista": "$ Apoyo en Precio Lista",
    "costo_neto": "$ Costo Apoyado",
    "precio_neto": "% Margen Garantizado",
    "margen_minimo": "Margen mínimo autorizado",
    "puntos_margen": "Puntos de margen",
    "pct_ventas": "% sobre ventas",
}
# Labels of the first general template, still accepted in files.
CALC_METHOD_ALIASES = {
    "% sobre costo": "pct_costo",
    "% sobre precio lista": "pct_precio_lista",
    "costo apoyado": "costo_neto",
    "precio apoyado sin iva": "precio_neto",
    "$ por unidad": "valor_unidad",
    "margen minimo": "margen_minimo",
}
# The types each class can be created with (rules v2).
_TACTICAL_METHODS = (
    "pct_costo",
    "pct_precio_lista",
    "valor_unidad",
    "valor_precio_lista",
    "costo_neto",
    "precio_neto",
)
METHODS_BY_KIND = {
    "tactico_cliente": _TACTICAL_METHODS,
    "actividad_temporal": ("pct_costo", "pct_precio_lista", "valor_unidad", "valor_precio_lista"),
    "excepcion_margen": ("margen_minimo",),
}

VALUE_UNITS = ("unidad", "caja", "kg")

# SIESA warehouse group 23 (PROMOCIONES), read by the DWH team on 2026-11-17 (t151/t152).
# A support applies there only with ``promo_warehouses``. When ``consumo.inventario_fecha``
# carries ``bodega_promocional`` this list is read from the DWH instead.
PROMO_WAREHOUSE_GROUP = "23"
PROMO_WAREHOUSES = frozenset(
    {
        "26",
        "MDE10",
        "MDE27",
        "MDE28",
        "MDE30",
        "OCC17",
        "OCC18",
        "OCC19",
        "BTA23",
        "BTA24",
        "CTG18",
        "CTG20",
        "BGA13",
        "URA17",
        "VUP12",
        "BAQ11",
        "BAQ14",
        "BGA12",
    }
)

STATUSES = ("activo", "pendiente_revision", "anulado")

THRESHOLD_TYPES = ("unidades", "cajas", "monto_ventas")

# Whether ``valid_from``/``valid_to`` put the support in force on a given day.
VALIDITIES = ("vigente", "vencido", "programado")

# The one state shown to people: ``anulado`` once voided, ``inactivo`` while switched off
# (reversible), otherwise the validity. Whether the data still needs review is a separate
# flag (``needs_review``), never a state.
STATES = (*VALIDITIES, "inactivo", "anulado")

# Where the unit of a cost (``value_unit``: per box or per unit) comes from.
UNIT_BASIS_SOURCES = ("hoja", "inferido_dwh", "manual", "desconocido")

# What wrote a version of a support (traceability, published to the DWH with each version).
ORIGINS = (
    "importacion",  # the workbook importer script
    "manual",  # created by hand
    "lote",  # created by the JSON batch endpoint
    "carga",  # bulk load by file (with its load id)
    "edicion",  # edited by hand
    "anulacion",  # voided
    "vencimiento",  # closed by a bulk load that no longer brings it
    "aprobacion_jdh",  # a JDH contribution request was approved
    "cancelacion_jdh",  # the JDH contribution was cancelled
    "solicitud_interna",  # approved internal support request from a quotation
)

# Internal support requests (from a customer quotation).
INTERNAL_STATUSES = ("pendiente", "aprobada", "rechazada")
INTERNAL_DECISIONS = ("persistente", "unica_vez")

SPECIAL_CASE_STATUSES = ("pendiente", "en_analisis", "programado", "implementado", "descartado")
SPECIAL_CASE_PRIORITIES = ("baja", "media", "alta")

# Issues the importer and the services attach to a support.
ISSUE_CODES = (
    "fecha_invalida",
    "fecha_texto",
    "nit_sin_cruce",
    "nit_secuencial_sospechoso",
    "sku_sin_cruce",
    "proveedor_sin_cruce",
    "duplicado",
    "base_ambigua",
    "valor_texto",
    "valor_faltante",
    "margen_negativo",
    "region_desconocida",
    "alcance_multivalor",
    "cliente_por_definir",
    "base_desconocida",
)
