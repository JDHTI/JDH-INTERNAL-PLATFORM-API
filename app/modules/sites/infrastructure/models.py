"""SQLAlchemy model for the PQRSF site catalog."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base


class SiteModel(Base):
    """A site where a customer can submit a PQRSF request."""

    __tablename__ = "pqrsf_sites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(4), nullable=False, unique=True)
    # Printed on the improvement opportunity (P.06) sent to a supplier.
    city: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(200))
    om_code: Mapped[str | None] = mapped_column(String(3), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
