"""Explicit router registry for API version 1."""

from fastapi import APIRouter

from app.modules.areas.presentation.router import router as areas_router
from app.modules.documents.presentation.router import area_router as area_documents_router
from app.modules.documents.presentation.router import router as documents_router
from app.modules.identity.presentation.router import router as me_router

v1_router = APIRouter(prefix="/v1", tags=["v1"])

v1_router.include_router(me_router, prefix="/me", tags=["identity"])
v1_router.include_router(areas_router, prefix="/areas", tags=["areas"])
v1_router.include_router(area_documents_router, prefix="/areas", tags=["documents"])
v1_router.include_router(documents_router, prefix="/documents", tags=["documents"])
