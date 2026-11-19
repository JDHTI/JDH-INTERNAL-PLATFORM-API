"""Closed values of the quoter and their Spanish labels."""

from __future__ import annotations

# What a quotation is: a customer quotation (``cotizador`` module) or the update of an
# existing SIESA price list (``actualizacion-listas`` module, BI). Both share the engine,
# the states and the files; a list update has no customer offer and no quantities.
QUOTATION = "cotizacion"
LIST_UPDATE = "lista"
# The update of one of the main lists (general, wholesale and Market): module
# ``listas-principales``, locked to BI. ``actualizacion-listas`` never opens those lists.
MAIN_LIST = "principal"
KINDS = (QUOTATION, LIST_UPDATE, MAIN_LIST)
# Kinds that update an existing price list: reviewed item by item, loaded from the staging.
LIST_KINDS = (LIST_UPDATE, MAIN_LIST)
# Module of the catalog that grants each kind.
KIND_MODULES = {
    QUOTATION: "cotizador",
    LIST_UPDATE: "actualizacion-listas",
    MAIN_LIST: "listas-principales",
}
KIND_LABELS = {
    QUOTATION: "Cotizador",
    LIST_UPDATE: "Actualización de listas",
    MAIN_LIST: "Listas principales",
}

# Review of each item of a list update: the editor marks it ready, the validator validates
# it. A validated item waits in the staging of its list until it is loaded in SIESA.
UNMARKED = "sin_marcar"
TO_VALIDATE = "por_validar"
VALIDATED = "validada"
REVIEW_STATUSES = (UNMARKED, TO_VALIDATE, VALIDATED)
REVIEW_LABELS = {UNMARKED: "Sin marcar", TO_VALIDATE: "Por validar", VALIDATED: "Validada"}
# Longest observation on an item.
NOTE_LENGTH = 500
# Months of list price versions an item's history reads (the DWH keeps them from 2026 for
# the main lists: older versions are not in SIESA any more).
PRICE_HISTORY_MONTHS = 24
# Where a list update starts: a customer (its assigned list) or the list itself (by code or
# name).
SOURCE_CUSTOMER = "cliente"
SOURCE_LIST = "lista"
SOURCES = (SOURCE_CUSTOMER, SOURCE_LIST)
# Months of sales a list update looks back on for the last sale of each SKU.
LIST_UPDATE_WINDOW = 12

# Life of a quotation: the advisor edits a draft and confirms it; the approver validates
# it (the files are released and wait to be loaded in SIESA) and then marks it loaded. A
# list update stays a draft: its items are reviewed one by one (``REVIEW_STATUSES``).
#
# Sending the offer to the customer is not a status (BI, 2026-11-19): it needs no validation
# and only stamps ``sent_at``; the quotation stays a draft and goes to validation only to
# update or create a price list.
DRAFT = "borrador"
PENDING = "pendiente_validacion"
UPLOAD_PENDING = "pendiente_carga"
UPLOADED = "cargada"
STATUSES = (DRAFT, PENDING, UPLOAD_PENDING, UPLOADED)
# The files can be downloaded, and nothing of the quotation changes any more.
CLOSED = (UPLOAD_PENDING, UPLOADED)
STATUS_LABELS = {
    DRAFT: "Borrador",
    PENDING: "Pendiente de validación",
    UPLOAD_PENDING: "Pendiente de carga",
    UPLOADED: "Cargada",
}

# What the files do in SIESA: update the customer's list or create a new one.
UPDATE_LIST = "actualizacion"
CREATE_LIST = "creacion"
LIST_ACTIONS = (UPDATE_LIST, CREATE_LIST)
LIST_ACTION_LABELS = {UPDATE_LIST: "Actualización de lista", CREATE_LIST: "Creación de lista"}

# Where the quotation's billing operation center comes from: the customer's branches, or
# chosen by the advisor (a refresh keeps it).
CENTER_AUTO = "auto"
CENTER_MANUAL = "manual"
CENTER_SOURCES = (CENTER_AUTO, CENTER_MANUAL)
# Longest text of a temporary customer's fields.
TEMPORARY_TEXT_LENGTH = 120

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

# Where a line comes from. ``local`` is a product the advisor quotes that SIESA does not
# have: it has no cost or rule and never reaches the files.
HISTORY = "historico"
ADDED = "agregado"
LOCAL = "local"
# A SKU with a price in force on the list a list update starts from.
FROM_LIST = "lista"
ORIGINS = (HISTORY, ADDED, LOCAL, FROM_LIST)
ORIGIN_LABELS = {
    HISTORY: "Histórico",
    ADDED: "Agregado",
    LOCAL: "Solo cotización",
    FROM_LIST: "En la lista",
}
# Stored category of a SIESA line that was looked up and has none (no product row, or no
# category): it is not looked up again. The API shows it as ``null``.
NO_CATEGORY = ""
# SKU of a local product: ``LOC-<n>``, numbered per quotation.
LOCAL_SKU_PREFIX = "LOC-"
# VAT points a local product may carry.
LOCAL_VAT_POINTS = (19, 5, 0)

# The customer offer: how long it holds, which lines it carries, its conditions.
DEFAULT_VALIDITY_DAYS = 30
MAX_VALIDITY_DAYS = 365
# ``changed``: active lines with a new price (local products too); ``all``: every active
# line with an offer price.
OFFER_CHANGED = "changed"
OFFER_ALL = "all"
OFFER_SCOPES = (OFFER_CHANGED, OFFER_ALL)
MAX_CONDITIONS = 20
CONDITION_LENGTH = 300
# Quantities are in inventory units, with up to two decimals.
MAX_QUANTITY = 10_000_000

# Which cost won: the supplier's quotation in force, the inventory average cost, or a price
# increase SIESA does not have yet (product master's history, no system update date).
QUOTE_COST = "cotizacion"
INVENTORY_COST = "inventario"
INCREASE_COST = "alza"
COST_SOURCE_LABELS = {
    QUOTE_COST: "Cotización del proveedor",
    INVENTORY_COST: "Inventario",
    INCREASE_COST: "Alza de precios pendiente",
}

# Liquor tax regional: the Coast (billing operation center 004) pays its own value.
NATIONAL = "nacional"
COAST = "costa"
REGIONAL_LABELS = {NATIONAL: "Nacional", COAST: "Costa"}

INACTIVE_ITEM = "referencia_inactiva"
LOCAL_PRODUCT = "producto_local"
PROBLEM_LABELS = {
    LOCAL_PRODUCT: "Producto solo para cotización: no existe en SIESA y no sale en los archivos",
    "sin_costo": "Sin costo actual",
    "sin_margen_objetivo": "El producto no tiene margen para el canal",
    "producto_no_sincronizado": "La referencia no está en el maestro de productos",
    "apoyo_condicionado": "El apoyo exige una cantidad mínima",
    "apoyo_en_revision": "Hay un apoyo en revisión que no se aplica",
    "apoyo_fuera_bodega": "Hay un apoyo de una bodega puntual que no se aplica",
    INACTIVE_ITEM: "Referencia inactiva en SIESA: reemplázala; no sale en los archivos",
    "precio_marcador": "La lista tiene un precio marcador ($1 o menos): no hay precio actual "
    "que comparar",
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
    "lista_sin_clientes": "La lista no tiene clientes activos: se usa el margen Lista 1 y el "
    "costo Nacional",
    "lista_sin_precios": "La lista no tiene precios vigentes: agrega las referencias a actualizar",
    "ultima_venta_no_leida": "El datawarehouse no respondió a tiempo con la última venta de "
    "cada referencia: actualiza para volver a intentarlo",
}

BASIS_LABELS = {
    "lista": "Margen Food Service",
    "mayoristas": "Margen Mayoristas",
    "salsamentarias": "Margen Salsamentarias",
    "masivo_food": "Margen Masivo Food",
    "market": "Margen Market Apartadó",
    "apoyo": "Objetivo del apoyo",
}

# Price list used when the customer has none in SIESA (``GENERAL``).
DEFAULT_PRICE_LIST = "1"
# The general list is never updated from a quotation: a customer on it gets a new list.
GENERAL_PRICE_LIST = "1"
