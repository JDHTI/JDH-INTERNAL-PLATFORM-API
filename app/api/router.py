"""Top-level API router wiring."""

from fastapi import APIRouter

from app.modules.health.presentation.router import router as health_router
from app.v1_0.v1_router import v1_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health")
api_router.include_router(v1_router)
