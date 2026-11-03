"""Closed values of the quoter and their Spanish labels."""

from __future__ import annotations

DRAFT = "borrador"
GENERATED = "generado"
STATUSES = (DRAFT, GENERATED)
STATUS_LABELS = {DRAFT: "Borrador", GENERATED: "Generado"}

# Months of sales history a quotation looks back on.
WINDOWS = (6, 12)

MEETS = "cumple"
BELOW_MINIMUM = "bajo_minimo"
NO_TARGET = "sin_objetivo"
NO_COST = "sin_costo"
COMPLIANCE = (MEETS, BELOW_MINIMUM, NO_TARGET, NO_COST)
COMPLIANCE_LABELS = {
    MEETS: "Cumple",
    BELOW_MINIMUM: "Bajo el mínimo",
    NO_TARGET: "Sin margen objetivo",
    NO_COST: "Sin costo",
}

PROBLEM_LABELS = {
    "sin_costo": "Sin costo actual",
    "costo_global": "Costo promedio de todas las bodegas (el CO del cliente no tiene existencia)",
    "sin_margen_objetivo": "El producto no tiene margen para el canal",
    "producto_no_sincronizado": "La referencia no está en el maestro de productos",
    "sin_precio_lista": "Sin precio vigente en la lista del cliente",
    "apoyo_condicionado": "El apoyo exige una cantidad mínima",
    "apoyo_en_revision": "Hay un apoyo en revisión que no se aplica",
    "apoyo_fuera_bodega": "Hay un apoyo de una bodega puntual que no se aplica",
}

WARNING_LABELS = {
    "sin_canal": "El cliente no tiene canal: se usa el margen Lista 1",
    "cliente_inactivo": "El cliente no tiene sucursales activas",
    "listas_distintas": "Las sucursales del cliente tienen listas de precios distintas",
    "co_distintos": "Las sucursales del cliente facturan desde centros de operación distintos",
    "lista_compartida": "La lista de precios es compartida: un cambio afecta a otros clientes",
    "sin_lista": "El cliente no tiene lista de precios: se usa la lista 1",
}

BASIS_LABELS = {
    "lista": "Margen Lista 1",
    "mayoristas": "Margen Mayoristas",
    "salsamentarias": "Margen Salsamentarias",
    "apoyo": "Objetivo del apoyo",
}

# Price list used when the customer has none in SIESA (``GENERAL``).
DEFAULT_PRICE_LIST = "1"
