"""Controlled vocabularies of supplier supports.

Each tuple is the closed set stored in a ``String`` column guarded by a
``CheckConstraint`` (the same pattern the rest of the platform uses for enums).
"""

from __future__ import annotations

KINDS = (
    "tactico_cliente",  # support for one client (usually x SKU)
    "actividad_temporal",  # named promotion, no client
    "lista_especial",  # special price list for one client
    "rebate",  # credit note / rebate on sales
    "recuperacion_cliente",  # client acquisition or recovery program
    "liquidacion_inventario",  # clearance / price protection over stock
    "presupuesto",  # capped budget with consumption
    "excepcion_margen",  # authorized minimum margin, no supplier money
)

FUNDINGS = ("proveedor", "jdh", "mixto")

SETTLEMENTS = ("nota_credito", "pie_factura", "costo_neto_facturado", "rebate_periodico")

CALC_METHODS = (
    "pct_costo",  # supplier_value is a fraction of cost
    "pct_precio_lista",  # fraction of list price, credited against cost
    "valor_unidad",  # fixed amount per unit
    "costo_neto",  # supplier_value is the agreed net cost
    "precio_neto",  # supplier_value is the agreed net price; target_margin_pct is guaranteed
    "puntos_margen",  # supplier_value is margin points off list margin
    "pct_ventas",  # rebate over sales; no per-unit amount
    "margen_minimo",  # only a minimum order margin, no supplier money
)

# How each calc method is written in the general template ("Tipo de apoyo" column). The file
# may carry the label or the code.
CALC_METHOD_LABELS = {
    "pct_costo": "% sobre costo",
    "pct_precio_lista": "% sobre precio lista",
    "costo_neto": "Costo apoyado",
    "precio_neto": "Precio apoyado sin IVA",
    "valor_unidad": "$ por unidad",
    "margen_minimo": "Margen mínimo",
    "puntos_margen": "Puntos de margen",
    "pct_ventas": "% sobre ventas",
}

VALUE_UNITS = ("unidad", "caja", "kg")

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
)

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
