"""Explicit router registry for API version 1."""

from fastapi import APIRouter

from app.modules.app_monitoring.presentation.router import board_router as monitoring_board_router
from app.modules.app_monitoring.presentation.router import router as app_monitoring_router
from app.modules.areas.presentation.permissions_router import router as permissions_router
from app.modules.areas.presentation.router import router as areas_router
from app.modules.commercial_masters.presentation.router import router as commercial_router
from app.modules.consumption.presentation.router import router as consumption_router
from app.modules.directory.presentation.admin_router import router as directory_admin_router
from app.modules.directory.presentation.router import router as directory_router
from app.modules.documents.presentation.router import area_router as area_documents_router
from app.modules.documents.presentation.router import router as documents_router
from app.modules.dwh_monitoring.presentation.router import router as dwh_monitoring_router
from app.modules.dwh_publish.presentation.router import router as dwh_publish_router
from app.modules.identity.presentation.router import router as identity_router
from app.modules.jobs.presentation.router import router as jobs_router
from app.modules.masters.presentation.router import router as masters_router
from app.modules.notifications.presentation.router import (
    ingest_router as notifications_ingest_router,
)
from app.modules.notifications.presentation.router import router as notifications_router
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
from app.modules.quotations.presentation.board_router import router as main_board_router
from app.modules.quotations.presentation.list_router import (
    router as price_list_updates_router,
)
from app.modules.quotations.presentation.router import router as quotations_router
from app.modules.salespeople.presentation.router import router as salespeople_router
from app.modules.sites.presentation.router import router as sites_router
from app.modules.supplier_supports.presentation.attachments_router import (
    router as supports_attachments_router,
)
from app.modules.supplier_supports.presentation.bulk_router import router as supports_bulk_router
from app.modules.supplier_supports.presentation.profiles_router import (
    router as support_profiles_router,
)
from app.modules.supplier_supports.presentation.requests_router import (
    router as support_requests_router,
)
from app.modules.supplier_supports.presentation.router import router as supports_router
from app.modules.supplier_supports.presentation.special_cases_router import (
    router as special_cases_router,
)

v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_router.include_router(identity_router, prefix="", tags=["identity"])
v1_router.include_router(consumption_router, prefix="", tags=["consumption"])
v1_router.include_router(areas_router, prefix="/areas", tags=["areas"])
v1_router.include_router(permissions_router, prefix="/permissions", tags=["permissions"])
v1_router.include_router(salespeople_router, prefix="/salespeople", tags=["salespeople"])
v1_router.include_router(area_documents_router, prefix="/areas", tags=["documents"])
v1_router.include_router(documents_router, prefix="/documents", tags=["documents"])
v1_router.include_router(area_projects_router, prefix="/areas", tags=["projects"])
v1_router.include_router(sites_router, prefix="/sites", tags=["sites"])
v1_router.include_router(directory_router, prefix="/directory", tags=["directory"])
v1_router.include_router(
    directory_admin_router, prefix="/directory/admin", tags=["directory-admin"]
)
# The ingestion goes first: its fixed paths must win over /{notification_id}.
v1_router.include_router(
    notifications_ingest_router, prefix="/notifications/ingest", tags=["notifications-ingest"]
)
v1_router.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
v1_router.include_router(masters_router, prefix="/masters", tags=["masters"])
v1_router.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
v1_router.include_router(dwh_publish_router, tags=["dwh-publish"])
v1_router.include_router(commercial_router, prefix="/commercial", tags=["commercial-masters"])
# The bulk router goes first: its fixed paths (/form-schema) must win over /{support_id}.
v1_router.include_router(
    supports_bulk_router, prefix="/supplier-supports", tags=["supplier-supports"]
)
v1_router.include_router(
    supports_attachments_router, prefix="/supplier-supports", tags=["supplier-supports"]
)
v1_router.include_router(support_requests_router, tags=["supplier-support-requests"])
v1_router.include_router(supports_router, prefix="/supplier-supports", tags=["supplier-supports"])
v1_router.include_router(
    support_profiles_router,
    prefix="/supplier-support-profiles",
    tags=["supplier-support-profiles"],
)
v1_router.include_router(
    special_cases_router, prefix="/supplier-special-cases", tags=["supplier-special-cases"]
)
v1_router.include_router(
    order_validation_router, prefix="/order-validations", tags=["order-validations"]
)
v1_router.include_router(quotations_router, prefix="/quotations", tags=["quotations"])
# The board goes first: its fixed paths (/main/...) must win over /{quotation_id}/....
v1_router.include_router(
    main_board_router, prefix="/price-list-updates", tags=["price-list-updates"]
)
v1_router.include_router(
    price_list_updates_router, prefix="/price-list-updates", tags=["price-list-updates"]
)
v1_router.include_router(dwh_monitoring_router, prefix="/dwh-monitoring", tags=["dwh-monitoring"])
v1_router.include_router(app_monitoring_router, prefix="/app-monitoring", tags=["app-monitoring"])
v1_router.include_router(monitoring_board_router, prefix="/monitoring", tags=["monitoring"])
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
