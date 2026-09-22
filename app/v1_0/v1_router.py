"""Explicit router registry for API version 1."""

from fastapi import APIRouter

v1_router = APIRouter(prefix="/v1", tags=["v1"])

# Register every versioned module explicitly here. Example:
# from app.modules.orders.presentation.router import router as orders_router
# v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
