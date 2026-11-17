"""Map warehouse ``consumo`` rows to the columns of the local master tables."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.modules.commercial_masters.domain.entities import clean_nit

Row = Mapping[str, Any]

PLACEHOLDER_COMPANY = -1


def text(value: object) -> str | None:
    """Collapse whitespace; ``None`` when blank."""

    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def number(value: object) -> Decimal | None:
    """Decimal of a numeric value, ``None`` when blank or not a number."""

    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def is_active(*states: object) -> bool:
    """A record is active when every given state flag is ``A``."""

    return all(str(state or "").strip().upper() == "A" for state in states)


def supplier_row(row: Row) -> dict[str, Any] | None:
    """``consumo.proveedores`` row, or ``None`` when it has no usable NIT."""

    nit = clean_nit(row.get("nit"))
    if nit is None or not row.get("proveedor_key"):
        return None
    return {
        "proveedor_key": row["proveedor_key"],
        "company_id": int(row.get("cia") or 1),
        "nit": nit,
        "branch_id": text(row.get("id_sucursal")),
        "legal_name": text(row.get("razon_social")),
        "branch_name": text(row.get("sucursal")),
        "establishment_name": text(row.get("nombre_establecimiento")),
        "supplier_class": text(row.get("clase_proveedor")),
        "supplier_type": text(row.get("tipo_proveedor")),
        "city": text(row.get("ciudad")),
        "payment_condition": text(row.get("desc_condicion_pago")),
        "state": text(row.get("estado")),
        "branch_state": text(row.get("estado_sucursal")),
        "is_active": is_active(row.get("estado"), row.get("estado_sucursal")),
        "entry_date": row.get("fecha_ingreso"),
    }


def customer_row(row: Row) -> dict[str, Any] | None:
    """``consumo.clientes`` row; the ``cia = -1`` placeholder and NIT-less rows are skipped."""

    nit = clean_nit(row.get("nit"))
    if nit is None or not row.get("cliente_key"):
        return None
    company = int(row.get("cia") or 1)
    if company == PLACEHOLDER_COMPANY:
        return None
    return {
        "cliente_key": row["cliente_key"],
        "company_id": company,
        "nit": nit,
        "branch_id": text(row.get("id_sucursal")),
        "legal_name": text(row.get("razon_social")),
        "branch_name": text(row.get("sucursal")),
        "establishment_name": text(row.get("nombre_establecimiento")),
        "channel_code": text(row.get("cod_canal")),
        "channel": text(row.get("canal")),
        "business_unit_code": text(row.get("cod_unidad_negocio")),
        "business_unit": text(row.get("unidad_negocio")),
        "business_group_code": text(row.get("cod_grupo_empresarial")),
        "business_group": text(row.get("grupo_empresarial")),
        "billing_center_code": text(row.get("cod_co_factura")),
        "billing_center": text(row.get("co_factura")),
        "logistics_zone": text(row.get("zona_logistica")),
        "department": text(row.get("departamento")),
        "city": text(row.get("ciudad")),
        "salesperson_code": text(row.get("cod_vendedor")),
        "salesperson_name": text(row.get("nombre_vendedor")),
        "sales_team": text(row.get("equipo")),
        "sales_team_class": text(row.get("clase_equipo")),
        "state": text(row.get("estado")),
        "branch_state": text(row.get("estado_sucursal")),
        "is_active": is_active(row.get("estado"), row.get("estado_sucursal")),
    }


def product_row(row: Row) -> dict[str, Any] | None:
    """``consumo.productos`` row; rows without a SKU are skipped."""

    sku = text(row.get("sku"))
    if sku is None or not row.get("producto_key"):
        return None
    return {
        "producto_key": row["producto_key"],
        "sku": sku,
        "supplier_sku": text(row.get("sku_proveedor")),
        "description": text(row.get("descripcion")),
        "state": text(row.get("estado")),
        "is_active": is_active(row.get("estado")),
        "commercial_supplier_code": text(row.get("cod_proveedor")),
        "manufacturer": text(row.get("fabricante")),
        "brand": text(row.get("marca")),
        "category": text(row.get("categoria")),
        "subcategory": text(row.get("subcategoria")),
        "product_line": text(row.get("linea")),
        "product_subline": text(row.get("sublinea")),
        "product_group": text(row.get("grupo")),
        "pack_factor": number(row.get("factor_empaque")),
        "inventory_unit": text(row.get("unidad_inventario")),
        "sales_vat_pct": number(row.get("iva_venta_pct")),
        "main_barcode": text(row.get("codigo_barras_principal")),
    }


def product_supplier_row(row: Row) -> dict[str, Any] | None:
    """``consumo.producto_proveedor`` row."""

    if not row.get("producto_key") or not row.get("proveedor_key"):
        return None
    return {
        "producto_key": row["producto_key"],
        "proveedor_key": row["proveedor_key"],
        "first_purchase": row.get("primera_compra"),
        "last_purchase": row.get("ultima_compra"),
        "purchase_lines": int(row.get("lineas_compra") or 0),
    }


def unit_cost_row(row: Row) -> dict[str, Any] | None:
    """One SKU's current average unit cost; rows without a positive cost are skipped."""

    sku = text(row.get("sku"))
    cost = number(row.get("avg_unit_cost"))
    if sku is None or cost is None or cost <= 0:
        return None
    return {"sku": sku, "avg_unit_cost": round(cost, 4), "avg_unit_cost_date": row.get("cut_date")}


def commercial_supplier_row(row: Row) -> dict[str, Any] | None:
    """One ``(cod_proveedor, proveedor)`` label with its product counts."""

    code = text(row.get("cod_proveedor"))
    name = text(row.get("proveedor"))
    if code is None:
        return None
    total = int(row.get("productos") or 0)
    active = int(row.get("productos_activos") or 0)
    return {
        "code": code,
        "name": name or code,
        "is_active": active > 0,
        "product_count": total,
        "active_product_count": active,
    }
