"""Dynamic filter language of ``POST /supplier-supports/search``.

A request is a tree of conditions::

    {"match": "all", "conditions": [
        {"field": "supplier.code", "op": "in", "value": ["233", "399"]},
        {"field": "supplier_value", "op": "between", "value": [0.05, 0.2]},
        {"match": "any", "conditions": [
            {"field": "channels", "op": "overlaps", "value": ["HOTELES"]},
            {"field": "channels", "op": "is_empty"}]}]}

Only fields in :data:`FIELDS` exist and each type allows a fixed set of
operators, so a client can never reach a column or an expression that is not
listed here. Values are coerced and validated before reaching the repository.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.core.errors import InvalidInputError
from app.modules.commercial_masters.domain.entities import clean_nit
from app.modules.supplier_supports.domain import vocabulary

MAX_DEPTH = 3
MAX_CONDITIONS = 50
MAX_LIST_VALUES = 500

TEXT_OPS = ("eq", "neq", "in", "nin", "contains", "starts_with", "is_null", "not_null")
NUMBER_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "is_null", "not_null")
BOOL_OPS = ("eq", "is_null", "not_null")
ARRAY_OPS = ("overlaps", "contains_all", "is_empty", "not_empty")
UUID_OPS = ("eq", "neq", "in", "nin", "is_null", "not_null")
ISSUE_OPS = ("overlaps", "is_empty", "not_empty")
OPS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "text": TEXT_OPS,
    "enum": ("eq", "neq", "in", "nin", "is_null", "not_null"),
    "number": NUMBER_OPS,
    "date": NUMBER_OPS,
    "datetime": NUMBER_OPS,
    "bool": BOOL_OPS,
    "array": ARRAY_OPS,
    "uuid": UUID_OPS,
    "issues": ISSUE_OPS,
}


@dataclass(frozen=True)
class FieldDef:
    """A field a client may filter or sort by."""

    name: str
    type: str
    label: str
    values: tuple[str, ...] = ()
    sortable: bool = True


def _text(name: str, label: str) -> FieldDef:
    return FieldDef(name, "text", label)


def _number(name: str, label: str) -> FieldDef:
    return FieldDef(name, "number", label)


def _date(name: str, label: str) -> FieldDef:
    return FieldDef(name, "date", label)


def _enum(name: str, label: str, values: Sequence[str]) -> FieldDef:
    return FieldDef(name, "enum", label, tuple(values))


_FIELD_LIST = (
    FieldDef("id", "uuid", "Identificador", sortable=False),
    FieldDef("special_case_id", "uuid", "Caso especial", sortable=False),
    FieldDef("is_enabled", "bool", "Activado", sortable=False),
    _enum("unit_basis_source", "Origen de la unidad", vocabulary.UNIT_BASIS_SOURCES),
    _enum("kind", "Tipo de apoyo", vocabulary.KINDS),
    _enum("funding", "Fuente del apoyo", vocabulary.FUNDINGS),
    _enum("calc_method", "Método de cálculo", vocabulary.CALC_METHODS),
    _enum("settlement_method", "Forma de liquidación", vocabulary.SETTLEMENTS),
    _enum("status", "Estado", vocabulary.STATUSES),
    _enum("value_unit", "Unidad del valor", vocabulary.VALUE_UNITS),
    _text("program", "Programa"),
    _text("external_ref", "Referencia externa"),
    _text("customer_nit", "NIT cliente"),
    _text("customer_name", "Cliente"),
    _text("customer_group", "Grupo empresarial"),
    _text("product_sku", "Referencia JDH"),
    _text("supplier_sku", "Referencia proveedor"),
    _text("brand", "Marca"),
    FieldDef("regionals", "array", "Regionales", sortable=False),
    FieldDef("business_units", "array", "Unidades de negocio", sortable=False),
    FieldDef("channels", "array", "Canales", sortable=False),
    _text("warehouse", "Bodega"),
    _number("supplier_value", "Valor del proveedor"),
    _number("jdh_pct", "% JDH"),
    _number("target_margin_pct", "Margen objetivo"),
    _number("min_margin_pct", "Margen mínimo"),
    _number("list_margin_pct", "Margen lista"),
    _number("reference_cost", "Costo de referencia"),
    _number("reference_list_price", "Precio lista de referencia"),
    _number("min_quantity", "Cantidad mínima"),
    _number("budget_amount", "Presupuesto"),
    _date("valid_from", "Vigencia inicial"),
    _date("valid_to", "Vigencia final"),
    _date("reference_date", "Fecha de referencia"),
    _text("authorized_by", "Autoriza"),
    _text("sales_rep", "Ejecutivo / vendedor"),
    _text("source", "Origen"),
    FieldDef("created_at", "datetime", "Creado"),
    FieldDef("updated_at", "datetime", "Actualizado"),
    FieldDef("issues.code", "issues", "Inconsistencias", vocabulary.ISSUE_CODES, sortable=False),
    _text("supplier.code", "Código proveedor"),
    _text("supplier.name", "Proveedor"),
    _text("product.description", "Descripción producto"),
    _text("product.brand", "Marca del producto"),
    _text("product.category", "Categoría"),
    _text("product.subcategory", "Subcategoría"),
    _text("product.line", "Línea"),
    _text("product.manufacturer", "Fabricante"),
    FieldDef("product.is_active", "bool", "Producto activo", sortable=False),
    FieldDef("customer.channel", "text", "Canal del cliente", sortable=False),
    FieldDef("customer.business_unit", "text", "Unidad de negocio del cliente", sortable=False),
    FieldDef("customer.business_group", "text", "Grupo empresarial del cliente", sortable=False),
    FieldDef("customer.city", "text", "Ciudad del cliente", sortable=False),
    FieldDef("customer.salesperson", "text", "Vendedor del cliente", sortable=False),
    FieldDef("customer.is_active", "bool", "Cliente activo", sortable=False),
)
FIELDS: dict[str, FieldDef] = {field.name: field for field in _FIELD_LIST}


@dataclass(frozen=True)
class Condition:
    """A validated leaf condition."""

    field: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class Group:
    """A validated group of conditions joined by ``all`` (AND) or ``any`` (OR)."""

    match: str
    items: tuple[Condition | Group, ...]


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidInputError("Valor numérico inválido")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInputError(f"Valor numérico inválido: {value!r}") from exc


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise InvalidInputError(f"Fecha inválida (use AAAA-MM-DD): {value!r}") from exc


def _datetime_value(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise InvalidInputError(f"Fecha y hora inválida (ISO 8601): {value!r}") from exc


def _scalar(definition: FieldDef, value: Any) -> Any:
    kind = definition.type
    if kind == "number":
        return _to_decimal(value)
    if kind == "date":
        return _date_value(value)
    if kind == "datetime":
        return _datetime_value(value)
    if kind == "uuid":
        try:
            return value if isinstance(value, UUID) else UUID(str(value))
        except ValueError as exc:
            raise InvalidInputError(f"Identificador inválido: {value!r}") from exc
    if kind == "bool":
        if not isinstance(value, bool):
            raise InvalidInputError(f"{definition.label}: se espera verdadero o falso")
        return value
    text = " ".join(str(value).split())
    if kind in ("enum", "issues") and text not in definition.values:
        allowed = ", ".join(definition.values)
        raise InvalidInputError(f"{definition.label}: valor inválido {text!r} (válidos: {allowed})")
    return text


def _values(definition: FieldDef, value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)) or not value:
        raise InvalidInputError(f"{definition.label}: se espera una lista con valores")
    if len(value) > MAX_LIST_VALUES:
        raise InvalidInputError(f"{definition.label}: máximo {MAX_LIST_VALUES} valores")
    return [_scalar(definition, item) for item in value]


def parse_condition(raw: Mapping[str, Any]) -> Condition:
    """Validate one leaf condition.

    Args:
        raw (Mapping[str, Any]): ``{"field": ..., "op": ..., "value": ...}``.

    Returns:
        Condition: The condition with its value coerced to the field's type.

    Raises:
        InvalidInputError: If the field, operator or value is not allowed.
    """

    name, op = raw.get("field"), raw.get("op")
    definition = FIELDS.get(name) if isinstance(name, str) else None
    if definition is None:
        raise InvalidInputError(f"Campo de filtro desconocido: {name!r}")
    if op not in OPS_BY_TYPE[definition.type]:
        allowed = ", ".join(OPS_BY_TYPE[definition.type])
        raise InvalidInputError(f"Operador {op!r} no válido para {name} (válidos: {allowed})")
    if op in ("is_null", "not_null", "is_empty", "not_empty"):
        return Condition(definition.name, op)
    value = raw.get("value")
    if definition.name == "customer_nit" and op in ("eq", "neq", "in", "nin"):
        value = (
            [clean_nit(item) or "" for item in value]
            if isinstance(value, list)
            else clean_nit(value)
        )
    if op in ("in", "nin", "overlaps", "contains_all"):
        return Condition(definition.name, op, _values(definition, value))
    if op == "between":
        bounds = _values(definition, value)
        if len(bounds) != 2 or bounds[0] > bounds[1]:
            raise InvalidInputError(f"{definition.label}: 'between' espera [desde, hasta]")
        return Condition(definition.name, op, bounds)
    if value is None:
        raise InvalidInputError(f"{definition.label}: falta el valor")
    return Condition(definition.name, op, _scalar(definition, value))


def parse_group(raw: Mapping[str, Any] | None, depth: int = 1) -> Group:
    """Validate a tree of conditions.

    Args:
        raw (Mapping[str, Any] | None): ``{"match": "all"|"any", "conditions": [...]}``.
        depth (int): Current nesting level.

    Returns:
        Group: The validated tree; empty when ``raw`` is ``None``.

    Raises:
        InvalidInputError: If the tree is too deep or too large, or a node is invalid.
    """

    if raw is None:
        return Group("all", ())
    if depth > MAX_DEPTH:
        raise InvalidInputError(f"Los filtros admiten como máximo {MAX_DEPTH} niveles")
    match = raw.get("match", "all")
    if match not in ("all", "any"):
        raise InvalidInputError("match debe ser 'all' o 'any'")
    nodes = raw.get("conditions") or []
    if not isinstance(nodes, list) or len(nodes) > MAX_CONDITIONS:
        raise InvalidInputError(f"Se admiten como máximo {MAX_CONDITIONS} condiciones por grupo")
    items: list[Condition | Group] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            raise InvalidInputError("Cada condición debe ser un objeto")
        if "conditions" in node:
            items.append(parse_group(node, depth + 1))
        else:
            items.append(parse_condition(node))
    return Group(match, tuple(items))


@dataclass(frozen=True)
class SortKey:
    """One ordering criterion."""

    field: str
    descending: bool


def parse_sort(raw: Sequence[str] | None) -> tuple[SortKey, ...]:
    """Validate ordering keys such as ``-valid_from`` or ``supplier.name``.

    Args:
        raw (Sequence[str] | None): Keys; a leading ``-`` means descending.

    Returns:
        tuple[SortKey, ...]: Validated keys.

    Raises:
        InvalidInputError: If a key is not a sortable field.
    """

    keys = []
    for item in raw or ():
        descending = item.startswith("-")
        name = item[1:] if descending else item
        definition = FIELDS.get(name)
        if definition is None or not definition.sortable:
            raise InvalidInputError(f"No se puede ordenar por {name!r}")
        keys.append(SortKey(name, descending))
    if len(keys) > 5:
        raise InvalidInputError("Máximo 5 criterios de orden")
    return tuple(keys)


def catalog() -> list[dict[str, Any]]:
    """Describe every field, its type, operators and allowed values (for UIs)."""

    return [
        {
            "field": definition.name,
            "label": definition.label,
            "type": definition.type,
            "operators": list(OPS_BY_TYPE[definition.type]),
            "values": list(definition.values),
            "sortable": definition.sortable,
        }
        for definition in FIELDS.values()
    ]


@dataclass(frozen=True)
class SearchSpec:
    """A validated search request."""

    group: Group
    q: str | None
    active_on: date
    validity: str | None
    state: str | None
    needs_review: bool | None
    has_issues: bool | None
    has_conflicts: bool | None
    effective_only: bool
    include_voided: bool
    sort: tuple[SortKey, ...]
    page: int
    size: int
    has_attachments: bool | None = None
