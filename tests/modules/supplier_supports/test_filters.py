"""The dynamic filter language only lets through known fields, operators and values."""

from datetime import date
from decimal import Decimal

import pytest
from app.core.errors import InvalidInputError
from app.modules.supplier_supports.domain import filters


def test_valid_tree_is_coerced_to_field_types() -> None:
    group = filters.parse_group(
        {
            "match": "all",
            "conditions": [
                {"field": "supplier.code", "op": "in", "value": ["233", "399"]},
                {"field": "supplier_value", "op": "between", "value": [0.05, "0.2"]},
                {"field": "valid_to", "op": "gte", "value": "2026-10-09"},
                {
                    "match": "any",
                    "conditions": [
                        {"field": "channels", "op": "overlaps", "value": ["HOTELES"]},
                        {"field": "channels", "op": "is_empty"},
                    ],
                },
            ],
        }
    )

    codes, amount, valid_to, nested = group.items
    assert codes.value == ["233", "399"]
    assert amount.value == [Decimal("0.05"), Decimal("0.2")]
    assert valid_to.value == date(2026, 10, 9)
    assert isinstance(nested, filters.Group) and nested.match == "any"


def test_none_means_no_filter() -> None:
    assert filters.parse_group(None) == filters.Group("all", ())


@pytest.mark.parametrize(
    "raw",
    [
        {"field": "password_hash", "op": "eq", "value": "x"},  # not a listed field
        {"field": "program", "op": "gt", "value": "x"},  # operator not allowed for text
        {"field": "kind", "op": "eq", "value": "inventado"},  # not in the enum
        {"field": "supplier_value", "op": "eq", "value": "abc"},  # not a number
        {"field": "valid_from", "op": "eq", "value": "31/12/2026"},  # not ISO
        {"field": "supplier_value", "op": "between", "value": [2, 1]},  # reversed bounds
        {"field": "supplier.code", "op": "in", "value": []},  # empty list
        {"field": "program", "op": "eq"},  # missing value
        {"field": "product.is_active", "op": "eq", "value": "yes"},  # not a bool
        {"field": "issues.code", "op": "overlaps", "value": ["no_existe"]},
    ],
)
def test_invalid_conditions_are_rejected(raw) -> None:
    with pytest.raises(InvalidInputError):
        filters.parse_condition(raw)


def test_nesting_and_size_are_bounded() -> None:
    deep = {"match": "all", "conditions": []}
    for _ in range(filters.MAX_DEPTH + 1):
        deep = {"match": "all", "conditions": [deep]}
    with pytest.raises(InvalidInputError, match="niveles"):
        filters.parse_group(deep)

    leaf = {"field": "program", "op": "eq", "value": "x"}
    with pytest.raises(InvalidInputError, match="condiciones"):
        filters.parse_group({"conditions": [leaf] * (filters.MAX_CONDITIONS + 1)})


def test_customer_nit_is_normalized_like_the_warehouse_stores_it() -> None:
    condition = filters.parse_condition(
        {"field": "customer_nit", "op": "in", "value": ["800.116.299", "35518947"]}
    )

    assert condition.value == ["800116299", "35518947"]


def test_sort_keys_are_whitelisted() -> None:
    keys = filters.parse_sort(["-valid_from", "supplier.name"])

    assert [(key.field, key.descending) for key in keys] == [
        ("valid_from", True),
        ("supplier.name", False),
    ]
    for bad in (["customer.channel"], ["issues.code"], ["nope"], ["-id"]):
        with pytest.raises(InvalidInputError):
            filters.parse_sort(bad)


def test_catalog_describes_every_field_with_its_operators() -> None:
    catalog = {item["field"]: item for item in filters.catalog()}

    assert set(catalog) == set(filters.FIELDS)
    assert "between" in catalog["supplier_value"]["operators"]
    assert "overlaps" in catalog["regionals"]["operators"]
    assert "pct_costo" in catalog["calc_method"]["values"]
    assert catalog["customer.channel"]["sortable"] is False
