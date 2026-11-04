"""Closed values of the quoter and their Spanish labels."""

from __future__ import annotations

# Life of a price list: the advisor edits a draft and confirms it; the approver validates
# it, and only then are the files generated and the list counts as created or updated.
DRAFT = "borrador"
PENDING = "pendiente_validacion"
CREATED = "creada"
UPDATED = "actualizada"
STATUSES = (DRAFT, PENDING, CREATED, UPDATED)
CLOSED = (CREATED, UPDATED)
STATUS_LABELS = {
    DRAFT: "Borrador",
    PENDING: "Pendiente de validación",
    CREATED: "Creada",
    UPDATED: "Actualizada",
}

# What the files do in SIESA: update the customer's list or create a new one.
UPDATE_LIST = "actualizacion"
CREATE_LIST = "creacion"
LIST_ACTIONS = (UPDATE_LIST, CREATE_LIST)
LIST_ACTION_LABELS = {UPDATE_LIST: "Actualización de lista", CREATE_LIST: "Creación de lista"}
# Status a validated quotation ends in, by list action.
CLOSED_BY_ACTION = {UPDATE_LIST: UPDATED, CREATE_LIST: CREATED}

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

# Where a line comes from.
HISTORY = "historico"
ADDED = "agregado"
ORIGINS = (HISTORY, ADDED)
ORIGIN_LABELS = {HISTORY: "Histórico", ADDED: "Agregado"}

# Which cost won: the supplier's quotation in force or the inventory average cost.
QUOTE_COST = "cotizacion"
INVENTORY_COST = "inventario"
COST_SOURCE_LABELS = {QUOTE_COST: "Cotización del proveedor", INVENTORY_COST: "Inventario"}

# Liquor tax regional: the Coast (billing operation center 004) pays its own value.
NATIONAL = "nacional"
COAST = "costa"
REGIONAL_LABELS = {NATIONAL: "Nacional", COAST: "Costa"}

INACTIVE_ITEM = "referencia_inactiva"
PROBLEM_LABELS = {
    "sin_costo": "Sin costo actual",
    "sin_margen_objetivo": "El producto no tiene margen para el canal",
    "producto_no_sincronizado": "La referencia no está en el maestro de productos",
    "apoyo_condicionado": "El apoyo exige una cantidad mínima",
    "apoyo_en_revision": "Hay un apoyo en revisión que no se aplica",
    "apoyo_fuera_bodega": "Hay un apoyo de una bodega puntual que no se aplica",
    INACTIVE_ITEM: "Referencia inactiva en SIESA: reemplázala; no sale en los archivos",
    "cotizacion_descartada": "Hay una cotización del proveedor fuera de rango (más de 3 veces el "
    "último costo): no se usa; revísala en SIESA",
}

WARNING_LABELS = {
    "sin_canal": "El cliente no tiene canal: se usa el margen Lista 1",
    "cliente_inactivo": "El cliente no tiene sucursales activas",
    "listas_distintas": "Las sucursales del cliente tienen listas de precios distintas",
    "co_distintos": "Las sucursales del cliente facturan desde centros de operación distintos",
    "lista_compartida": "La lista de precios es compartida: un cambio afecta a otros clientes",
    "sin_lista": "El cliente no tiene lista de precios: se usa la lista 1",
    "sin_ventas": "El cliente no tiene ventas en la ventana: agrega las referencias a cotizar",
    "lista_general": "El cliente está en la lista 1, que no se actualiza por este medio: "
    "se crea una lista nueva",
}

BASIS_LABELS = {
    "lista": "Margen Lista 1",
    "mayoristas": "Margen Mayoristas",
    "salsamentarias": "Margen Salsamentarias",
    "apoyo": "Objetivo del apoyo",
}

# Price list used when the customer has none in SIESA (``GENERAL``).
DEFAULT_PRICE_LIST = "1"
# The general list is never updated from a quotation: a customer on it gets a new list.
GENERAL_PRICE_LIST = "1"
