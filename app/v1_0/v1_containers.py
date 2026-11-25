"""Dependency providers owned by API version 1."""

from zoneinfo import ZoneInfo

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.data_provider import DataProviderConnection
from app.infrastructure.database import Database
from app.infrastructure.gotenberg import GotenbergConverter
from app.infrastructure.realtime import RealtimeBroker
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.action_plans.application.service import ActionPlanService
from app.modules.action_plans.infrastructure.pqrsf_cases import PqrsfCaseSource
from app.modules.action_plans.infrastructure.repository import SqlAlchemyActionPlanRepository
from app.modules.app_monitoring.application.board import MonitoringBoardService
from app.modules.app_monitoring.application.service import AppMonitoringService
from app.modules.app_monitoring.infrastructure.prober import HttpProber
from app.modules.app_monitoring.infrastructure.repository import SqlAlchemyApplicationRepository
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import (
    AnyPermissionPolicy,
    AreaLeaderPolicy,
    AreaTeam,
    ExplicitGrantPolicy,
    PermissionPolicy,
    UnrestrictedAreaAccess,
)
from app.modules.areas.infrastructure.repository import SqlAlchemyAreaRepository
from app.modules.commercial_masters.application.employee_prices import EmployeePriceService
from app.modules.commercial_masters.application.price_increases import (
    AnalysisCache,
    PriceIncreaseService,
)
from app.modules.commercial_masters.application.service import (
    CommercialMasterService,
    MasterLookup,
)
from app.modules.commercial_masters.application.siesa_loads import SiesaLoadService
from app.modules.commercial_masters.application.sync import MasterSyncService
from app.modules.commercial_masters.infrastructure.dwh_source import WarehouseMasterSource
from app.modules.commercial_masters.infrastructure.margins_file import SpreadsheetMarginReader
from app.modules.commercial_masters.infrastructure.placements import WarehousePlacementSource
from app.modules.commercial_masters.infrastructure.price_increases_repository import (
    SqlAlchemyPriceIncreaseRepository,
)
from app.modules.commercial_masters.infrastructure.repository import (
    SqlAlchemyCommercialMasterRepository,
)
from app.modules.commercial_masters.infrastructure.siesa_load_repository import (
    SqlAlchemySiesaLoadRepository,
)
from app.modules.commercial_masters.infrastructure.siesa_quotations import (
    WarehouseCurrentQuotations,
)
from app.modules.consumption.application.cursor import ClientCursorCodec
from app.modules.consumption.application.service import ConsumptionService
from app.modules.consumption.infrastructure.repository import WarehouseClientRepository
from app.modules.cost_settings.application.service import CostSettingsService
from app.modules.cost_settings.infrastructure.repository import SqlAlchemyCostSettingsRepository
from app.modules.costs.application.access import CostAccess
from app.modules.costs.application.entries import EntryAnalysisService, EntryCache
from app.modules.costs.application.increases import IncreaseService
from app.modules.costs.application.novelties import NoveltyService
from app.modules.costs.infrastructure.dwh_source import WarehouseEntrySource
from app.modules.costs.infrastructure.increase_file import (
    IncreaseWorkbookReader,
    template_workbook,
    workbook_name,
)
from app.modules.costs.infrastructure.increase_repository import SqlAlchemyIncreaseRepository
from app.modules.costs.infrastructure.ledger import CommercialIncreaseLedger
from app.modules.costs.infrastructure.repository import SqlAlchemyNoveltyRepository
from app.modules.costs.infrastructure.supplier_items import WarehouseSupplierItems
from app.modules.directory.application.service import DirectoryService
from app.modules.directory.application.sync import DirectorySyncService
from app.modules.directory.infrastructure.graph import GraphDirectoryClient
from app.modules.directory.infrastructure.repository import (
    SqlAlchemyDirectoryRepository,
    SqlAlchemySyncRunRepository,
)
from app.modules.directory.infrastructure.worker import SyncRequest
from app.modules.documents.application.service import DocumentService
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from app.modules.dwh_monitoring.application.service import DwhMonitoringService
from app.modules.dwh_monitoring.application.sync import MonitoringSyncService
from app.modules.dwh_monitoring.infrastructure.dwh_source import WarehouseMonitoringSource
from app.modules.dwh_monitoring.infrastructure.repository import SqlAlchemyMonitoringRepository
from app.modules.dwh_monitoring.infrastructure.worker import SyncTrigger
from app.modules.dwh_publish.application.service import (
    DwhPublishService,
    PublishSettings,
    PublishState,
)
from app.modules.dwh_publish.infrastructure.repository import SqlAlchemyOutboxRepository
from app.modules.dwh_publish.infrastructure.writer import WarehouseWriterConnection
from app.modules.health.application.service import HealthService
from app.modules.identity.application.directory import UserDirectory
from app.modules.identity.application.provisioning import UserProvisioning
from app.modules.identity.application.service import IdentityService
from app.modules.identity.infrastructure.entra import EntraClient
from app.modules.identity.infrastructure.repository import IdentityRepository
from app.modules.jobs.application.service import JobService, JobsSettings, PollStatus
from app.modules.jobs.infrastructure.dwh_source import WarehouseSequenceSource
from app.modules.jobs.infrastructure.handlers import build_job_handlers
from app.modules.jobs.infrastructure.repository import (
    SqlAlchemyJobRunRepository,
    SqlAlchemySequenceWatermarks,
)
from app.modules.jobs.infrastructure.worker import RunRequest
from app.modules.mail.infrastructure.graph import GraphMailClient
from app.modules.masters.application.service import MasterService
from app.modules.masters.infrastructure.access import AreaMasterAccess
from app.modules.masters.infrastructure.cache import MasterReadCache
from app.modules.masters.infrastructure.dwh_source import WarehouseMasterReader
from app.modules.masters.infrastructure.repository import SqlAlchemyMasterRepository
from app.modules.notifications.application.service import (
    NotificationService as BellNotificationService,
)
from app.modules.notifications.infrastructure.push import WebPushSender
from app.modules.notifications.infrastructure.repository import (
    SqlAlchemyNotificationRepository as BellNotificationRepository,
)
from app.modules.order_validation.application.service import OrderValidationService
from app.modules.order_validation.domain.entities import OrderPolicy
from app.modules.order_validation.infrastructure.agreements import SqlAlchemyAgreementSource
from app.modules.order_validation.infrastructure.order_file import SpreadsheetOrderReader
from app.modules.order_validation.infrastructure.repository import (
    SqlAlchemyOrderValidationRepository,
)
from app.modules.pqrsf.application.attachment_guard import AttachmentGuard
from app.modules.pqrsf.application.case_attachments import CaseAttachmentService
from app.modules.pqrsf.application.certificates import CertificateService
from app.modules.pqrsf.application.classification import ClassificationService
from app.modules.pqrsf.application.comments import CaseCommentService
from app.modules.pqrsf.application.document_delivery import DocumentDeliveryService
from app.modules.pqrsf.application.email_intake import EmailIntakeService
from app.modules.pqrsf.application.feedback import FeedbackService
from app.modules.pqrsf.application.forwards import CustomerForwardService
from app.modules.pqrsf.application.improvement_opportunities import (
    ImprovementOpportunityService,
)
from app.modules.pqrsf.application.item_delivery import ItemDeliveryService
from app.modules.pqrsf.application.notifications import NotificationService
from app.modules.pqrsf.application.service import PqrsfService
from app.modules.pqrsf.application.supplier_messages import SupplierMessageService
from app.modules.pqrsf.application.tracking import CaseTrackingService
from app.modules.pqrsf.infrastructure.certificate_repository import (
    ManagerDocumentFiles,
    SqlAlchemyCertificateRepository,
)
from app.modules.pqrsf.infrastructure.delivery_repository import SqlAlchemyDeliveryRepository
from app.modules.pqrsf.infrastructure.email_repository import SqlAlchemyEmailRepository
from app.modules.pqrsf.infrastructure.feedback_repository import SqlAlchemyFeedbackRepository
from app.modules.pqrsf.infrastructure.holidays_api import NagerHolidayProvider
from app.modules.pqrsf.infrastructure.improvement_repository import (
    SqlAlchemyImprovementRepository,
)
from app.modules.pqrsf.infrastructure.item_delivery_repository import (
    SqlAlchemyItemDeliveryRepository,
)
from app.modules.pqrsf.infrastructure.notification_repository import (
    SqlAlchemyNotificationRepository,
)
from app.modules.pqrsf.infrastructure.notification_worker import Wakeup
from app.modules.pqrsf.infrastructure.policies import AreaTeamDirectory, HolidayCalendar
from app.modules.pqrsf.infrastructure.product_catalog import MasterProductCatalog
from app.modules.pqrsf.infrastructure.repository import SqlAlchemyPqrsfRepository
from app.modules.pqrsf.infrastructure.sales_lookup import WarehouseSalesLookup
from app.modules.pqrsf.infrastructure.security import (
    ClamAVScanner,
    PillowImageProcessor,
    VideoInspector,
)
from app.modules.pqrsf.infrastructure.signer_directory import DirectorySigner
from app.modules.pqrsf.infrastructure.supplier_contact_repository import (
    SqlAlchemySupplierContactRepository,
)
from app.modules.pqrsf.infrastructure.tracking_repository import SqlAlchemyCaseRepository
from app.modules.pqrsf.infrastructure.word_renderer import DocxTemplateRenderer
from app.modules.projects.application.live import ProjectLiveService
from app.modules.projects.application.service import ProjectService
from app.modules.projects.infrastructure.repository import SqlAlchemyProjectRepository
from app.modules.quotations.application.liquor_taxes import LiquorTaxService
from app.modules.quotations.application.list_review import ListReviewService
from app.modules.quotations.application.main_board import MainBoardService
from app.modules.quotations.application.sales_backfill import SalesBackfill
from app.modules.quotations.application.service import QuotationService
from app.modules.quotations.domain.entities import QuotationPolicy
from app.modules.quotations.infrastructure.board_repository import SqlAlchemyBoardRepository
from app.modules.quotations.infrastructure.dwh_insights import WarehouseInsightSource
from app.modules.quotations.infrastructure.dwh_source import WarehouseQuotationSource
from app.modules.quotations.infrastructure.lines_workbook import QuotationLinesWorkbook
from app.modules.quotations.infrastructure.liquor_repository import (
    SqlAlchemyLiquorTaxRepository,
)
from app.modules.quotations.infrastructure.list_template_file import ListTemplateFile
from app.modules.quotations.infrastructure.masters import SqlAlchemyQuotationMasterSource
from app.modules.quotations.infrastructure.offer_workbook import CustomerOfferWorkbook
from app.modules.quotations.infrastructure.repository import SqlAlchemyQuotationRepository
from app.modules.quotations.infrastructure.siesa_file import SiesaPriceFileWriter
from app.modules.quotations.infrastructure.workbook import PriceListWorkbookWriter
from app.modules.salespeople.application.service import SalesScopeService
from app.modules.salespeople.infrastructure.dwh_roster import WarehouseSalesRoster
from app.modules.salespeople.infrastructure.repository import SqlAlchemyLinkRepository
from app.modules.sites.application.service import SiteService
from app.modules.sites.infrastructure.repository import SqlAlchemySiteRepository
from app.modules.supplier_supports.application.attachments import SupportAttachmentService
from app.modules.supplier_supports.application.bulk import QuietHours, SupportBulkService
from app.modules.supplier_supports.application.costs import SupportCostService
from app.modules.supplier_supports.application.deletions import SupportDeletionService
from app.modules.supplier_supports.application.internal_requests import (
    InternalRequestLinks,
    InternalRequestService,
)
from app.modules.supplier_supports.application.profiles import SupportProfileService
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.application.rollover import SupportRolloverService
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.infrastructure.attachments_repository import (
    SqlAlchemyAttachmentRepository,
)
from app.modules.supplier_supports.infrastructure.bulk_file import SpreadsheetSupportReader
from app.modules.supplier_supports.infrastructure.costs import (
    SqlAlchemyCostRepository,
    WarehouseCostSource,
)
from app.modules.supplier_supports.infrastructure.deletions_repository import (
    SqlAlchemySupportDeletionRepository,
)
from app.modules.supplier_supports.infrastructure.document_store import (
    DocumentManagerSupportStore,
)
from app.modules.supplier_supports.infrastructure.internal_requests_repository import (
    SqlAlchemyInternalRequestRepository,
)
from app.modules.supplier_supports.infrastructure.profiles_repository import (
    SqlAlchemyProfileRepository,
)
from app.modules.supplier_supports.infrastructure.publication import (
    DeleteTopic,
    LoadTopic,
    SupportTopic,
    build_topics,
)
from app.modules.supplier_supports.infrastructure.quotation_access import QuoterAccess
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository
from app.modules.supplier_supports.infrastructure.requests_repository import (
    SqlAlchemySupportRequestRepository,
)
from app.modules.support_settlements.application.service import SettlementService
from app.modules.support_settlements.infrastructure.dwh_source import WarehouseSettlementSource
from app.modules.support_settlements.infrastructure.export_file import settlement_workbook
from app.modules.support_settlements.infrastructure.masters import SqlAlchemySettlementMasters
from app.modules.support_settlements.infrastructure.repository import (
    SqlAlchemySettlementRepository,
)
from app.modules.territory.application.prospects import ProspectService
from app.modules.territory.application.routes import RouteService
from app.modules.territory.application.zones import ZoneService
from app.modules.territory.infrastructure.openrouteservice import OpenRouteServiceRouter
from app.modules.territory.infrastructure.prospect_file import SpreadsheetProspectReader
from app.modules.territory.infrastructure.prospect_repository import (
    SqlAlchemyProspectRepository,
)
from app.modules.territory.infrastructure.route_repository import SqlAlchemyRouteRepository
from app.modules.territory.infrastructure.zone_repository import SqlAlchemyZoneRepository


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

    # Areas and documents read users through this port, never the tables.
    user_directory = providers.Factory(UserDirectory, repository=identity_repository)
    # The employee directory creates and blocks users through this port.
    user_provisioning = providers.Factory(
        UserProvisioning,
        repository=identity_repository,
        tenant_id=config.directory.user_tenant_id,
    )

    # --- Employee directory (Entra users synced nightly through Graph) ---
    directory_graph_client = providers.Singleton(
        GraphDirectoryClient,
        tenant_id=config.directory.tenant_id,
        client_id=config.directory.client_id,
        client_secret=config.directory.client_secret,
    )
    directory_repository = providers.Factory(SqlAlchemyDirectoryRepository, database=database)
    directory_run_repository = providers.Factory(SqlAlchemySyncRunRepository, database=database)
    # Hands the runs an administrator asks for to the background sync.
    directory_sync_trigger = providers.Singleton(SyncRequest)
    directory_sync_service = providers.Factory(
        DirectorySyncService,
        graph=directory_graph_client,
        repository=directory_repository,
        runs=directory_run_repository,
        users=user_provisioning,
        storage=storage,
        corporate_domains=config.directory.corporate_domains,
        batch_size=config.directory.batch_size,
    )
    # --- Notifications of the general view (bell, Web Push, Strapi ingestion) ---
    notification_repository = providers.Factory(BellNotificationRepository, database=database)
    notification_push = providers.Singleton(
        WebPushSender,
        public_key=config.notifications.vapid_public_key,
        private_key=config.notifications.vapid_private_key,
        subject=config.notifications.vapid_subject,
    )
    directory_service = providers.Factory(
        DirectoryService,
        repository=directory_repository,
        runs=directory_run_repository,
        storage=storage,
        sync_enabled=config.directory.sync_enabled,
        sync_at=config.directory.sync_at,
        timezone=config.directory.timezone,
        request_sync=directory_sync_trigger.provided.set,
    )
    notification_service = providers.Factory(
        BellNotificationService,
        repository=notification_repository,
        directory=directory_service,
        push=notification_push,
        timezone=config.notifications.timezone,
    )

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
    identity_service = providers.Factory(
        IdentityService,
        repository=identity_repository,
        entra=entra_client,
        storage=storage,
        settings=settings,
        # Global role changes go to the permissions log of the areas module.
        audit=area_service,
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
    )

    site_repository = providers.Factory(SqlAlchemySiteRepository, database=database)
    site_service = providers.Factory(SiteService, repository=site_repository)
    master_repository = providers.Factory(SqlAlchemyMasterRepository, database=database)
    master_access = providers.Factory(AreaMasterAccess, areas=area_service)
    # Masters are read live from the DWH; one reader caps the queries and one cache keeps
    # what was read by sequence run, for the whole API.
    master_warehouse = providers.Singleton(
        WarehouseMasterReader,
        connection=data_provider,
        query_timeout=config.masters.dwh_query_timeout,
        max_concurrent=config.masters.dwh_max_concurrent,
    )
    master_cache = providers.Singleton(MasterReadCache)
    master_service = providers.Factory(
        MasterService,
        repository=master_repository,
        warehouse=master_warehouse,
        cache=master_cache,
        access=master_access,
    )
    pqrsf_repository = providers.Factory(SqlAlchemyPqrsfRepository, database=database)
    pqrsf_case_repository = providers.Factory(SqlAlchemyCaseRepository, database=database)
    pqrsf_calendar = providers.Singleton(
        HolidayCalendar,
        timezone=config.pqrsf.timezone,
        country=config.pqrsf.holidays_country,
    )
    pqrsf_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.pqrsf.manager_area_slug,
        module="pqrsf",
    )
    pqrsf_classification_service = providers.Factory(
        ClassificationService,
        requests=pqrsf_repository,
        cases=pqrsf_case_repository,
        managers=pqrsf_manager_policy,
    )
    # --- PQRSF emails sent from the mailbox (Microsoft Graph) ---
    mail_client = providers.Singleton(
        GraphMailClient,
        tenant_id=config.mail.tenant_id,
        client_id=config.mail.client_id,
        client_secret=config.mail.client_secret,
    )
    pqrsf_team = providers.Factory(
        AreaTeamDirectory, areas=area_service, area_slug=config.pqrsf.manager_area_slug
    )
    pqrsf_holidays = providers.Singleton(
        NagerHolidayProvider,
        country=config.pqrsf.holidays_country,
        fallback=pqrsf_calendar,
        base_url=config.pqrsf.holidays_api_url,
    )
    pqrsf_notification_repository = providers.Factory(
        SqlAlchemyNotificationRepository, database=database
    )
    pqrsf_email_repository = providers.Factory(SqlAlchemyEmailRepository, database=database)
    pqrsf_feedback_repository = providers.Factory(SqlAlchemyFeedbackRepository, database=database)
    # Wakes the email sender so a thank-you leaves right after it is queued.
    pqrsf_wakeup = providers.Singleton(Wakeup)
    pqrsf_feedback_service = providers.Factory(
        FeedbackService,
        repository=pqrsf_feedback_repository,
        notifications=pqrsf_notification_repository,
        storage=storage,
        managers=pqrsf_manager_policy,
        people=user_directory,
        replies_enabled=config.pqrsf.notifications_enabled,
        max_replies_per_email=config.pqrsf.feedback_replies_per_email,
        wake=pqrsf_wakeup.provided.set,
    )
    pqrsf_certificate_repository = providers.Factory(
        SqlAlchemyCertificateRepository, database=database
    )
    pqrsf_delivery_repository = providers.Factory(SqlAlchemyDeliveryRepository, database=database)
    pqrsf_manager_files = providers.Factory(
        ManagerDocumentFiles, database=database, storage=storage
    )
    pqrsf_notification_service = providers.Factory(
        NotificationService,
        repository=pqrsf_notification_repository,
        cases=pqrsf_case_repository,
        people=user_directory,
        team=pqrsf_team,
        mail=mail_client,
        managers=pqrsf_manager_policy,
        holidays=pqrsf_holidays,
        mailbox=config.mail.pqrsf_inbox,
        area_slug=config.pqrsf.manager_area_slug,
        ui_base_url=config.pqrsf.ui_base_url,
        timezone=config.pqrsf.timezone,
        redirect_to=config.pqrsf.notify_redirect,
        enabled=config.pqrsf.notifications_enabled,
        emails=pqrsf_email_repository,
        storage=storage,
        documents=pqrsf_manager_files,
        requests=pqrsf_repository,
        bell=notification_service,
        noreply_sender=config.mail.noreply_sender,
    )
    pqrsf_sales_lookup = providers.Factory(
        WarehouseSalesLookup, connection=data_provider, schema=settings.provided.DWH_SCHEMA
    )
    pqrsf_tracking_service = providers.Factory(
        CaseTrackingService,
        repository=pqrsf_case_repository,
        requests=pqrsf_repository,
        sites=site_service,
        people=user_directory,
        managers=pqrsf_manager_policy,
        calendar=pqrsf_calendar,
        notifier=pqrsf_notification_service,
        sales=pqrsf_sales_lookup,
    )
    # --- Salespeople: who is which SIESA salesperson, and each person's sales scope ---
    # (before the supports and the quoter: both show each person only their customers)
    salesperson_link_repository = providers.Factory(SqlAlchemyLinkRepository, database=database)
    sales_roster = providers.Factory(
        WarehouseSalesRoster,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.quotations.dwh_timeout,
    )
    # Comercial's directors (2026-11-23): every customer, support and customer quotation. Only
    # granted by name, so a Comercial role holding ``*`` is not a director.
    commercial_director_policy = providers.Factory(
        ExplicitGrantPolicy,
        areas=area_service,
        area_slug=config.quotations.manager_area_slug,
        module="comercial-director",
    )
    sales_scope_service = providers.Factory(
        SalesScopeService,
        directors=commercial_director_policy,
        links=salesperson_link_repository,
        roster=sales_roster,
        users=user_directory,
        access=area_service,
        audit=area_service,
        # BI and the call center see every customer; in Comercial only its leader or
        # responsible does.
        all_area_slugs=providers.List(
            config.quotations.approver_area_slug, config.quotations.call_center_area_slug
        ),
        leader_area_slugs=providers.List(config.quotations.manager_area_slug),
    )
    # --- Commercial masters (DWH mirror) and supplier supports ---
    commercial_master_repository = providers.Factory(
        SqlAlchemyCommercialMasterRepository, database=database
    )
    supplier_support_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.supplier_supports.manager_area_slug,
        module="apoyos",
    )
    commercial_master_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.supplier_supports.manager_area_slug,
        module="maestros",
    )
    commercial_margin_reader = providers.Singleton(SpreadsheetMarginReader)
    commercial_master_service = providers.Factory(
        CommercialMasterService,
        repository=commercial_master_repository,
        managers=commercial_master_manager_policy,
        margin_reader=commercial_margin_reader,
        max_upload_bytes=config.product_margins.max_upload_bytes,
    )
    price_increase_repository = providers.Factory(
        SqlAlchemyPriceIncreaseRepository, database=database
    )
    # Kept across requests: the analysis of a period is worked out once per data version.
    price_increase_analysis_cache = providers.Singleton(AnalysisCache)
    price_increase_service = providers.Factory(
        PriceIncreaseService,
        repository=price_increase_repository,
        # BI's masters, and Abastecimiento's history of increases (Costos, 2026-11-23).
        managers=providers.Factory(
            AnyPermissionPolicy,
            commercial_master_manager_policy,
            providers.Factory(
                PermissionPolicy, areas=area_service, area_slug=None, module="acumulado-alzas"
            ),
        ),
        # The area's workbook, uploaded back from its sheet (2026-11-25).
        max_upload_bytes=config.product_margins.max_upload_bytes,
        cache=price_increase_analysis_cache,
        # The operation center and installation filters (2026-11-24).
        placements=providers.Factory(
            WarehousePlacementSource,
            connection=data_provider,
            schema=settings.provided.DWH_SCHEMA,
        ),
    )
    # Pending increases into SIESA's supplier quotations (the 0212 file; 2026-11-25).
    siesa_load_service = providers.Factory(
        SiesaLoadService,
        repository=providers.Factory(SqlAlchemySiesaLoadRepository, database=database),
        increases=price_increase_service,
        quotations=providers.Factory(
            WarehouseCurrentQuotations,
            connection=data_provider,
            schema=settings.provided.DWH_SCHEMA,
        ),
    )
    master_lookup = providers.Factory(MasterLookup, repository=commercial_master_repository)
    pqrsf_product_catalog = providers.Factory(MasterProductCatalog, masters=master_lookup)
    warehouse_master_source = providers.Factory(
        WarehouseMasterSource, connection=data_provider, schema=settings.provided.DWH_SCHEMA
    )
    employee_price_service = providers.Factory(
        EmployeePriceService,
        repository=commercial_master_repository,
        costs=warehouse_master_source,
    )
    master_sync_service = providers.Factory(
        MasterSyncService,
        source=warehouse_master_source,
        repository=commercial_master_repository,
        batch_size=config.masters_sync.batch_size,
    )
    supplier_support_repository = providers.Factory(SqlAlchemySupportRepository, database=database)
    supplier_support_service = providers.Factory(
        SupplierSupportService,
        repository=supplier_support_repository,
        masters=master_lookup,
        managers=supplier_support_manager_policy,
        calendar=pqrsf_calendar,
        sales=sales_scope_service,
    )
    supplier_support_request_repository = providers.Factory(
        SqlAlchemySupportRequestRepository, database=database
    )
    # Comercial asks for supports and BI reviews: one module, two meanings of manage.
    supplier_support_requester_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug="comercial", module="solicitudes-apoyos"
    )
    supplier_support_reviewer_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.supplier_supports.manager_area_slug,
        module="solicitudes-apoyos",
    )
    supplier_support_request_service = providers.Factory(
        SupportRequestService,
        repository=supplier_support_request_repository,
        managers=supplier_support_reviewer_policy,
        requesters=supplier_support_requester_policy,
        calendar=pqrsf_calendar,
    )
    supplier_support_profile_repository = providers.Factory(
        SqlAlchemyProfileRepository, database=database
    )
    supplier_support_profile_service = providers.Factory(
        SupportProfileService,
        repository=supplier_support_profile_repository,
        masters=master_lookup,
        managers=supplier_support_manager_policy,
    )
    supplier_support_attachment_repository = providers.Factory(
        SqlAlchemyAttachmentRepository, database=database
    )
    # The supports managers file proofs in BI's space without belonging to BI: this document
    # service does not check area membership, so it is only reachable through the store, which
    # runs after the supports' own permission check.
    unrestricted_area_access = providers.Factory(UnrestrictedAreaAccess, repository=area_repository)
    supplier_support_document_service = providers.Factory(
        DocumentService,
        repository=document_repository,
        storage=storage,
        areas=unrestricted_area_access,
        max_upload_bytes=config.documents.max_upload_bytes,
        allowed_extensions=config.documents.allowed_extensions,
        download_url_ttl=config.documents.download_url_ttl,
    )
    supplier_support_document_store = providers.Factory(
        DocumentManagerSupportStore,
        documents=supplier_support_document_service,
        area_slug=config.supplier_supports.documents_area_slug,
        folder_name=config.supplier_supports.documents_folder,
    )
    supplier_support_attachment_service = providers.Factory(
        SupportAttachmentService,
        supports=supplier_support_service,
        attachments=supplier_support_attachment_repository,
        store=supplier_support_document_store,
        max_supports=config.supplier_supports.max_attach_supports,
        max_file_bytes=config.documents.max_upload_bytes,
    )
    supplier_support_file_reader = providers.Singleton(SpreadsheetSupportReader)
    dwh_publish_zone = providers.Singleton(ZoneInfo, config.dwh_publish.timezone)
    supplier_support_quiet_hours = providers.Singleton(
        QuietHours,
        start=config.dwh_publish.quiet_start,
        end=config.dwh_publish.quiet_end,
        zone=dwh_publish_zone,
        defer_over=config.dwh_publish.defer_over_rows,
    )
    # --- Publication to the DWH (plataforma schema): outbox, writer role, worker ---
    dwh_writer = providers.Singleton(
        WarehouseWriterConnection,
        host=config.dwh_publish.host,
        port=config.dwh_publish.port,
        database=config.dwh_publish.database,
        user=config.dwh_publish.user,
        password=config.dwh_publish.password,
        ssl=config.dwh_publish.ssl,
        max_size=config.dwh_publish.pool_size,
        statement_timeout_ms=config.dwh_publish.statement_timeout_ms,
        lock_timeout_ms=config.dwh_publish.lock_timeout_ms,
    )
    dwh_outbox_repository = providers.Factory(SqlAlchemyOutboxRepository, database=database)
    dwh_publish_topics = providers.Singleton(
        build_topics,
        supports=providers.Factory(SupportTopic, database=database),
        loads=providers.Factory(LoadTopic, database=database),
        deletes=providers.Factory(DeleteTopic),
    )
    dwh_publish_settings = providers.Singleton(
        PublishSettings,
        enabled=config.dwh_publish.enabled,
        batch_size=config.dwh_publish.batch_size,
        max_attempts=config.dwh_publish.max_attempts,
        poll_seconds=config.dwh_publish.poll_seconds,
    )
    # The worker and the status endpoint share what the worker last did.
    dwh_publish_state = providers.Singleton(PublishState)
    dwh_publish_service = providers.Factory(
        DwhPublishService,
        repository=dwh_outbox_repository,
        writer=dwh_writer,
        topics=dwh_publish_topics,
        settings=dwh_publish_settings,
        state=dwh_publish_state,
        configured=config.dwh_publish.configured,
    )
    # --- Retained orders validator (reads the supports master) ---
    order_validation_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.order_validation.manager_area_slug,
        module="validador-pedidos",
    )
    # The service decides and the repository counts the ready orders: both use this policy.
    order_validation_policy = providers.Singleton(
        OrderPolicy,
        max_points_below_target=config.order_validation.max_points_below_target,
        tolerance_min_order_margin=config.order_validation.tolerance_min_order_margin,
        tolerance_max_lines=config.order_validation.tolerance_max_lines,
    )
    order_validation_repository = providers.Factory(
        SqlAlchemyOrderValidationRepository, database=database, policy=order_validation_policy
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
        policy=order_validation_policy,
    )
    # --- Quoter (Comercial → Cotizador): live DWH reads plus the supports of the validator ---
    quotation_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.quotations.manager_area_slug,
        module="cotizador",
    )
    quotation_approver_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.quotations.approver_area_slug,
        module="cotizador",
    )
    # List updates (BI → Actualización de listas): the same engine, its own module. Whoever
    # manages the module in any area updates lists; the approvers' area validates the items.
    list_update_manager_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug=None, module="actualizacion-listas"
    )
    list_update_approver_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.quotations.approver_area_slug,
        module="actualizacion-listas",
    )
    # The main lists (BI → Listas principales, locked to BI): ``manage`` on the module edits
    # them; ``manage`` on ``validacion-listas`` (BI's analysts and leader) validates and loads.
    main_list_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.quotations.approver_area_slug,
        module="listas-principales",
    )
    main_list_approver_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.quotations.approver_area_slug,
        module="validacion-listas",
    )
    quotation_policy = providers.Singleton(
        QuotationPolicy, max_points_below_target=config.quotations.max_points_below_target
    )
    quotation_offer_writer = providers.Singleton(CustomerOfferWorkbook)
    # --- Ajuste de costos: the quoter's cost rules (2026-11-25) ---
    # ``read`` and ``manage`` on ``ajuste-costos`` in any area where it is on (BI by default).
    cost_settings_service = providers.Factory(
        CostSettingsService,
        repository=providers.Factory(SqlAlchemyCostSettingsRepository, database=database),
        managers=providers.Factory(
            PermissionPolicy, areas=area_service, area_slug=None, module="ajuste-costos"
        ),
        threshold_default=config.quotations.max_points_below_target,
    )
    quotation_lines_writer = providers.Singleton(QuotationLinesWorkbook)
    quotation_template_reader = providers.Singleton(ListTemplateFile)
    quotation_repository = providers.Factory(SqlAlchemyQuotationRepository, database=database)
    quotation_warehouse_source = providers.Factory(
        WarehouseQuotationSource,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.quotations.dwh_timeout,
        sales_timeout=config.quotations.dwh_sales_timeout,
        cost_rules=cost_settings_service,
    )
    quotation_insight_source = providers.Factory(
        WarehouseInsightSource,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.quotations.dwh_timeout,
    )
    liquor_tax_repository = providers.Factory(SqlAlchemyLiquorTaxRepository, database=database)
    quotation_master_source = providers.Factory(SqlAlchemyQuotationMasterSource, database=database)
    quotation_workbook_writer = providers.Singleton(PriceListWorkbookWriter)
    quotation_flat_file_writer = providers.Singleton(
        SiesaPriceFileWriter, company=config.quotations.siesa_company
    )
    # --- Commercial map (prospects on the map, zones, visit routes) ---
    # ``manage`` in any area where the module is on (Comercial by default).
    territory_manager_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug=None, module="mapa-comercial"
    )
    territory_prospect_repository = providers.Factory(
        SqlAlchemyProspectRepository, database=database
    )
    territory_zone_repository = providers.Factory(SqlAlchemyZoneRepository, database=database)
    territory_route_repository = providers.Factory(SqlAlchemyRouteRepository, database=database)
    territory_reader = providers.Singleton(SpreadsheetProspectReader)
    # One HTTP client for the process; closed in the lifespan.
    territory_routing = providers.Singleton(
        OpenRouteServiceRouter,
        api_key=config.territory.routing_api_key,
        directions_url=config.territory.routing_directions_url,
        optimization_url=config.territory.routing_optimization_url,
        timeout_seconds=config.territory.routing_timeout,
        provider=config.territory.routing_provider,
    )
    territory_prospect_service = providers.Factory(
        ProspectService,
        repository=territory_prospect_repository,
        reader=territory_reader,
        managers=territory_manager_policy,
        max_upload_bytes=config.territory.max_upload_bytes,
    )
    territory_zone_service = providers.Factory(
        ZoneService, repository=territory_zone_repository, managers=territory_manager_policy
    )
    territory_route_service = providers.Factory(
        RouteService,
        routes=territory_route_repository,
        prospects=territory_prospect_repository,
        routing=territory_routing,
        users=user_directory,
        managers=territory_manager_policy,
        max_stops=config.territory.routing_max_stops,
    )
    # --- Internal support requests (asked from a customer quotation, decided by BI) ---
    internal_support_request_repository = providers.Factory(
        SqlAlchemyInternalRequestRepository, database=database
    )
    # Designated reviewers: ``manage`` on the permission in any area where it is on.
    internal_support_designated_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug=None, module="aprobacion-apoyos-internos"
    )
    internal_support_request_links = providers.Factory(
        InternalRequestLinks,
        repository=internal_support_request_repository,
        managers=supplier_support_manager_policy,
        designated=internal_support_designated_policy,
    )
    # The last sales of a list update built without them, read again in the background.
    quotation_sales_backfill = providers.Factory(
        SalesBackfill,
        repository=quotation_repository,
        warehouse=quotation_warehouse_source,
    )
    quotation_service = providers.Factory(
        QuotationService,
        sales_backfill=quotation_sales_backfill,
        support_requests=internal_support_request_links,
        sales=sales_scope_service,
        repository=quotation_repository,
        warehouse=quotation_warehouse_source,
        masters=quotation_master_source,
        supports=order_validation_agreements,
        managers=quotation_manager_policy,
        calendar=pqrsf_calendar,
        workbook=quotation_workbook_writer,
        flat_file=quotation_flat_file_writer,
        policy=quotation_policy,
        max_lines=config.quotations.max_lines,
        approvers=quotation_approver_policy,
        offer_writer=quotation_offer_writer,
        lines_writer=quotation_lines_writer,
        template_reader=quotation_template_reader,
        cost_rules=cost_settings_service,
        list_managers=list_update_manager_policy,
        list_approvers=list_update_approver_policy,
        main_lists=config.quotations.main_lists,
        main_managers=main_list_manager_policy,
        main_approvers=main_list_approver_policy,
        directors=commercial_director_policy,
        # The call center's leader sees the quotations of every member of the area.
        team=providers.Factory(
            AreaTeam,
            areas=area_service,
            repository=area_repository,
            area_slug=config.quotations.call_center_area_slug,
        ),
    )
    # Deleting supports and requests for good (2026-11-23): only the leader of the supports'
    # area (BI) and the platform administrators.
    supplier_support_deletion_service = providers.Factory(
        SupportDeletionService,
        repository=providers.Factory(SqlAlchemySupportDeletionRepository, database=database),
        deleters=providers.Factory(
            AreaLeaderPolicy,
            areas=area_service,
            area_slug=config.supplier_supports.manager_area_slug,
        ),
        supports=supplier_support_service,
    )
    internal_support_request_service = providers.Factory(
        InternalRequestService,
        repository=internal_support_request_repository,
        links=internal_support_request_links,
        quotations=providers.Factory(QuoterAccess, quotations=quotation_service),
        masters=master_lookup,
        calendar=pqrsf_calendar,
        # Comercial asks an authorized margin from the requests panel too (by customer + SKU).
        requesters=supplier_support_requester_policy,
        # A supplier margin request copies the guaranteed support it lowers.
        supports=supplier_support_repository,
    )
    list_review_service = providers.Factory(
        ListReviewService,
        quotations=quotation_service,
        repository=quotation_repository,
        insights=quotation_insight_source,
        calendar=pqrsf_calendar,
        workbook=quotation_workbook_writer,
        flat_file=quotation_flat_file_writer,
    )
    liquor_tax_service = providers.Factory(
        LiquorTaxService,
        quotations=quotation_service,
        source=quotation_insight_source,
        masters=quotation_master_source,
        repository=liquor_tax_repository,
        calendar=pqrsf_calendar,
        workbook=quotation_workbook_writer,
        flat_file=quotation_flat_file_writer,
        coast_lists=config.quotations.coast_lists,
        excluded_lists=config.quotations.liquor_excluded_lists,
        # The few collaborators who change the liquor tax: ``manage`` on ``impuesto-licor``.
        managers=providers.Factory(
            PermissionPolicy,
            areas=area_service,
            area_slug=config.quotations.approver_area_slug,
            module="impuesto-licor",
        ),
    )
    # --- Data warehouse monitoring (TI → Monitoreo → Datawarehouse) ---
    # Same DWH connection as the masters; only TI members and admins see it.
    dwh_monitoring_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug="ti", module="datawarehouse"
    )
    app_monitoring_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug="ti", module="monitoreo"
    )
    dwh_monitoring_repository = providers.Factory(SqlAlchemyMonitoringRepository, database=database)
    dwh_monitoring_source = providers.Factory(WarehouseMonitoringSource, connection=data_provider)
    dwh_monitoring_sync_service = providers.Factory(
        MonitoringSyncService, source=dwh_monitoring_source, repository=dwh_monitoring_repository
    )
    # Wakes the background load when someone asks for a load now.
    dwh_monitoring_trigger = providers.Singleton(SyncTrigger)
    dwh_monitoring_service = providers.Factory(
        DwhMonitoringService,
        repository=dwh_monitoring_repository,
        access=dwh_monitoring_policy,
        sync_enabled=config.dwh_monitoring.sync_enabled,
        sync_interval=config.dwh_monitoring.sync_interval,
        request_sync=dwh_monitoring_trigger.provided.set,
    )
    # --- Costos (Abastecimiento): entries against the quotation, novelties, credit notes ---
    cost_access = providers.Factory(
        CostAccess,
        entries=providers.Factory(
            PermissionPolicy, areas=area_service, area_slug=None, module="analisis-entradas"
        ),
        novelties=providers.Factory(
            PermissionPolicy, areas=area_service, area_slug=None, module="novedades-compras"
        ),
        increases=providers.Factory(
            PermissionPolicy, areas=area_service, area_slug=None, module="alzas-proveedor"
        ),
        ledger=providers.Factory(
            PermissionPolicy, areas=area_service, area_slug=None, module="acumulado-alzas"
        ),
    )
    cost_entry_source = providers.Factory(
        WarehouseEntrySource,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.costs.dwh_timeout,
    )
    cost_novelty_repository = providers.Factory(SqlAlchemyNoveltyRepository, database=database)
    # Kept across requests: a period's entries are read from the DWH once every few minutes.
    cost_entry_cache = providers.Singleton(EntryCache)
    cost_entry_service = providers.Factory(
        EntryAnalysisService,
        source=cost_entry_source,
        novelties=cost_novelty_repository,
        threshold=config.costs.threshold,
        max_range_days=config.costs.max_range_days,
        cache_seconds=config.costs.cache_seconds,
        cache=cost_entry_cache,
    )
    cost_credit_note_guard = providers.Factory(
        AttachmentGuard,
        image_processor=providers.Singleton(
            PillowImageProcessor,
            max_bytes=config.costs.credit_note_max_bytes,
            max_pixels=config.pqrsf.max_image_pixels,
        ),
        malware_scanner=providers.Singleton(
            ClamAVScanner,
            host=config.pqrsf.clamav_host,
            port=config.pqrsf.clamav_port,
            timeout=config.pqrsf.clamav_timeout,
        ),
        allowed_extensions=config.costs.credit_note_extensions,
        max_attachments=1,
        max_attachment_bytes=config.costs.credit_note_max_bytes,
        max_total_attachment_bytes=config.costs.credit_note_max_bytes,
    )
    cost_novelty_service = providers.Factory(
        NoveltyService,
        repository=cost_novelty_repository,
        source=cost_entry_source,
        access=cost_access,
        storage=storage,
        guard=cost_credit_note_guard,
        threshold=config.costs.threshold,
        sync_days=config.costs.sync_days,
        start_date=config.costs.start_date,
    )
    # The price increases suppliers send («MAESTRA» or the form), approved into the history.
    cost_increase_service = providers.Factory(
        IncreaseService,
        repository=providers.Factory(SqlAlchemyIncreaseRepository, database=database),
        items=providers.Factory(
            WarehouseSupplierItems,
            connection=data_provider,
            schema=settings.provided.DWH_SCHEMA,
            timeout=config.costs.dwh_timeout,
        ),
        ledger=providers.Factory(CommercialIncreaseLedger, repository=price_increase_repository),
        access=cost_access,
        storage=storage,
        reader=providers.Singleton(IncreaseWorkbookReader),
        writer=providers.Object(template_workbook),
        namer=providers.Object(workbook_name),
        reasons=cost_novelty_repository.provided.reasons,
        max_upload_bytes=config.costs.increases_max_upload_bytes,
        # The supplier's support of the increase (2026-11-24): PDF, picture, sheet or Word.
        support_guard=providers.Factory(
            AttachmentGuard,
            image_processor=providers.Singleton(
                PillowImageProcessor,
                max_bytes=config.costs.support_max_bytes,
                max_pixels=config.pqrsf.max_image_pixels,
            ),
            malware_scanner=providers.Singleton(
                ClamAVScanner,
                host=config.pqrsf.clamav_host,
                port=config.pqrsf.clamav_port,
                timeout=config.pqrsf.clamav_timeout,
            ),
            allowed_extensions=config.costs.support_extensions,
            max_attachments=1,
            max_attachment_bytes=config.costs.support_max_bytes,
            max_total_attachment_bytes=config.costs.support_max_bytes,
        ),
        area_slug=config.costs.area_slug,
    )
    # --- Liquidación de apoyos (BI): what each supplier owes for a month of sales ---
    support_settlement_service = providers.Factory(
        SettlementService,
        repository=providers.Factory(SqlAlchemySettlementRepository, database=database),
        masters=providers.Factory(SqlAlchemySettlementMasters, database=database),
        sales=providers.Factory(
            WarehouseSettlementSource,
            connection=data_provider,
            schema=settings.provided.DWH_SCHEMA,
            timeout=config.support_settlements.dwh_timeout,
        ),
        policy=providers.Factory(
            PermissionPolicy,
            areas=area_service,
            area_slug=config.supplier_supports.manager_area_slug,
            module="liquidacion-apoyos",
        ),
        # Reopening a closed month: the leader of the supports' area and platform admins.
        leaders=providers.Factory(
            AreaLeaderPolicy,
            areas=area_service,
            area_slug=config.supplier_supports.manager_area_slug,
        ),
        start_period=config.support_settlements.start_period,
        writer=providers.Object(settlement_workbook),
    )
    # --- Background jobs: the master syncs, started when the DWH sequences finish ---
    job_run_repository = providers.Factory(SqlAlchemyJobRunRepository, database=database)
    job_watermarks = providers.Factory(SqlAlchemySequenceWatermarks, database=database)
    job_sequence_source = providers.Factory(WarehouseSequenceSource, connection=data_provider)
    supplier_support_cost_source = providers.Factory(
        WarehouseCostSource,
        connection=data_provider,
        quotations=quotation_warehouse_source,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.quotations.dwh_timeout,
    )
    supplier_support_cost_service = providers.Factory(
        SupportCostService,
        repository=providers.Factory(SqlAlchemyCostRepository, database=database),
        warehouse=supplier_support_cost_source,
        cost_rules=cost_settings_service,
    )
    # The bulk load assigns the DWH cost of its supports as part of the load (not the job).
    supplier_support_bulk_service = providers.Factory(
        SupportBulkService,
        supports=supplier_support_service,
        profiles=supplier_support_profile_service,
        reader=supplier_support_file_reader,
        max_upload_bytes=config.supplier_supports.max_upload_bytes,
        max_rows=config.supplier_supports.max_bulk_rows,
        quiet_hours=supplier_support_quiet_hours,
        costs=supplier_support_cost_service,
    )
    # Programmed supports replace the previous one (daily job and the alerts).
    supplier_support_rollover_service = providers.Factory(
        SupportRolloverService,
        repository=supplier_support_repository,
        calendar=pqrsf_calendar,
    )
    # The worker and the API share the last poll result and the queue of runs.
    job_poll_status = providers.Singleton(PollStatus)
    job_request = providers.Singleton(RunRequest)
    job_settings = providers.Singleton(
        JobsSettings,
        enabled=config.jobs.enabled,
        poll_seconds=config.jobs.poll_seconds,
        fallback_at=config.jobs.fallback_at,
        timezone=config.jobs.timezone,
    )
    # The board of Listas principales; its sync is the background job ``main-lists-sync``.
    main_board_service = providers.Factory(
        MainBoardService,
        quotations=quotation_service,
        review=list_review_service,
        repository=providers.Factory(SqlAlchemyBoardRepository, database=database),
        insights=quotation_insight_source,
        runs=job_run_repository,
        jobs_enabled=config.jobs.enabled,
        request_run=job_request.provided.set,
    )
    job_handlers = providers.Singleton(
        build_job_handlers,
        masters=master_service.provider,
        commercial_sync=master_sync_service.provider,
        support_costs=supplier_support_cost_service.provider,
        main_lists=main_board_service.provider,
        support_rollover=supplier_support_rollover_service.provider,
        stale_drafts=quotation_service.provider,
        cost_novelties=cost_novelty_service.provider,
        support_settlements=support_settlement_service.provider,
    )
    job_service = providers.Factory(
        JobService,
        runs=job_run_repository,
        watermarks=job_watermarks,
        sequences=job_sequence_source,
        handlers=job_handlers,
        status=job_poll_status,
        settings=job_settings,
        request_run=job_request.provided.set,
        wait_for_dwh_seconds=config.jobs.wait_for_dwh,
    )
    # --- Application monitoring (TI → Monitoreo → Aplicativos) and the general board ---
    app_monitoring_repository = providers.Factory(
        SqlAlchemyApplicationRepository, database=database
    )
    app_monitoring_prober = providers.Singleton(HttpProber)
    # Wakes the background checks when someone asks to check everything now.
    app_monitoring_trigger = providers.Singleton(SyncTrigger)
    app_monitoring_service = providers.Factory(
        AppMonitoringService,
        repository=app_monitoring_repository,
        prober=app_monitoring_prober,
        access=app_monitoring_policy,
        check_enabled=config.app_monitoring.enabled,
        check_interval=config.app_monitoring.interval,
        retention_days=config.app_monitoring.retention_days,
        request_check=app_monitoring_trigger.provided.set,
    )
    monitoring_board_service = providers.Factory(
        MonitoringBoardService,
        applications=app_monitoring_service,
        warehouse=dwh_monitoring_service,
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
    pqrsf_video_inspector = providers.Singleton(
        VideoInspector, max_bytes=config.pqrsf.max_video_bytes
    )
    # --- PQRSF by email: reading the mailbox ---
    mail_image_processor = providers.Singleton(
        PillowImageProcessor,
        max_bytes=config.mail.max_attachment_bytes,
        max_pixels=config.pqrsf.max_image_pixels,
    )
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
        open_cases=config.mail.open_cases,
        max_attempts=config.mail.max_attempts,
        max_attachments=config.mail.max_attachments,
        max_attachment_bytes=config.mail.max_attachment_bytes,
        max_total_attachment_bytes=config.mail.max_total_attachment_bytes,
        allowed_extensions=config.mail.attachment_extensions,
        feedback=pqrsf_feedback_service,
    )
    pqrsf_comment_guard = providers.Factory(
        AttachmentGuard,
        image_processor=mail_image_processor,
        malware_scanner=pqrsf_malware_scanner,
        allowed_extensions=config.mail.attachment_extensions,
        max_attachments=config.mail.max_attachments,
        max_attachment_bytes=config.mail.max_attachment_bytes,
        max_total_attachment_bytes=config.mail.max_total_attachment_bytes,
    )
    pqrsf_comment_service = providers.Factory(
        CaseCommentService,
        tracking=pqrsf_tracking_service,
        emails=pqrsf_email_repository,
        notifications=pqrsf_notification_repository,
        storage=storage,
        guard=pqrsf_comment_guard,
        cc_domains=config.mail.cc_domains,
    )
    pqrsf_forward_service = providers.Factory(
        CustomerForwardService,
        tracking=pqrsf_tracking_service,
        emails=pqrsf_email_repository,
        notifications=pqrsf_notification_repository,
        noreply_sender=config.mail.noreply_sender,
        mailbox=config.mail.pqrsf_inbox,
    )
    pqrsf_supplier_contact_repository = providers.Factory(
        SqlAlchemySupplierContactRepository, database=database
    )
    # --- Improvement opportunity (OM, P.06): template in the bucket, Word and PDF ---
    pqrsf_improvement_repository = providers.Factory(
        SqlAlchemyImprovementRepository, database=database
    )
    pdf_converter = providers.Singleton(
        GotenbergConverter,
        base_url=config.gotenberg.url,
        timeout_seconds=config.gotenberg.timeout,
    )
    pqrsf_om_service = providers.Factory(
        ImprovementOpportunityService,
        tracking=pqrsf_tracking_service,
        requests=pqrsf_repository,
        repository=pqrsf_improvement_repository,
        storage=storage,
        renderer=providers.Singleton(DocxTemplateRenderer),
        converter=pdf_converter,
        signers=providers.Factory(
            DirectorySigner, employees=directory_repository, people=user_directory
        ),
        managers=pqrsf_manager_policy,
    )
    # --- Action plans (P.04.F.02), fed by the PQRSF cases ---
    action_plan_repository = providers.Factory(SqlAlchemyActionPlanRepository, database=database)
    action_plan_policy = providers.Factory(
        PermissionPolicy, areas=area_service, area_slug=None, module="planes-accion"
    )
    action_plan_service = providers.Factory(
        ActionPlanService,
        repository=action_plan_repository,
        policy=action_plan_policy,
        cases=providers.Factory(
            PqrsfCaseSource, tracking=pqrsf_tracking_service, requests=pqrsf_repository
        ),
        profiles=providers.Factory(
            DirectorySigner, employees=directory_repository, people=user_directory
        ),
        storage=storage,
        guard=pqrsf_comment_guard,
        bell=notification_service,
        area_slug=config.pqrsf.manager_area_slug,
    )
    pqrsf_supplier_message_service = providers.Factory(
        SupplierMessageService,
        tracking=pqrsf_tracking_service,
        files=pqrsf_comment_service,
        emails=pqrsf_email_repository,
        notifications=pqrsf_notification_repository,
        requests=pqrsf_repository,
        contacts=pqrsf_supplier_contact_repository,
        wake=pqrsf_wakeup.provided.set,
        oms=pqrsf_om_service,
    )
    pqrsf_case_attachment_service = providers.Factory(
        CaseAttachmentService,
        emails=pqrsf_email_repository,
        access=pqrsf_tracking_service,
        storage=storage,
    )
    pqrsf_item_delivery_repository = providers.Factory(
        SqlAlchemyItemDeliveryRepository, database=database
    )
    pqrsf_item_delivery_service = providers.Factory(
        ItemDeliveryService,
        tracking=pqrsf_tracking_service,
        deliveries=pqrsf_item_delivery_repository,
        certificates=pqrsf_certificate_repository,
        emails=pqrsf_email_repository,
        notifications=pqrsf_notification_repository,
        storage=storage,
        guard=pqrsf_comment_guard,
        area_slug=config.pqrsf.certificates_area_slug,
        wake=pqrsf_wakeup.provided.set,
    )
    pqrsf_certificate_service = providers.Factory(
        CertificateService,
        repository=pqrsf_certificate_repository,
        managers=pqrsf_manager_policy,
        area_slug=config.pqrsf.certificates_area_slug,
    )
    pqrsf_delivery_service = providers.Factory(
        DocumentDeliveryService,
        deliveries=pqrsf_delivery_repository,
        certificates=pqrsf_certificate_repository,
        notifications=pqrsf_notification_repository,
        managers=pqrsf_manager_policy,
        people=user_directory,
        enabled=config.pqrsf.notifications_enabled,
        max_emails_per_address=config.pqrsf.document_emails_per_address,
        max_email_bytes=config.pqrsf.document_email_max_bytes,
        wake=pqrsf_wakeup.provided.set,
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
        feedback=pqrsf_feedback_service,
        deliveries=pqrsf_delivery_service,
        products=pqrsf_product_catalog,
        video_inspector=pqrsf_video_inspector,
        max_videos=config.pqrsf.max_videos,
        max_video_bytes=config.pqrsf.max_video_bytes,
        notifications=pqrsf_notification_repository,
        acknowledge=config.pqrsf.notifications_enabled,
        wake=pqrsf_wakeup.provided.set,
    )
