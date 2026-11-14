"""Controlled registry of module models for Alembic.

Importing this module attaches every table to ``Base.metadata``. Add a module
here when it introduces models, so autogenerate and ``alembic check`` see them.
"""

from app.infrastructure.database import Base
from app.modules.app_monitoring.infrastructure import models as app_monitoring_models
from app.modules.areas.infrastructure import models as areas_models
from app.modules.commercial_masters.infrastructure import models as commercial_masters_models
from app.modules.directory.infrastructure import models as directory_models
from app.modules.documents.infrastructure import models as documents_models
from app.modules.dwh_monitoring.infrastructure import models as dwh_monitoring_models
from app.modules.dwh_publish.infrastructure import models as dwh_publish_models
from app.modules.identity.infrastructure import models as identity_models
from app.modules.jobs.infrastructure import models as jobs_models
from app.modules.masters.infrastructure import models as masters_models
from app.modules.notifications.infrastructure import models as notifications_models
from app.modules.order_validation.infrastructure import models as order_validation_models
from app.modules.pqrsf.infrastructure import models as pqrsf_models
from app.modules.projects.infrastructure import models as projects_models
from app.modules.quotations.infrastructure import models as quotations_models
from app.modules.salespeople.infrastructure import models as salespeople_models
from app.modules.sites.infrastructure import models as sites_models
from app.modules.supplier_supports.infrastructure import models as supplier_supports_models
from app.modules.territory.infrastructure import models as territory_models

__all__ = [
    "Base",
    "app_monitoring_models",
    "areas_models",
    "commercial_masters_models",
    "directory_models",
    "documents_models",
    "dwh_monitoring_models",
    "dwh_publish_models",
    "identity_models",
    "jobs_models",
    "masters_models",
    "notifications_models",
    "order_validation_models",
    "pqrsf_models",
    "projects_models",
    "quotations_models",
    "salespeople_models",
    "sites_models",
    "supplier_supports_models",
    "territory_models",
]
