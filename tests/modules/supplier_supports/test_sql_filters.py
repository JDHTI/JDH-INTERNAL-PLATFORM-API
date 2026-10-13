"""The filter tree compiles to parameterized PostgreSQL; no value is ever inlined."""

from datetime import date
from decimal import Decimal

import pytest
from app.modules.supplier_supports.domain import filters
from app.modules.supplier_supports.infrastructure import repository as repo
from sqlalchemy import select
from sqlalchemy.dialects import postgresql


def sql(group: dict | None) -> tuple[str, dict]:
    statement = repo._base().where(repo._compile(filters.parse_group(group)))
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def test_every_filterable_field_has_a_column() -> None:
    for name, definition in filters.FIELDS.items():
        if name == "issues.code":
            continue
        known = (
            name in repo._SUPPORT_COLUMNS
            or name in repo._JOINED_COLUMNS
            or name in repo._CUSTOMER_COLUMNS
        )
        assert known, f"{name} ({definition.type}) is declared but not mapped"


def test_every_sortable_field_has_a_sort_column() -> None:
    sortable = {name for name, item in filters.FIELDS.items() if item.sortable}

    assert sortable == set(repo._SORT_COLUMNS)


def test_text_comparison_is_case_insensitive_and_parameterized() -> None:
    text, params = sql(
        {
            "conditions": [
                {"field": "supplier.name", "op": "eq", "value": "Familia'; DROP TABLE x;--"}
            ]
        }
    )

    assert "lower(coalesce(commercial_suppliers.name" in text
    assert "DROP TABLE" not in text
    assert "familia'; drop table x;--" in params.values()


def test_range_and_array_operators() -> None:
    text, params = sql(
        {
            "conditions": [
                {"field": "supplier_value", "op": "between", "value": [0.05, 0.2]},
                {"field": "regionals", "op": "overlaps", "value": ["ANTIOQUIA", "CENTRO"]},
                {"field": "channels", "op": "is_empty"},
                {"field": "valid_to", "op": "gte", "value": "2026-10-09"},
            ]
        }
    )

    assert "BETWEEN" in text and "&&" in text and "cardinality" in text
    assert Decimal("0.05") in params.values() and date(2026, 10, 9) in params.values()


def test_any_joins_with_or_and_nested_groups_keep_their_precedence() -> None:
    text, _ = sql(
        {
            "match": "any",
            "conditions": [
                {"field": "kind", "op": "eq", "value": "rebate"},
                {
                    "match": "all",
                    "conditions": [
                        {"field": "brand", "op": "eq", "value": "corona"},
                        {"field": "status", "op": "neq", "value": "anulado"},
                    ],
                },
            ],
        }
    )

    assert " OR " in text and " AND " in text


def test_customer_fields_filter_through_the_customer_master_by_nit() -> None:
    text, _ = sql(
        {"conditions": [{"field": "customer.channel", "op": "in", "value": ["RESTAURANTES"]}]}
    )

    assert "EXISTS" in text and "customers.nit = supplier_supports.customer_nit" in text


def test_issue_codes_use_jsonb_containment() -> None:
    text, params = sql(
        {"conditions": [{"field": "issues.code", "op": "overlaps", "value": ["sku_sin_cruce"]}]}
    )

    assert "@>" in text
    assert [{"code": "sku_sin_cruce"}] in params.values()


def test_empty_tree_matches_everything() -> None:
    text, _ = sql(None)

    assert "WHERE true" in text or "WHERE 1 = 1" in text or "WHERE" not in text.split("FROM")[-1]


@pytest.mark.parametrize("descending", [True, False])
def test_default_and_custom_ordering_always_end_with_the_id(descending) -> None:
    keys = filters.parse_sort([("-" if descending else "") + "supplier.name"])

    text = str(select(repo.S).order_by(*repo._order(keys)).compile(dialect=postgresql.dialect()))

    assert text.rstrip().endswith("supplier_supports.id")
    assert ("DESC" in text) is descending
    assert "NULLS LAST" in text


def test_conflicts_compare_supplier_sku_and_warehouse_and_skip_undated_or_unscoped_rows() -> None:
    from sqlalchemy.orm import aliased

    other = aliased(repo.SupplierSupportModel)
    text = str(
        select(repo.S.id).where(repo._conflict_clause(other)).compile(dialect=postgresql.dialect())
    )

    assert text.count("supplier_sku") >= 2 and text.count("warehouse") >= 2  # compared in the key
    assert "valid_from IS NOT NULL" in text and "customer_nit IS NOT NULL" in text
