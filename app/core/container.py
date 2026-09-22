"""Backward-compatible import path for the global application container."""

from app.app_container import (
    ApplicationContainer,
    GlobalResources,
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)

__all__ = [
    "ApplicationContainer",
    "GlobalResources",
    "build_application_container",
    "initialize_global_resources",
    "shutdown_global_resources",
]
