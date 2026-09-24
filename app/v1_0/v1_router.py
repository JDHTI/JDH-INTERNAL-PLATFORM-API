"""Explicit router registry for API version 1."""

from fastapi import APIRouter

from app.modules.areas.presentation.router import router as areas_router
from app.modules.consumption.presentation.router import router as consumption_router
from app.modules.documents.presentation.router import area_router as area_documents_router
from app.modules.documents.presentation.router import router as documents_router
from app.modules.identity.presentation.router import router as identity_router

v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_router.include_router(identity_router, prefix="", tags=["identity"])
v1_router.include_router(consumption_router, prefix="", tags=["consumption"])
v1_router.include_router(areas_router, prefix="/areas", tags=["areas"])
v1_router.include_router(area_documents_router, prefix="/areas", tags=["documents"])
v1_router.include_router(documents_router, prefix="/documents", tags=["documents"])

# Register every versioned module explicitly here. Example:
# from app.modules.orders.presentation.router import router as orders_router
# v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
