"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.app_container import (
    GlobalResources,
    build_application_container,
    initialize_global_resources,
    shutdown_global_resources,
)
from app.core.config import Settings, settings
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging, logger
from app.core.middleware import RequestContextMiddleware
from app.modules.pqrsf.infrastructure.email_worker import EmailIntakeWorker
from app.modules.pqrsf.infrastructure.notification_worker import NotificationWorker


def create_app(app_settings: Settings | None = None) -> FastAPI:
    """Create an independently wired FastAPI application instance."""

    runtime_settings = app_settings or settings
    configure_logging(runtime_settings)
    container = build_application_container(runtime_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resources: GlobalResources | None = None
        mail_worker: EmailIntakeWorker | None = None
        notification_worker: NotificationWorker | None = None
        try:
            # Instantiate clients at startup; network probes remain part of readiness.
            resources = await initialize_global_resources(container)
            if runtime_settings.MAIL_INTAKE_ENABLED:
                v1 = container.v1_container
                mail_worker = EmailIntakeWorker(
                    v1.pqrsf_email_intake_service, runtime_settings.MAIL_POLL_INTERVAL_SECONDS
                )
                mail_worker.start()
                logger.info(f"PQRSF email intake reading {runtime_settings.MAIL_PQRSF_INBOX}")
            if runtime_settings.PQRSF_NOTIFICATIONS_ENABLED:
                notification_worker = NotificationWorker(
                    container.v1_container.pqrsf_notification_service
                )
                notification_worker.start()
                logger.info(f"PQRSF notifications sending from {runtime_settings.MAIL_PQRSF_INBOX}")
            logger.info(f"{runtime_settings.APP_NAME} starting in {runtime_settings.APP_ENV}")
            logger.info(
                f"API running on http://localhost:{runtime_settings.PORT}{runtime_settings.API_PREFIX}"
            )
            if runtime_settings.docs_enabled:
                logger.info(
                    "Swagger docs running on "
                    f"http://localhost:{runtime_settings.PORT}{runtime_settings.API_PREFIX}/docs"
                )
            else:
                logger.info("Swagger docs disabled in production environment")
            yield
        finally:
            logger.info(f"{runtime_settings.APP_NAME} shutdown")
            if notification_worker is not None:
                await notification_worker.stop()
                await container.v1_container.pqrsf_holidays().close()
            if mail_worker is not None:
                await mail_worker.stop()
            if mail_worker is not None or notification_worker is not None:
                await container.v1_container.mail_client().close()
            if resources is not None:
                await shutdown_global_resources(resources)

    allow_credentials = "*" not in runtime_settings.cors_origins
    app = FastAPI(
        title=runtime_settings.APP_NAME,
        version=runtime_settings.APP_VERSION,
        debug=runtime_settings.debug_enabled,
        openapi_url=(
            f"{runtime_settings.API_PREFIX}/openapi.json" if runtime_settings.docs_enabled else None
        ),
        docs_url=(f"{runtime_settings.API_PREFIX}/docs" if runtime_settings.docs_enabled else None),
        redoc_url=(
            f"{runtime_settings.API_PREFIX}/redoc" if runtime_settings.docs_enabled else None
        ),
        lifespan=lifespan,
    )
    app.state.container = container
    app.state.settings = runtime_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        # Lets the browser read the server-chosen name of downloaded files.
        expose_headers=["Content-Disposition"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": runtime_settings.APP_NAME, "version": runtime_settings.APP_VERSION}

    app.include_router(api_router, prefix=runtime_settings.API_PREFIX)
    return app


app = create_app()
