"""Explicit router registry for API version 1."""

from fastapi import APIRouter

from app.modules.areas.presentation.router import router as areas_router
from app.modules.commercial_masters.presentation.router import router as commercial_router
from app.modules.consumption.presentation.router import router as consumption_router
from app.modules.documents.presentation.router import area_router as area_documents_router
from app.modules.documents.presentation.router import router as documents_router
from app.modules.dwh_monitoring.presentation.router import router as dwh_monitoring_router
from app.modules.identity.presentation.router import router as identity_router
from app.modules.masters.presentation.router import router as masters_router
from app.modules.order_validation.presentation.router import router as order_validation_router
from app.modules.pqrsf.presentation.certificates_router import router as pqrsf_certificates_router
from app.modules.pqrsf.presentation.classification_router import (
    router as pqrsf_classification_router,
)
from app.modules.pqrsf.presentation.deliveries_router import router as pqrsf_deliveries_router
from app.modules.pqrsf.presentation.feedback_router import router as pqrsf_feedback_router
from app.modules.pqrsf.presentation.item_delivery_router import router as pqrsf_item_delivery_router
from app.modules.pqrsf.presentation.router import admin_router as pqrsf_admin_router
from app.modules.pqrsf.presentation.router import public_router as pqrsf_public_router
from app.modules.pqrsf.presentation.supplier_messages_router import (
    router as pqrsf_supplier_messages_router,
)
from app.modules.pqrsf.presentation.tracking_router import router as pqrsf_tracking_router
from app.modules.projects.presentation.router import area_router as area_projects_router
from app.modules.sites.presentation.router import router as sites_router
from app.modules.supplier_supports.presentation.router import router as supports_router
from app.modules.supplier_supports.presentation.special_cases_router import (
    router as special_cases_router,
)

v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_router.include_router(identity_router, prefix="", tags=["identity"])
v1_router.include_router(consumption_router, prefix="", tags=["consumption"])
v1_router.include_router(areas_router, prefix="/areas", tags=["areas"])
v1_router.include_router(area_documents_router, prefix="/areas", tags=["documents"])
v1_router.include_router(documents_router, prefix="/documents", tags=["documents"])
v1_router.include_router(area_projects_router, prefix="/areas", tags=["projects"])
v1_router.include_router(sites_router, prefix="/sites", tags=["sites"])
v1_router.include_router(masters_router, prefix="/masters", tags=["masters"])
v1_router.include_router(commercial_router, prefix="/commercial", tags=["commercial-masters"])
v1_router.include_router(supports_router, prefix="/supplier-supports", tags=["supplier-supports"])
v1_router.include_router(
    special_cases_router, prefix="/supplier-special-cases", tags=["supplier-special-cases"]
)
v1_router.include_router(
    order_validation_router, prefix="/order-validations", tags=["order-validations"]
)
v1_router.include_router(dwh_monitoring_router, prefix="/dwh-monitoring", tags=["dwh-monitoring"])
v1_router.include_router(pqrsf_public_router, tags=["pqrsf-public"])
v1_router.include_router(pqrsf_admin_router, tags=["pqrsf"])
v1_router.include_router(pqrsf_tracking_router, tags=["pqrsf-tracking"])
v1_router.include_router(pqrsf_feedback_router, tags=["pqrsf-feedback"])
v1_router.include_router(pqrsf_certificates_router, tags=["pqrsf-certificates"])
v1_router.include_router(pqrsf_deliveries_router, tags=["pqrsf-deliveries"])
v1_router.include_router(pqrsf_item_delivery_router, tags=["pqrsf-tracking"])
v1_router.include_router(pqrsf_classification_router, tags=["pqrsf-classification"])
v1_router.include_router(pqrsf_supplier_messages_router, tags=["pqrsf-tracking"])

# Register every versioned module explicitly here. Example:
# from app.modules.orders.presentation.router import router as orders_router
# v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
