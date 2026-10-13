"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.data_provider import DataProviderConnection
from app.infrastructure.database import Database
from app.infrastructure.realtime import RealtimeBroker
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import AreaManagerPolicy
from app.modules.areas.infrastructure.repository import SqlAlchemyAreaRepository
from app.modules.commercial_masters.application.service import (
    CommercialMasterService,
    MasterLookup,
)
from app.modules.commercial_masters.application.sync import MasterSyncService
from app.modules.commercial_masters.infrastructure.dwh_source import WarehouseMasterSource
from app.modules.commercial_masters.infrastructure.margins_file import SpreadsheetMarginReader
from app.modules.commercial_masters.infrastructure.repository import (
    SqlAlchemyCommercialMasterRepository,
)
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
from app.modules.mail.infrastructure.graph import GraphMailClient
from app.modules.masters.application.service import MasterService
from app.modules.masters.infrastructure.repository import SqlAlchemyMasterRepository
from app.modules.order_validation.application.service import OrderValidationService
from app.modules.order_validation.infrastructure.agreements import SqlAlchemyAgreementSource
from app.modules.order_validation.infrastructure.order_file import SpreadsheetOrderReader
from app.modules.order_validation.infrastructure.repository import (
    SqlAlchemyOrderValidationRepository,
)
from app.modules.pqrsf.application.case_attachments import CaseAttachmentService
from app.modules.pqrsf.application.email_intake import EmailIntakeService
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.infrastructure.email_repository import SqlAlchemyEmailRepository
from app.modules.pqrsf.infrastructure.policies import HolidayCalendar
from app.modules.pqrsf.infrastructure.repository import SqlAlchemyPqrsfRepository
from app.modules.pqrsf.infrastructure.security import ClamAVScanner, PillowImageProcessor
from app.modules.pqrsf.infrastructure.tracking_repository import SqlAlchemyCaseRepository
from app.modules.projects.application.live import ProjectLiveService
from app.modules.projects.application.service import ProjectService
from app.modules.projects.infrastructure.repository import SqlAlchemyProjectRepository
from app.modules.sites.application.service import SiteService
from app.modules.sites.infrastructure.repository import SqlAlchemySiteRepository
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.application.special_cases import SpecialCaseService
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository
from app.modules.supplier_supports.infrastructure.special_cases_repository import (
    SqlAlchemySpecialCaseRepository,
)


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
    # --- Commercial masters (DWH mirror) and supplier supports ---
    commercial_master_repository = providers.Factory(
        SqlAlchemyCommercialMasterRepository, database=database
    )
    supplier_support_manager_policy = providers.Factory(
        AreaManagerPolicy,
        areas=area_service,
        area_slug=config.supplier_supports.manager_area_slug,
    )
    commercial_margin_reader = providers.Singleton(SpreadsheetMarginReader)
    commercial_master_service = providers.Factory(
        CommercialMasterService,
        repository=commercial_master_repository,
        managers=supplier_support_manager_policy,
        margin_reader=commercial_margin_reader,
        max_upload_bytes=config.product_margins.max_upload_bytes,
    )
    master_lookup = providers.Factory(MasterLookup, repository=commercial_master_repository)
    warehouse_master_source = providers.Factory(
        WarehouseMasterSource, connection=data_provider, schema=settings.provided.DWH_SCHEMA
    )
    master_sync_service = providers.Factory(
        MasterSyncService,
        source=warehouse_master_source,
        repository=commercial_master_repository,
        batch_size=config.masters_sync.batch_size,
    )
    special_case_repository = providers.Factory(SqlAlchemySpecialCaseRepository, database=database)
    special_case_service = providers.Factory(
        SpecialCaseService,
        repository=special_case_repository,
        masters=master_lookup,
        managers=supplier_support_manager_policy,
    )
    supplier_support_repository = providers.Factory(SqlAlchemySupportRepository, database=database)
    supplier_support_service = providers.Factory(
        SupplierSupportService,
        repository=supplier_support_repository,
        masters=master_lookup,
        managers=supplier_support_manager_policy,
        calendar=pqrsf_calendar,
    )
    # --- Retained orders validator (reads the supports master) ---
    order_validation_manager_policy = providers.Factory(
        AreaManagerPolicy,
        areas=area_service,
        area_slug=config.order_validation.manager_area_slug,
    )
    order_validation_repository = providers.Factory(
        SqlAlchemyOrderValidationRepository, database=database
    )
    order_validation_agreements = providers.Factory(SqlAlchemyAgreementSource, database=database)
    order_validation_reader = providers.Singleton(SpreadsheetOrderReader)
    order_validation_service = providers.Factory(
        OrderValidationService,
        repository=order_validation_repository,
        agreements=order_validation_agreements,
        reader=order_validation_reader,
        managers=order_validation_manager_policy,
        calendar=pqrsf_calendar,
        max_upload_bytes=config.order_validation.max_upload_bytes,
        max_lines=config.order_validation.max_lines,
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
    # --- PQRSF by email (Microsoft Graph) ---
    mail_client = providers.Singleton(
        GraphMailClient,
        tenant_id=config.mail.tenant_id,
        client_id=config.mail.client_id,
        client_secret=config.mail.client_secret,
    )
    mail_image_processor = providers.Singleton(
        PillowImageProcessor,
        max_bytes=config.mail.max_attachment_bytes,
        max_pixels=config.pqrsf.max_image_pixels,
    )
    pqrsf_email_repository = providers.Factory(SqlAlchemyEmailRepository, database=database)
    pqrsf_email_intake_service = providers.Factory(
        EmailIntakeService,
        mail=mail_client,
        emails=pqrsf_email_repository,
        cases=pqrsf_tracking_service,
        requests=pqrsf_repository,
        sites=site_service,
        storage=storage,
        image_processor=mail_image_processor,
        malware_scanner=pqrsf_malware_scanner,
        mailbox=config.mail.pqrsf_inbox,
        ignored_senders=config.mail.ignored_senders,
        since=config.mail.since,
        max_attempts=config.mail.max_attempts,
        max_attachments=config.mail.max_attachments,
        max_attachment_bytes=config.mail.max_attachment_bytes,
        max_total_attachment_bytes=config.mail.max_total_attachment_bytes,
        allowed_extensions=config.mail.attachment_extensions,
    )
    pqrsf_case_attachment_service = providers.Factory(
        CaseAttachmentService,
        emails=pqrsf_email_repository,
        access=pqrsf_tracking_service,
        storage=storage,
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
