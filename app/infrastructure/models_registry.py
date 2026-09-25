"""Controlled registry of module models for Alembic.

Importing this module attaches every table to ``Base.metadata``. Add a module
here when it introduces models, so autogenerate and ``alembic check`` see them.
"""

from app.infrastructure.database import Base
from app.modules.areas.infrastructure import models as areas_models
from app.modules.documents.infrastructure import models as documents_models
from app.modules.identity.infrastructure import models as identity_models
from app.modules.sites.infrastructure import models as sites_models

__all__ = ["Base", "areas_models", "documents_models", "identity_models", "sites_models"]
