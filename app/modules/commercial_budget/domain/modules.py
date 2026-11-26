"""The permission keys of Gestión comercial (catalog ``app/modules/areas``)."""

from __future__ import annotations

# The commercial budget by sales rep: ``read`` looks, ``manage`` fills the budget.
BUDGET = "presupuesto-comercial"
# The sales-rep master upload to the warehouse (dwh-platform).
VENDOR_MASTER = "maestro-vendedores"

__all__ = ["BUDGET", "VENDOR_MASTER"]
