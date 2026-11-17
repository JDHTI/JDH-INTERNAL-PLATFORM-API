"""Export the JDH employee price list (MDE1 cost at the Margen Empleado JDH) to files.

Writes ``<out>.json`` (the same body as ``GET /commercial/products/employee-prices``) and
``<out>.csv`` (``;``-separated, one line per SKU). Reads the products from the platform and
the MDE1 costs live from the data warehouse; it writes nothing to either.

Usage:
    poetry run python scripts/export-employee-prices.py precios-empleados
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.app_container import (
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings
from app.core.errors import ApplicationError
from app.modules.commercial_masters.domain.employee_prices import EmployeePriceList

_CSV_COLUMNS = (
    "sku",
    "description",
    "brand",
    "inventory_unit",
    "cost",
    "margin",
    "price",
    "vat_pct",
    "price_with_vat",
)


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def write(result: EmployeePriceList, out: Path) -> tuple[Path, Path]:
    """Write the list as JSON and CSV next to each other."""

    body = {
        "warehouse": result.warehouse,
        "generated_at": result.generated_at.isoformat(),
        "snapshot_date": result.snapshot_date.isoformat() if result.snapshot_date else None,
        "total": len(result.items),
        "without_cost": result.without_cost,
        "items": [
            {name: _plain(value) for name, value in asdict(item).items()} for item in result.items
        ],
    }
    json_path = out.with_suffix(".json")
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    csv_path = out.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(_CSV_COLUMNS)
        for item in body["items"]:
            writer.writerow([item[name] for name in _CSV_COLUMNS])
    return json_path, csv_path


async def run(out: Path) -> int:
    """Build the list and write it."""

    container = build_application_container(Settings())
    resources = await initialize_global_resources(container)
    try:
        result = await container.v1_container.employee_price_service().price_list()
    except ApplicationError as exc:
        print(f"Error: {getattr(exc, 'detail', None) or exc}", file=sys.stderr)
        return 1
    finally:
        await shutdown_global_resources(resources)
    json_path, csv_path = write(result, out)
    print(f"Lista de precios empleados JDH (bodega {result.warehouse})")
    print(f"  foto de inventario ...... {result.snapshot_date}")
    print(f"  productos con precio .... {len(result.items)}")
    print(f"  activos sin costo MDE1 .. {result.without_cost}")
    print(f"  {json_path}")
    print(f"  {csv_path}")
    return 0


def main() -> int:
    """Parse arguments and run."""

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path, help="Path of the files, without extension")
    args = parser.parse_args()
    return asyncio.run(run(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
