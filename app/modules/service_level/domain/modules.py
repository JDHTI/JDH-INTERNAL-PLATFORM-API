"""The two permission keys of Nivel de servicio, one per view (catalog ``app/modules/areas``)."""

from __future__ import annotations

# The pending lines of the fulfilled orders: read sees them, manage gives reasons, keeps the
# reasons' catalog and recalculates the ABCD classification.
PENDING_ORDERS = "pendientes-pedidos"
# The dashboard by operation center: read only.
SERVICE_LEVEL = "nivel-servicio"
MODULE_KEYS = (PENDING_ORDERS, SERVICE_LEVEL)

__all__ = ["MODULE_KEYS", "PENDING_ORDERS", "SERVICE_LEVEL"]
