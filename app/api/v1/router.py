"""Versioned API router. Add module routers here explicitly."""

from fastapi import APIRouter

v1_router = APIRouter(prefix="/v1", tags=["v1"])

# Example when a module is added:
# from app.modules.orders.presentation.router import router as orders_router
# v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
