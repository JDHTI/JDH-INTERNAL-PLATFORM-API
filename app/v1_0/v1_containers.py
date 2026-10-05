"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.data_provider import DataProviderConnection
from app.infrastructure.database import Database
from app.infrastructure.realtime import RealtimeBroker
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.repository import SqlAlchemyAreaRepository
from app.modules.consumption.application.cursor import ClientCursorCodec
from app.modules.consumption.application.service import ConsumptionService
from app.modules.consumption.infrastructure.repository import WarehouseClientRepository
from app.modules.documents.application.service import DocumentService
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from app.modules.health.application.service import HealthService
from app.modules.identity.application.directory import UserDirectory
from app.modules.identity.application.service import IdentityService
from app.modules.identity.infrastructure.entra import EntraClient
from app.modules.identity.infrastructure.repository import IdentityRepository
from app.modules.masters.application.service import MasterService
from app.modules.masters.infrastructure.repository import SqlAlchemyMasterRepository
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.infrastructure.policies import AreaManagerPolicy, HolidayCalendar
from app.modules.pqrsf.infrastructure.repository import SqlAlchemyPqrsfRepository
from app.modules.pqrsf.infrastructure.security import ClamAVScanner, PillowImageProcessor
from app.modules.pqrsf.infrastructure.tracking_repository import SqlAlchemyCaseRepository
from app.modules.projects.application.live import ProjectLiveService
from app.modules.projects.application.service import ProjectService
from app.modules.projects.infrastructure.repository import SqlAlchemyProjectRepository
from app.modules.sites.application.service import SiteService
from app.modules.sites.infrastructure.repository import SqlAlchemySiteRepository


class V1Container(containers.DeclarativeContainer):
    """Version-scoped services and their infrastructure contracts."""

    config = providers.Configuration()

    database = providers.Dependency(instance_of=Database)
    redis = providers.Dependency(instance_of=RedisClient)
    realtime = providers.Dependency(instance_of=RealtimeBroker)
    storage = providers.Dependency(instance_of=S3Storage)
    data_provider = providers.Dependency(instance_of=DataProviderConnection)
    settings = providers.Dependency(instance_of=Settings)

    health_service = providers.Factory(
        HealthService,
        database=database,
        redis=redis,
        storage=storage,
        data_provider=data_provider,
    )
    consumption_repository = providers.Factory(
        WarehouseClientRepository,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        view=settings.provided.DWH_CLIENTS_VIEW,
    )
    consumption_cursor_codec = providers.Factory(
        ClientCursorCodec,
        secret=providers.Callable(
            lambda secret: secret.get_secret_value(), settings.provided.DWH_CURSOR_SECRET
        ),
        provider_name=settings.provided.DWH_PROVIDER,
    )
    consumption_service = providers.Factory(
        ConsumptionService,
        data_source=consumption_repository,
        cursor_codec=consumption_cursor_codec,
    )
    identity_repository = providers.Factory(IdentityRepository, database=database)
    entra_client = providers.Singleton(
        EntraClient,
        tenant_id=settings.provided.ENTRA_TENANT_ID,
        client_id=settings.provided.ENTRA_CLIENT_ID,
        client_secret=providers.Callable(
            lambda secret: secret.get_secret_value(), settings.provided.ENTRA_CLIENT_SECRET
        ),
        audience=settings.provided.entra_api_audience,
        api_scope=settings.provided.ENTRA_API_SCOPE,
        redirect_uri=settings.provided.ENTRA_REDIRECT_URI,
        authority=settings.provided.entra_authority,
        issuer=settings.provided.entra_issuer,
    )
    identity_service = providers.Factory(
        IdentityService,
        repository=identity_repository,
        entra=entra_client,
        storage=storage,
        settings=settings,
    )

    # Areas and documents read users through this port, never the tables.
    user_directory = providers.Factory(UserDirectory, repository=identity_repository)

    # --- Areas ---
    area_repository = providers.Factory(
        SqlAlchemyAreaRepository,
        database=database,
        # Same fallback IdentityService.tenant_id uses for users.
        tenant_id=providers.Callable(
            lambda tenant: tenant or "development", settings.provided.ENTRA_TENANT_ID
        ),
    )
    area_service = providers.Factory(
        AreaService,
        repository=area_repository,
        users=user_directory,
    )

    # --- Documents (segmented by area) ---
    document_repository = providers.Factory(SqlAlchemyDocumentRepository, database=database)
    document_service = providers.Factory(
        DocumentService,
        repository=document_repository,
        storage=storage,
        areas=area_service,
        max_upload_bytes=config.documents.max_upload_bytes,
        allowed_extensions=config.documents.allowed_extensions,
        download_url_ttl=config.documents.download_url_ttl,
    )
    # --- Projects (segmented by area, live over Redis) ---
    project_live_service = providers.Factory(
        ProjectLiveService, channel=realtime, presence_ttl=config.projects.presence_ttl
    )
    project_repository = providers.Factory(SqlAlchemyProjectRepository, database=database)
    project_service = providers.Factory(
        ProjectService,
        repository=project_repository,
        areas=area_service,
        people=user_directory,
        events=project_live_service,
        enabled_area_slugs=config.projects.area_slugs,
    )

    site_repository = providers.Factory(SqlAlchemySiteRepository, database=database)
    site_service = providers.Factory(SiteService, repository=site_repository)
    master_repository = providers.Factory(SqlAlchemyMasterRepository, database=database)
    master_service = providers.Factory(MasterService, repository=master_repository)
    pqrsf_repository = providers.Factory(SqlAlchemyPqrsfRepository, database=database)
    pqrsf_case_repository = providers.Factory(SqlAlchemyCaseRepository, database=database)
    pqrsf_calendar = providers.Singleton(
        HolidayCalendar,
        timezone=config.pqrsf.timezone,
        country=config.pqrsf.holidays_country,
    )
    pqrsf_manager_policy = providers.Factory(
        AreaManagerPolicy, areas=area_service, area_slug=config.pqrsf.manager_area_slug
    )
    pqrsf_tracking_service = providers.Factory(
        CaseTrackingService,
        repository=pqrsf_case_repository,
        requests=pqrsf_repository,
        sites=site_service,
        people=user_directory,
        managers=pqrsf_manager_policy,
        calendar=pqrsf_calendar,
    )
    pqrsf_image_processor = providers.Singleton(
        PillowImageProcessor,
        max_bytes=config.pqrsf.max_attachment_bytes,
        max_pixels=config.pqrsf.max_image_pixels,
    )
    pqrsf_malware_scanner = providers.Singleton(
        ClamAVScanner,
        host=config.pqrsf.clamav_host,
        port=config.pqrsf.clamav_port,
        timeout=config.pqrsf.clamav_timeout,
    )
    pqrsf_service = providers.Factory(
        PqrsfService,
        repository=pqrsf_repository,
        sites=site_service,
        storage=storage,
        image_processor=pqrsf_image_processor,
        malware_scanner=pqrsf_malware_scanner,
        cases=pqrsf_tracking_service,
        max_attachments=config.pqrsf.max_attachments,
        max_attachment_bytes=config.pqrsf.max_attachment_bytes,
        max_total_attachment_bytes=config.pqrsf.max_total_attachment_bytes,
    )
