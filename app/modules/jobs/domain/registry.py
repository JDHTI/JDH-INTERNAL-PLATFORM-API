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
    # DWH sequence whose fresh data starts it; ``None`` runs only by hand.
    sequence: str | None
    # ISO weekdays (1 = Monday) when the daily fallback runs it if the DWH did not.
    fallback_days: frozenset[int]


IDENTITY_MASTERS = (
    "terceros",
    "clientes",
    "proveedores",
    "vendedores",
    "supervisores",
    "equipos",
    "centros-operacion",
    "centros-costo",
    "unidades-negocio",
    "regionales",
)

JOBS: tuple[JobDefinition, ...] = (
    JobDefinition(
        "masters-sync",
        "Maestros de identidad",
        "Clientes, proveedores, vendedores y demás maestros del catálogo",
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
        "masters-sync-products",
        "Maestro de productos",
        "El maestro de productos del catálogo",
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
)
JOBS_BY_KEY = {job.key: job for job in JOBS}


def jobs_for(sequence: str) -> list[JobDefinition]:
    """The jobs that a sequence's fresh data starts, in registry order."""

    return [job for job in JOBS if job.sequence == sequence]


def sequences() -> list[str]:
    """Every DWH sequence some job waits for, in registry order."""

    seen: list[str] = []
    for job in JOBS:
        if job.sequence and job.sequence not in seen:
            seen.append(job.sequence)
    return seen
