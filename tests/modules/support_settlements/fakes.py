"""Builders and doubles of the support settlement."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.errors import ConflictError, NotFoundError
from app.modules.support_settlements.domain.entities import (
    CLOSED,
    DRAFT,
    SETTLED,
    CustomerBranch,
    Product,
    Quote,
    SaleLine,
    Settlement,
    SettlementResult,
    SettlementSupport,
)

D = Decimal
SEPTEMBER = date(2026, 9, 10)


def support(**values) -> SettlementSupport:
    base = {
        "id": uuid4(),
        "version": 1,
        "supplier_code": "233",
        "supplier_name": "FAMILIA SANCELA",
        "kind": "tactico_cliente",
        "calc_method": "costo_neto",
        "status": "activo",
        "customer_nit": "900973779",
        "product_sku": "18369",
        "supplier_value": D("79266.92"),
        "value_unit": "caja",
        "pack_factor": D("9"),
        "valid_from": date(2026, 1, 1),
        "valid_to": date(2027, 1, 30),
    }
    return SettlementSupport(**{**base, **values})


def sale(**values) -> SaleLine:
    base = {
        "line_key": uuid4().hex,
        "sale_date": SEPTEMBER,
        "sku": "18369",
        "quantity": D("45"),
        "gross": D("470000"),
        "net": D("470000"),
        "cost": D("443355"),
        "document": "FEV-17614",
        "co": "001",
        "regional": "ANTIOQUIA",
        "customer_key": "900973779-001",
        "nit": "900973779",
        "branch_id": "001",
        "warehouse_code": "MDE1",
        "warehouse_name": "PRINCIPAL MEDELLIN",
    }
    return SaleLine(**{**base, **values})


def customer(**values) -> CustomerBranch:
    base = {
        "customer_key": "900973779-001",
        "nit": "900973779",
        "branch_id": "001",
        "name": "Foodie Sas",
        "channel": "RESTAURANTES",
        "business_unit": "FOOD SERVICE",
        "billing_center": "ANTIOQUIA",
        "billing_center_code": "001",
        "city": "Medellín",
    }
    return CustomerBranch(**{**base, **values})


def product(**values) -> Product:
    base = {
        "sku": "18369",
        "producto_key": "P18369",
        "description": "Serv. Caf Plus",
        "supplier_code": "233",
        "pack_factor": D("9"),
        "inventory_unit": "UNID",
        "margins": {"margin_list": D("0.16")},
    }
    return Product(**{**base, **values})


def quote(cost: str, sku: str = "18369", since: date = date(2026, 1, 13), **values) -> Quote:
    return Quote(sku=sku, since=since, cost=D(cost), **values)


class FakeMasters:
    def __init__(self, supports=(), products=(), customers=()) -> None:
        self.items = list(supports)
        self.products_by_sku = {item.sku: item for item in products}
        self.branches = {item.customer_key: item for item in customers}

    async def supports(self, start, following):
        return [
            item
            for item in self.items
            if (item.valid_from is None or item.valid_from < following)
            and (item.valid_to is None or item.valid_to >= start)
        ]

    async def products(self, supplier_codes, skus):
        return {
            sku: item
            for sku, item in self.products_by_sku.items()
            if item.supplier_code in supplier_codes or sku in skus
        }

    async def customers(self, keys):
        return {key: self.branches[key] for key in keys if key in self.branches}


class FakeSales:
    def __init__(self, lines=(), quotes=None) -> None:
        self.lines = list(lines)
        self.quote_rows = quotes or {}
        self.calls = 0

    async def sales(self, start, following, product_keys):
        self.calls += 1
        return [line for line in self.lines if start <= line.sale_date < following]

    async def quotes(self, start, following, product_keys):
        return self.quote_rows


class FakeRepository:
    """Settlements by ``(period, cost_basis)``."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], Settlement] = {}
        self.results: dict[tuple[str, str], SettlementResult] = {}

    async def get(self, period, cost_basis):
        return self.items.get((period, cost_basis))

    async def list(self):
        return sorted(self.items.values(), key=lambda item: item.period, reverse=True)

    async def replace(self, period, cost_basis, result, actor, now, *, close=False):
        key = (period, cost_basis)
        current = self.items.get(key)
        if current is not None and current.status == CLOSED:
            raise ConflictError("cerrada")
        amount = sum((sale.amount for sale in result.sales), D(0))
        flagged = sum(
            (item.amount for sale in result.sales for item in sale.items if item.status != SETTLED),
            D(0),
        )
        stored = Settlement(
            id=current.id if current else uuid4(),
            period=period,
            status=CLOSED if close else DRAFT,
            amount=amount,
            flagged_amount=flagged,
            net=sum((ref.net for ref in result.references), D(0)),
            cost=sum((ref.cost for ref in result.references), D(0)),
            sales_read=result.sales_read,
            supported_lines=len(result.sales),
            suppliers=len({ref.supplier_code for ref in result.references}),
            calculated_at=now,
            calculated_by=actor,
            closed_at=now if close else None,
            closed_by=actor if close else None,
            cost_basis=cost_basis,
        )
        self.items[key] = stored
        self.results[key] = result
        return stored

    async def reopen(self, period, cost_basis, actor, now):
        key = (period, cost_basis)
        current = self.items.get(key)
        if current is None:
            raise NotFoundError("no")
        if current.status != CLOSED:
            raise ConflictError("no cerrada")
        self.items[key] = replace(current, status=DRAFT, reopened_at=now, reopened_by=actor)
        return self.items[key]

    def _settlement_period(self, settlement_id: UUID) -> tuple[str, str]:
        return next(key for key, item in self.items.items() if item.id == settlement_id)

    async def suppliers(self, settlement_id):
        result = self.results[self._settlement_period(settlement_id)]
        rows: dict[str, dict] = defaultdict(
            lambda: {
                "references": 0,
                "supported_lines": 0,
                "quantity": D(0),
                "net": D(0),
                "cost": D(0),
                "amount": D(0),
                "flagged_amount": D(0),
            }
        )
        for ref in result.references:
            row = rows[ref.supplier_code]
            row.update(supplier_code=ref.supplier_code, supplier_name=ref.supplier_name)
            row["references"] += 1
            row["supported_lines"] += ref.supported_lines
            for name in ("quantity", "net", "cost", "amount", "flagged_amount"):
                row[name] += getattr(ref, name)
        return list(rows.values())

    async def references(self, settlement_id, supplier_code):
        result = self.results[self._settlement_period(settlement_id)]
        return [
            {
                "supplier_code": ref.supplier_code,
                "supplier_name": ref.supplier_name,
                "sku": ref.sku,
                "description": ref.description,
                "quantity": ref.quantity,
                "net": ref.net,
                "cost": ref.cost,
                "amount": ref.amount,
                "flagged_amount": ref.flagged_amount,
                "supported_lines": ref.supported_lines,
            }
            for ref in result.references
            if supplier_code is None or ref.supplier_code == supplier_code
        ]

    async def lines(self, settlement_id, *, supplier_code, sku, nit, status, page, size):
        result = self.results[self._settlement_period(settlement_id)]
        rows = [self._row(sale) for sale in result.sales]
        return rows[(page - 1) * size : page * size], len(rows)

    async def breakdown(self, settlement_id, by, supplier_code):
        return []

    async def export_rows(self, settlement_id, supplier_code):
        result = self.results[self._settlement_period(settlement_id)]
        return [self._row(sale) for sale in result.sales], await self.references(
            settlement_id, supplier_code
        )

    @staticmethod
    def _row(sale) -> dict:
        line, branch = sale.line, sale.customer
        return {
            "id": uuid4(),
            "line_key": line.line_key,
            "sale_date": line.sale_date,
            "document": line.document,
            "co": line.co,
            "co_name": line.co_name,
            "regional": line.regional,
            "business_unit": branch.business_unit if branch else None,
            "channel": branch.channel if branch else None,
            "city": branch.city if branch else None,
            "salesperson": line.salesperson,
            "nit": line.nit,
            "branch_id": line.branch_id,
            "customer_name": branch.name if branch else None,
            "business_group": branch.business_group if branch else None,
            "supplier_code": sale.supplier_code,
            "supplier_name": sale.supplier_name,
            "sku": line.sku,
            "description": sale.description,
            "warehouse": line.warehouse_name,
            "is_return": line.is_return,
            "reason": line.reason,
            "quantity": line.quantity,
            "gross": line.gross,
            "net": line.net,
            "cost": line.cost,
            "base_unit_cost": sale.base_unit_cost,
            "cost_source": sale.cost_source,
            "amount": sale.amount,
            "flags": list(sale.flags),
            "items": [
                {
                    "support_id": item.support_id,
                    "support_version": item.support_version,
                    "kind": item.kind,
                    "calc_method": item.calc_method,
                    "status": item.status,
                    "supplier_value": item.supplier_value,
                    "value_unit": item.value_unit,
                    "valid_from": item.valid_from,
                    "valid_to": item.valid_to,
                    "negotiated_pct": item.negotiated_pct,
                    "settled_pct": item.settled_pct,
                    "unit_amount": item.unit_amount,
                    "amount": item.amount,
                    "detail": item.detail,
                }
                for item in sale.items
            ],
        }


class Policy:
    def __init__(self, managers=(), readers=()) -> None:
        self.managers = set(managers)
        self.readers = set(readers)

    async def is_manager(self, principal) -> bool:
        return principal.is_platform_admin or principal.email in self.managers

    async def can_read(self, principal) -> bool:
        return await self.is_manager(principal) or principal.email in self.readers


NOW = datetime(2026, 11, 24, 15, 0)
