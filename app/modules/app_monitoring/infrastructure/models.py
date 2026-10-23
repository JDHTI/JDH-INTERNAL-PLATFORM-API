"""Monitored applications and the history of their checks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

__all__ = ["AplicativoModel", "ChequeoModel"]

_TZ = DateTime(timezone=True)


class AplicativoModel(Base):
    """A platform the TI area monitors; edited from the screens."""

    __tablename__ = "app_mon_aplicativos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    url_base: Mapped[str] = mapped_column(String(500), nullable=False)
    ruta_health: Mapped[str] = mapped_column(String(300), nullable=False)
    ruta_ui: Mapped[str] = mapped_column(String(300), nullable=False)
    verificar_ui: Mapped[bool] = mapped_column(Boolean, nullable=False)
    habilitado: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verificar_tls: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timeout_s: Mapped[float] = mapped_column(Float, nullable=False)
    umbral_lento_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    creado_en: Mapped[datetime] = mapped_column(_TZ, nullable=False, server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(
        _TZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChequeoModel(Base):
    """One request against an application's health endpoint or UI."""

    __tablename__ = "app_mon_chequeos"
    __table_args__ = (
        Index("ix_app_mon_chequeos_aplicativo", "aplicativo_id", "objetivo", "chequeado_en"),
        Index("ix_app_mon_chequeos_chequeado_en", "chequeado_en"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aplicativo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app_mon_aplicativos.id", ondelete="CASCADE"),
        nullable=False,
    )
    objetivo: Mapped[str] = mapped_column(String(10), nullable=False)
    chequeado_en: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    estado: Mapped[str] = mapped_column(String(10), nullable=False)
    codigo_http: Mapped[int | None] = mapped_column(SmallInteger)
    latencia_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    detalle: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
