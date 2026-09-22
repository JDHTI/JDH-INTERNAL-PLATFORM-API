"""Explicit router registry for API version 1."""

from fastapi import APIRouter

from app.modules.consumption.presentation.router import router as consumption_router
from app.modules.identity.presentation.router import router as identity_router

v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_router.include_router(identity_router, prefix="", tags=["identity"])
v1_router.include_router(consumption_router, prefix="", tags=["consumption"])

# Register every versioned module explicitly here. Example:
# from app.modules.orders.presentation.router import router as orders_router
# v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
