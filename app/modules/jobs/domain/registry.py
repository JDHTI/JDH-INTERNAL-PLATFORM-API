"""The background jobs of the platform and the warehouse sequence that feeds each one.

The DWH publishes its loads as *sequences* (``monitoreo.secuencias``). When a
sequence finishes with its views re-published (``ultima_datos_listos``) the
``consumo`` data it owns is fresh, and the jobs mapped to it run. Schedule measured
by the DWH team on 2026-11-05 (Bogotá, median data ready):

- ``identidad``, every day 03:00, ready ~03:42: customers, suppliers, salespeople and
  the other identity masters.
- ``reporte_ventas``, Mon-Sat 07:30, ready ~07:56: products and sales.
- ``abastecimiento``, Mon-Fri 08:30, ready ~08:56: price lists, inventory and the
  product-supplier bridge.

A job key is a URL segment (``POST /jobs/{key}/run``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from enum import StrEnum

KEY_PATTERN = re.compile(r"^[a-z0-9-]{1,60}$")
EVERY_DAY = frozenset(range(1, 8))
MONDAY_TO_SATURDAY = frozenset(range(1, 7))
MONDAY_TO_FRIDAY = frozenset(range(1, 6))


class Trigger(StrEnum):
    """What started a run."""

    DWH = "dwh"
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    STARTUP = "startup"


class RunStatus(StrEnum):
    """Where a run is."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    # It ran, and some of its parts failed (a master without read permission).
    PARTIAL = "partial"
    FAILED = "failed"


OPEN_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)
DONE_STATUSES = (RunStatus.SUCCESS, RunStatus.PARTIAL)


@dataclass(frozen=True)
class JobDefinition:
    """A job the platform runs in the background."""

    key: str
    label: str
    description: str
    # DWH sequence whose fresh data starts it; ``None`` runs at ``at`` or only by hand.
    sequence: str | None
    # ISO weekdays (1 = Monday) when the daily fallback runs it if the DWH did not (or, with
    # ``at``, the days it runs at that time).
    fallback_days: frozenset[int]
    # Local time it runs every day, whatever the DWH does (2026-11-20); a start-up after
    # that time runs it if it has not run since.
    at: time | None = None


JOBS: tuple[JobDefinition, ...] = (
    JobDefinition(
        "masters-warm-identity",
        "Caché de maestros de identidad",
        "Lee del DWH clientes, proveedores, vendedores y demás maestros de identidad",
        "identidad",
        EVERY_DAY,
    ),
    JobDefinition(
        "commercial-sync-identity",
        "Maestros comerciales: clientes y proveedores",
        "Tablas del cotizador, el validador y los apoyos",
        "identidad",
        EVERY_DAY,
    ),
    JobDefinition(
        "masters-warm-products",
        "Caché del maestro de productos",
        "Lee del DWH el maestro de productos",
        "reporte_ventas",
        MONDAY_TO_SATURDAY,
    ),
    JobDefinition(
        "commercial-sync-products",
        "Maestros comerciales: productos",
        "Productos y proveedores comerciales del cotizador y los apoyos",
        "reporte_ventas",
        MONDAY_TO_SATURDAY,
    ),
    JobDefinition(
        "commercial-sync-supply",
        "Maestros comerciales: costos y compras",
        "Costo unitario promedio y el puente producto-proveedor",
        "abastecimiento",
        MONDAY_TO_FRIDAY,
    ),
    JobDefinition(
        "supplier-supports-costs",
        "Apoyos: costo del DWH",
        "Costo de cada apoyo con la regla del cotizador (el mayor entre cotización e inventario)",
        "abastecimiento",
        MONDAY_TO_FRIDAY,
    ),
    # Right after midnight, when a programmed support starts: it replaces the previous one.
    # It reads no DWH data, so it does not wait for a sequence (2026-11-20).
    JobDefinition(
        "supplier-supports-rollover",
        "Apoyos: programados reemplazan al anterior",
        "Vence el día anterior el apoyo que un apoyo programado de la misma clase, cliente y "
        "referencia reemplaza, para que nunca se crucen. Corre a las 00:05",
        None,
        EVERY_DAY,
        at=time(0, 5),
    ),
    # A customer quotation left as a draft and never sent to validation for over 30 days is
    # deleted (2026-11-23). It reads no DWH data.
    JobDefinition(
        "quotations-stale-drafts",
        "Cotizador: borrar borradores sin enviar",
        "Borra por completo las cotizaciones de cliente en borrador que nunca se enviaron a "
        "validación y tienen más de 30 días de creadas. Corre a las 00:15",
        None,
        EVERY_DAY,
        at=time(0, 15),
    ),
    # After the supply load: the price lists and the inventory are the day's.
    JobDefinition(
        "main-lists-sync",
        "Listas principales: sincronizar",
        "Arma o refresca las 14 listas principales con las listas, el inventario y las ventas "
        "del día; conserva precios nuevos, estados y observaciones",
        "abastecimiento",
        MONDAY_TO_FRIDAY,
    ),
    # The purchase entries and quotations come with the supply load (2026-11-23).
    JobDefinition(
        "costs-novelties-sync",
        "Costos: novedades de las entradas",
        "Copia las entradas de compra de los últimos 45 días que no cuadran con la cotización "
        "vigente, refresca las que ya estaban y marca anuladas las que SIESA ya no tiene",
        "abastecimiento",
        MONDAY_TO_FRIDAY,
    ),
    # After the sales load (2026-11-24): the open months of the settlement are recalculated;
    # a closed month never changes.
    JobDefinition(
        "support-settlements-refresh",
        "Apoyos: liquidación de los meses abiertos",
        "Recalcula la liquidación de apoyos del mes en curso y del mes vencido si no está "
        "cerrado, con las ventas y los apoyos del día",
        "reporte_ventas",
        MONDAY_TO_SATURDAY,
    ),
    # After the sales load (2026-11-26): the commercial budget's months of sales and the rep
    # master (refreshed earlier by ``identidad``); a closed month keeps its own picture.
    JobDefinition(
        "commercial-budget-sales",
        "Presupuesto comercial: ventas por cliente",
        "Copia del DWH el maestro de vendedores y las ventas por cliente del mes en curso, del "
        "mes anterior si no está cerrado y de los meses de historia que falten",
        "reporte_ventas",
        MONDAY_TO_SATURDAY,
    ),
)
JOBS_BY_KEY = {job.key: job for job in JOBS}


def jobs_for(sequence: str) -> list[JobDefinition]:
    """The jobs that a sequence's fresh data starts, in registry order."""

    return [job for job in JOBS if job.sequence == sequence]


def timed_jobs() -> list[JobDefinition]:
    """The jobs that run at a fixed local time, in registry order."""

    return [job for job in JOBS if job.at is not None]


def sequences() -> list[str]:
    """Every DWH sequence some job waits for, in registry order."""

    seen: list[str] = []
    for job in JOBS:
        if job.sequence and job.sequence not in seen:
            seen.append(job.sequence)
    return seen
