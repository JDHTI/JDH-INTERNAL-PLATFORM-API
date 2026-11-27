"""Controlled vocabularies of the retained orders validator."""

from __future__ import annotations

READY = "listo_para_liberar"
PENDING = "pendiente_validacion"
# A person marked the order as released; only orders take it, never a line.
RELEASED = "liberado"

# The status of a line: an order is ready only when every line is.
STATUSES = (READY, PENDING)
# The status of an order: the validation's, or released by a person.
ORDER_STATUSES = (READY, PENDING, RELEASED)

STATUS_LABELS = {
    READY: "Listo para liberación",
    PENDING: "Pendiente de validación",
    RELEASED: "Liberado",
}

# Why a line got its status. The first two release it; the rest leave it pending. The margin
# threshold does not act on lines: it acts on the margin of the whole order (see rules).
REASONS = (
    "cumple_con_apoyo",  # an active agreement brings the margin to what was agreed
    "cumple_margen_lista",  # the margin already reaches the product's margin for the channel
    "precio_libre",  # the warehouse's average cost is 0 or 1: the price is not checked
    "margen_100_con_existencias",  # 100 % margin, but the warehouse has stock: releasable
    "falta_impuesto_licor",  # it complies, but the product's liquor tax is missing in the line
    "acuerdo_en_revision",  # it would comply, but the agreement still has data issues
    "no_cumple_acuerdo",  # there is an agreement and the order does not meet it
    "acuerdo_no_evaluable",  # there is an agreement, but it cannot be checked per order
    "acuerdo_vencido",  # an agreement covers the line, but not on the order date
    "sin_acuerdo",  # no agreement covers the line
    "margen_100",  # the line has a 100 % margin: no cost, it would inflate the order margin
    "datos_incompletos",  # the file row lacks the supplier, price or margin
)

READY_REASONS = (
    "cumple_con_apoyo",
    "cumple_margen_lista",
    "precio_libre",
    "margen_100_con_existencias",
)

REASON_LABELS = {
    "cumple_con_apoyo": "Cumple con el apoyo pactado",
    "cumple_margen_lista": "Cumple el margen del producto para el canal",
    "precio_libre": "Costo 0 o 1: precio libre",
    "margen_100_con_existencias": "Margen 100 % con existencias",
    "falta_impuesto_licor": "Falta el impuesto al licor",
    "acuerdo_en_revision": "Apoyo pendiente de revisión",
    "no_cumple_acuerdo": "No cumple las condiciones del apoyo",
    "acuerdo_no_evaluable": "Apoyo no evaluable por pedido",
    "acuerdo_vencido": "Apoyo fuera de vigencia",
    "sin_acuerdo": "Sin apoyo registrado",
    "margen_100": "Margen 100 %, revisar",
    "datos_incompletos": "Datos incompletos en el archivo",
}

# Why an order is ready for release.
RELEASE_BASES = ("lineas", "margen", "tolerancia", "manual")

# Warnings raised on an order (and on the lines behind them) for a person to look at.
ALERTS = ("margen_100", "bajo_objetivo", "falta_impuesto_licor", "costo_distinto")

ALERT_LABELS = {
    "margen_100": "Margen 100 %, revisar",
    "bajo_objetivo": "Producto muy por debajo de su margen objetivo",
    "falta_impuesto_licor": "Falta el impuesto al licor",
    "costo_distinto": "El costo del pedido no es el costo promedio de la bodega",
}

# Result of checking one agreement against one line.
CHECK_OUTCOMES = ("cumple", "no_cumple", "no_evaluable")
