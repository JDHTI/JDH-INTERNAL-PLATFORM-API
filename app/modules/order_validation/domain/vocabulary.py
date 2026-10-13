"""Controlled vocabularies of the retained orders validator."""

from __future__ import annotations

READY = "listo_para_liberar"
PENDING = "pendiente_validacion"

# The status of a line and of an order: an order is ready only when every line is.
STATUSES = (READY, PENDING)

STATUS_LABELS = {
    READY: "Listo para liberación",
    PENDING: "Pendiente de validación",
}

# Why a line got its status. The first two release it; the rest leave it pending. The margin
# threshold does not act on lines: it acts on the margin of the whole order (see rules).
REASONS = (
    "cumple_con_apoyo",  # an active agreement brings the margin to what was agreed
    "cumple_margen_lista",  # the margin already reaches the product's margin for the channel
    "acuerdo_en_revision",  # it would comply, but the agreement still has data issues
    "no_cumple_acuerdo",  # there is an agreement and the order does not meet it
    "acuerdo_no_evaluable",  # there is an agreement, but it cannot be checked per order
    "acuerdo_vencido",  # an agreement covers the line, but not on the order date
    "sin_acuerdo",  # no agreement covers the line
    "datos_incompletos",  # the file row lacks the supplier, price or margin
)

READY_REASONS = ("cumple_con_apoyo", "cumple_margen_lista")

REASON_LABELS = {
    "cumple_con_apoyo": "Cumple con el apoyo pactado",
    "cumple_margen_lista": "Cumple el margen del producto para el canal",
    "acuerdo_en_revision": "Apoyo pendiente de revisión",
    "no_cumple_acuerdo": "No cumple las condiciones del apoyo",
    "acuerdo_no_evaluable": "Apoyo no evaluable por pedido",
    "acuerdo_vencido": "Apoyo fuera de vigencia",
    "sin_acuerdo": "Sin apoyo registrado",
    "datos_incompletos": "Datos incompletos en el archivo",
}

# Result of checking one agreement against one line.
CHECK_OUTCOMES = ("cumple", "no_cumple", "no_evaluable")
