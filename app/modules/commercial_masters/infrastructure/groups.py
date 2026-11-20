"""Business groups of the customer master, compared the way people write their names."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select

from app.modules.commercial_masters.infrastructure.models import CustomerModel


def group_label(value: str | None) -> str:
    """A group's name in Python as :func:`group_key` writes it in SQL: upper case, single
    spaces."""

    return " ".join((value or "").upper().split())


def group_key(column: Any) -> ColumnElement[str]:
    """A group's name in SQL: upper case, trimmed, single spaces."""

    return func.upper(func.regexp_replace(func.trim(column), r"\s+", " ", "g"))


def groups_of_nits(nits: Sequence[str]) -> Select[Any]:
    """The groups (:func:`group_key`) of the branches of these customers."""

    return select(group_key(CustomerModel.business_group)).where(
        CustomerModel.nit.in_(list(nits)), CustomerModel.business_group.is_not(None)
    )


def groups_of_salespeople(codes: Sequence[str]) -> Select[Any]:
    """The groups (:func:`group_key`) of the customers of these salespeople."""

    return select(group_key(CustomerModel.business_group)).where(
        func.trim(CustomerModel.salesperson_code).in_(list(codes)),
        CustomerModel.business_group.is_not(None),
    )


__all__ = ["group_key", "group_label", "groups_of_nits", "groups_of_salespeople"]
