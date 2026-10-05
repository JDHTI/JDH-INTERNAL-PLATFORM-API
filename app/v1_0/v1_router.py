"""Explicit router registry for API version 1."""

from fastapi import APIRouter

from app.modules.areas.presentation.router import router as areas_router
from app.modules.consumption.presentation.router import router as consumption_router
from app.modules.documents.presentation.router import area_router as area_documents_router
from app.modules.documents.presentation.router import router as documents_router
from app.modules.identity.presentation.router import router as identity_router
from app.modules.masters.presentation.router import router as masters_router
from app.modules.pqrsf.presentation.router import admin_router as pqrsf_admin_router
from app.modules.pqrsf.presentation.router import public_router as pqrsf_public_router
from app.modules.pqrsf.presentation.tracking_router import router as pqrsf_tracking_router
from app.modules.projects.presentation.router import area_router as area_projects_router
from app.modules.sites.presentation.router import router as sites_router

v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_router.include_router(identity_router, prefix="", tags=["identity"])
v1_router.include_router(consumption_router, prefix="", tags=["consumption"])
v1_router.include_router(areas_router, prefix="/areas", tags=["areas"])
v1_router.include_router(area_documents_router, prefix="/areas", tags=["documents"])
v1_router.include_router(documents_router, prefix="/documents", tags=["documents"])
v1_router.include_router(area_projects_router, prefix="/areas", tags=["projects"])
v1_router.include_router(sites_router, prefix="/sites", tags=["sites"])
v1_router.include_router(masters_router, prefix="/masters", tags=["masters"])
v1_router.include_router(pqrsf_public_router, tags=["pqrsf-public"])
v1_router.include_router(pqrsf_admin_router, tags=["pqrsf"])
v1_router.include_router(pqrsf_tracking_router, tags=["pqrsf-tracking"])

# Register every versioned module explicitly here. Example:
# from app.modules.orders.presentation.router import router as orders_router
# v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
