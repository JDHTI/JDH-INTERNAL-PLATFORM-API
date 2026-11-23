"""The four permission keys of Costos, one per view (catalog ``app/modules/areas``)."""

from __future__ import annotations

ENTRIES = "analisis-entradas"
NOVELTIES = "novedades-compras"
INCREASES = "alzas-proveedor"
LEDGER = "acumulado-alzas"
MODULE_KEYS = (ENTRIES, NOVELTIES, INCREASES, LEDGER)

__all__ = ["ENTRIES", "INCREASES", "LEDGER", "MODULE_KEYS", "NOVELTIES"]
