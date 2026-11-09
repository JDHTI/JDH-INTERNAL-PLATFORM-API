"""Dependency providers owned by API version 1."""

from dependency_injector import containers, providers

from app.core.config import Settings
from app.infrastructure.data_provider import DataProviderConnection
from app.infrastructure.database import Database
from app.infrastructure.realtime import RealtimeBroker
from app.infrastructure.redis import RedisClient
from app.infrastructure.s3 import S3Storage
from app.modules.app_monitoring.application.board import MonitoringBoardService
from app.modules.app_monitoring.application.service import AppMonitoringService
from app.modules.app_monitoring.infrastructure.prober import HttpProber
from app.modules.app_monitoring.infrastructure.repository import SqlAlchemyApplicationRepository
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import PermissionPolicy, UnrestrictedAreaAccess
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
from app.modules.pqrsf.infrastructure.supplier_contact_repository import (
    SqlAlchemySupplierContactRepository,
)
from app.modules.pqrsf.infrastructure.tracking_repository import SqlAlchemyCaseRepository
from app.modules.projects.application.live import ProjectLiveService
from app.modules.projects.application.service import ProjectService
from app.modules.projects.infrastructure.repository import SqlAlchemyProjectRepository
from app.modules.quotations.application.service import QuotationService
from app.modules.quotations.domain.entities import QuotationPolicy
from app.modules.quotations.infrastructure.dwh_source import WarehouseQuotationSource
from app.modules.quotations.infrastructure.masters import SqlAlchemyQuotationMasterSource
from app.modules.quotations.infrastructure.offer_workbook import CustomerOfferWorkbook
from app.modules.quotations.infrastructure.repository import SqlAlchemyQuotationRepository
from app.modules.quotations.infrastructure.siesa_file import SiesaPriceFileWriter
from app.modules.quotations.infrastructure.template import QuotationTemplateFile
from app.modules.quotations.infrastructure.workbook import PriceListWorkbookWriter
from app.modules.salespeople.application.service import SalesScopeService
from app.modules.salespeople.infrastructure.dwh_roster import WarehouseSalesRoster
from app.modules.salespeople.infrastructure.repository import SqlAlchemyLinkRepository
from app.modules.sites.application.service import SiteService
from app.modules.sites.infrastructure.repository import SqlAlchemySiteRepository
from app.modules.supplier_supports.application.attachments import SupportAttachmentService
from app.modules.supplier_supports.application.bulk import SupportBulkService
from app.modules.supplier_supports.application.profiles import SupportProfileService
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.application.service import SupplierSupportService
from app.modules.supplier_supports.application.special_cases import SpecialCaseService
from app.modules.supplier_supports.infrastructure.attachments_repository import (
    SqlAlchemyAttachmentRepository,
)
from app.modules.supplier_supports.infrastructure.bulk_file import SpreadsheetSupportReader
from app.modules.supplier_supports.infrastructure.document_store import (
    DocumentManagerSupportStore,
)
from app.modules.supplier_supports.infrastructure.profiles_repository import (
    SqlAlchemyProfileRepository,
)
from app.modules.supplier_supports.infrastructure.repository import SqlAlchemySupportRepository
from app.modules.supplier_supports.infrastructure.requests_repository import (
    SqlAlchemySupportRequestRepository,
)
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
        customer_forwarding=providers.Callable(bool, config.mail.noreply_sender),
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
    special_case_manager_policy = providers.Factory(
        PermissionPolicy,
        areas=area_service,
        area_slug=config.supplier_supports.manager_area_slug,
        module="casos-especiales",
    )
    commercial_margin_reader = providers.Singleton(SpreadsheetMarginReader)
    commercial_master_service = providers.Factory(
        CommercialMasterService,
        repository=commercial_master_repository,
        managers=commercial_master_manager_policy,
        margin_reader=commercial_margin_reader,
        max_upload_bytes=config.product_margins.max_upload_bytes,
    )
    master_lookup = providers.Factory(MasterLookup, repository=commercial_master_repository)
    pqrsf_product_catalog = providers.Factory(MasterProductCatalog, masters=master_lookup)
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
        managers=special_case_manager_policy,
    )
    supplier_support_repository = providers.Factory(SqlAlchemySupportRepository, database=database)
    supplier_support_service = providers.Factory(
        SupplierSupportService,
        repository=supplier_support_repository,
        masters=master_lookup,
        managers=supplier_support_manager_policy,
        calendar=pqrsf_calendar,
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
    supplier_support_bulk_service = providers.Factory(
        SupportBulkService,
        supports=supplier_support_service,
        profiles=supplier_support_profile_service,
        reader=supplier_support_file_reader,
        max_upload_bytes=config.supplier_supports.max_upload_bytes,
        max_rows=config.supplier_supports.max_bulk_rows,
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
    quotation_policy = providers.Singleton(
        QuotationPolicy, max_points_below_target=config.quotations.max_points_below_target
    )
    quotation_template_file = providers.Singleton(QuotationTemplateFile)
    quotation_offer_writer = providers.Singleton(CustomerOfferWorkbook)
    quotation_repository = providers.Factory(SqlAlchemyQuotationRepository, database=database)
    quotation_warehouse_source = providers.Factory(
        WarehouseQuotationSource,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.quotations.dwh_timeout,
    )
    quotation_master_source = providers.Factory(SqlAlchemyQuotationMasterSource, database=database)
    quotation_workbook_writer = providers.Singleton(PriceListWorkbookWriter)
    quotation_flat_file_writer = providers.Singleton(
        SiesaPriceFileWriter, company=config.quotations.siesa_company
    )
    # --- Salespeople: who is which SIESA salesperson, and each person's sales scope ---
    salesperson_link_repository = providers.Factory(SqlAlchemyLinkRepository, database=database)
    sales_roster = providers.Factory(
        WarehouseSalesRoster,
        connection=data_provider,
        schema=settings.provided.DWH_SCHEMA,
        timeout=config.quotations.dwh_timeout,
    )
    sales_scope_service = providers.Factory(
        SalesScopeService,
        links=salesperson_link_repository,
        roster=sales_roster,
        users=user_directory,
        access=area_service,
        audit=area_service,
        # BI sees every customer; in Comercial only its leader or responsible does.
        all_area_slugs=providers.List(config.quotations.approver_area_slug),
        leader_area_slugs=providers.List(config.quotations.manager_area_slug),
    )
    quotation_service = providers.Factory(
        QuotationService,
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
        template=quotation_template_file,
        max_upload_bytes=config.quotations.max_upload_bytes,
        offer_writer=quotation_offer_writer,
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
    # --- Background jobs: the master syncs, started when the DWH sequences finish ---
    job_run_repository = providers.Factory(SqlAlchemyJobRunRepository, database=database)
    job_watermarks = providers.Factory(SqlAlchemySequenceWatermarks, database=database)
    job_sequence_source = providers.Factory(WarehouseSequenceSource, connection=data_provider)
    job_handlers = providers.Singleton(
        build_job_handlers,
        masters=master_service.provider,
        commercial_sync=master_sync_service.provider,
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
    pqrsf_supplier_message_service = providers.Factory(
        SupplierMessageService,
        tracking=pqrsf_tracking_service,
        files=pqrsf_comment_service,
        emails=pqrsf_email_repository,
        notifications=pqrsf_notification_repository,
        requests=pqrsf_repository,
        contacts=pqrsf_supplier_contact_repository,
        wake=pqrsf_wakeup.provided.set,
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
