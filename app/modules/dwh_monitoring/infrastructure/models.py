"""Local copy of the warehouse ``monitoreo`` views.

One table per view, ``dwh_mon_<view>``, with the contract's column names (see
``domain/views.py``). ``cargado_en`` is when the platform last wrote the row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

__all__ = [
    "AuditoriaExtraccionModel",
    "AuditoriaTransformacionModel",
    "CalidadUltimoResultadoModel",
    "CargaModel",
    "CorridaSecuenciaModel",
    "EstadisticaSecuenciaModel",
    "FrescuraDominioModel",
    "FrescuraResumenModel",
    "FrescuraTablaModel",
    "HorarioTipicoModel",
    "SecuenciaModel",
    "SecuenciaTablaModel",
]

_TZ = DateTime(timezone=True)


def _loaded() -> Mapped[datetime]:
    return mapped_column(_TZ, nullable=False)


class SecuenciaModel(Base):
    __tablename__ = "dwh_mon_secuencias"

    secuencia: Mapped[str] = mapped_column(Text, primary_key=True)
    descripcion: Mapped[str | None] = mapped_column(Text)
    estado_programacion: Mapped[str | None] = mapped_column(Text)
    calendario: Mapped[str | None] = mapped_column(Text)
    hora_inicio: Mapped[time | None] = mapped_column(Time)
    tope_s: Mapped[int | None] = mapped_column(Integer)
    tablas_fase1: Mapped[int | None] = mapped_column(Integer)
    tablas_fase2: Mapped[int | None] = mapped_column(Integer)
    transformacion: Mapped[str | None] = mapped_column(Text)
    ultima_corrida_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ultima_estado: Mapped[str | None] = mapped_column(Text)
    ultima_inicio: Mapped[datetime | None] = mapped_column(_TZ)
    ultima_datos_listos: Mapped[datetime | None] = mapped_column(_TZ)
    ultima_fin: Mapped[datetime | None] = mapped_column(_TZ)
    ultima_duracion_s: Mapped[int | None] = mapped_column(Integer)
    en_curso: Mapped[bool | None] = mapped_column(Boolean)
    eta_datos_listos_en_curso: Mapped[datetime | None] = mapped_column(_TZ)
    eta_fin_en_curso: Mapped[datetime | None] = mapped_column(_TZ)
    proxima_ejecucion: Mapped[datetime | None] = mapped_column(_TZ)
    eta_datos_listos_proxima: Mapped[datetime | None] = mapped_column(_TZ)
    eta_fin_proxima: Mapped[datetime | None] = mapped_column(_TZ)
    hora_tipica_datos_listos: Mapped[time | None] = mapped_column(Time)
    extraccion_mediana_min: Mapped[Decimal | None] = mapped_column(Numeric)
    transformacion_mediana_min: Mapped[Decimal | None] = mapped_column(Numeric)
    datos_listos_mediana_min: Mapped[Decimal | None] = mapped_column(Numeric)
    datos_listos_p90_min: Mapped[Decimal | None] = mapped_column(Numeric)
    duracion_mediana_min: Mapped[Decimal | None] = mapped_column(Numeric)
    pct_exito: Mapped[Decimal | None] = mapped_column(Numeric)
    corridas_medidas: Mapped[int | None] = mapped_column(Integer)
    systemd_proximo: Mapped[datetime | None] = mapped_column(_TZ)
    systemd_ultimo: Mapped[datetime | None] = mapped_column(_TZ)
    sincronizado_en: Mapped[datetime | None] = mapped_column(_TZ)
    cargado_en: Mapped[datetime] = _loaded()


class SecuenciaTablaModel(Base):
    __tablename__ = "dwh_mon_secuencia_tablas"

    secuencia: Mapped[str] = mapped_column(Text, primary_key=True)
    fase: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    pipeline_name: Mapped[str] = mapped_column(Text, primary_key=True)
    carril: Mapped[int | None] = mapped_column(SmallInteger)
    orden: Mapped[int | None] = mapped_column(SmallInteger)
    dominio: Mapped[str | None] = mapped_column(Text)
    alcance: Mapped[str | None] = mapped_column(Text)
    tabla: Mapped[str | None] = mapped_column(Text)
    estrategia: Mapped[str | None] = mapped_column(Text)
    habilitada: Mapped[bool | None] = mapped_column(Boolean)
    ultima_extraccion_ok: Mapped[datetime | None] = mapped_column(_TZ)
    ultimo_estado: Mapped[str | None] = mapped_column(Text)
    ultimo_inicio: Mapped[datetime | None] = mapped_column(_TZ)
    ultimo_fin: Mapped[datetime | None] = mapped_column(_TZ)
    ultimo_filas: Mapped[int | None] = mapped_column(BigInteger)
    ultimo_error: Mapped[str | None] = mapped_column(Text)
    cargado_en: Mapped[datetime] = _loaded()


class HorarioTipicoModel(Base):
    __tablename__ = "dwh_mon_horario_tipico"

    secuencia: Mapped[str] = mapped_column(Text, primary_key=True)
    calendario: Mapped[str] = mapped_column(Text, primary_key=True)
    dias_semana: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger))
    hora_programada: Mapped[time | None] = mapped_column(Time)
    hora_inicio_real: Mapped[time | None] = mapped_column(Time)
    hora_fin_extraccion: Mapped[time | None] = mapped_column(Time)
    hora_datos_listos: Mapped[time | None] = mapped_column(Time)
    hora_datos_listos_p90: Mapped[time | None] = mapped_column(Time)
    hora_fin: Mapped[time | None] = mapped_column(Time)
    corridas_medidas: Mapped[int | None] = mapped_column(Integer)
    cargado_en: Mapped[datetime] = _loaded()


class EstadisticaSecuenciaModel(Base):
    __tablename__ = "dwh_mon_estadisticas_secuencia"

    secuencia: Mapped[str] = mapped_column(Text, primary_key=True)
    corridas_medidas: Mapped[int | None] = mapped_column(Integer)
    extraccion_mediana_s: Mapped[float | None] = mapped_column(Float)
    transformacion_mediana_s: Mapped[float | None] = mapped_column(Float)
    datos_listos_mediana_s: Mapped[float | None] = mapped_column(Float)
    datos_listos_p90_s: Mapped[float | None] = mapped_column(Float)
    duracion_mediana_s: Mapped[float | None] = mapped_column(Float)
    duracion_p90_s: Mapped[float | None] = mapped_column(Float)
    retraso_inicio_mediana_s: Mapped[float | None] = mapped_column(Float)
    extraccion_fin_desde_programada_s: Mapped[float | None] = mapped_column(Float)
    datos_listos_desde_programada_s: Mapped[float | None] = mapped_column(Float)
    datos_listos_p90_desde_programada_s: Mapped[float | None] = mapped_column(Float)
    fin_desde_programada_s: Mapped[float | None] = mapped_column(Float)
    pct_exito: Mapped[Decimal | None] = mapped_column(Numeric)
    cargado_en: Mapped[datetime] = _loaded()


class FrescuraResumenModel(Base):
    """Append-only: the newest row is the KPI, the rest is its history."""

    __tablename__ = "dwh_mon_frescura_resumen"

    calculado_en: Mapped[datetime] = mapped_column(_TZ, primary_key=True)
    tablas_habilitadas: Mapped[int | None] = mapped_column(Integer)
    tablas_deshabilitadas: Mapped[int | None] = mapped_column(Integer)
    tablas_programadas: Mapped[int | None] = mapped_column(Integer)
    frescas_programadas: Mapped[int | None] = mapped_column(Integer)
    pct_fresca_programadas: Mapped[Decimal | None] = mapped_column(Numeric)
    frescas_total: Mapped[int | None] = mapped_column(Integer)
    pct_fresca_total: Mapped[Decimal | None] = mapped_column(Numeric)
    atrasadas: Mapped[int | None] = mapped_column(Integer)
    nunca_extraidas: Mapped[int | None] = mapped_column(Integer)
    ultima_extraccion_ok: Mapped[datetime | None] = mapped_column(_TZ)
    cargado_en: Mapped[datetime] = _loaded()


class FrescuraDominioModel(Base):
    __tablename__ = "dwh_mon_frescura_por_dominio"

    dominio: Mapped[str] = mapped_column(Text, primary_key=True)
    tablas: Mapped[int | None] = mapped_column(Integer)
    frescas: Mapped[int | None] = mapped_column(Integer)
    atrasadas: Mapped[int | None] = mapped_column(Integer)
    nunca_extraidas: Mapped[int | None] = mapped_column(Integer)
    programadas: Mapped[int | None] = mapped_column(Integer)
    pct_fresca: Mapped[Decimal | None] = mapped_column(Numeric)
    ultima_extraccion_ok: Mapped[datetime | None] = mapped_column(_TZ)
    cargado_en: Mapped[datetime] = _loaded()


class FrescuraTablaModel(Base):
    __tablename__ = "dwh_mon_frescura_tablas"

    pipeline_name: Mapped[str] = mapped_column(Text, primary_key=True)
    dominio: Mapped[str | None] = mapped_column(Text)
    tabla: Mapped[str | None] = mapped_column(Text)
    estrategia: Mapped[str | None] = mapped_column(Text)
    programacion: Mapped[str | None] = mapped_column(Text)
    secuencias: Mapped[str | None] = mapped_column(Text)
    ultima_extraccion_ok: Mapped[datetime | None] = mapped_column(_TZ)
    edad_s: Mapped[int | None] = mapped_column(BigInteger)
    edad_horas: Mapped[Decimal | None] = mapped_column(Numeric)
    max_staleness_s: Mapped[int | None] = mapped_column(BigInteger)
    ultima_pasada_secuencia: Mapped[datetime | None] = mapped_column(_TZ)
    estado_frescura: Mapped[str | None] = mapped_column(Text)
    motivo_deshabilitada: Mapped[str | None] = mapped_column(Text)
    cargado_en: Mapped[datetime] = _loaded()


class CalidadUltimoResultadoModel(Base):
    __tablename__ = "dwh_mon_calidad_ultimo_resultado"

    tipo: Mapped[str] = mapped_column(Text, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, primary_key=True)
    objeto: Mapped[str | None] = mapped_column(Text)
    capa: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str | None] = mapped_column(Text)
    severidad: Mapped[str | None] = mapped_column(Text)
    filas_fallidas: Mapped[int | None] = mapped_column(BigInteger)
    ejecutado_en: Mapped[datetime | None] = mapped_column(_TZ)
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    selector: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cargado_en: Mapped[datetime] = _loaded()


class AuditoriaExtraccionModel(Base):
    __tablename__ = "dwh_mon_auditoria_extracciones"
    __table_args__ = (
        Index("ix_dwh_mon_auditoria_extracciones_inicio", "inicio"),
        Index("ix_dwh_mon_auditoria_extracciones_secuencia", "secuencia", "inicio"),
        Index("ix_dwh_mon_auditoria_extracciones_dominio", "dominio", "inicio"),
        Index("ix_dwh_mon_auditoria_extracciones_corrida", "corrida_id"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    inicio: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    fin: Mapped[datetime | None] = mapped_column(_TZ)
    duracion_s: Mapped[int | None] = mapped_column(Integer)
    estado: Mapped[str | None] = mapped_column(Text)
    disparo: Mapped[str | None] = mapped_column(Text)
    secuencia: Mapped[str | None] = mapped_column(Text)
    corrida_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dominio: Mapped[str | None] = mapped_column(Text)
    tabla: Mapped[str | None] = mapped_column(Text)
    pipeline_name: Mapped[str | None] = mapped_column(Text)
    estrategia: Mapped[str | None] = mapped_column(Text)
    filas_extraidas: Mapped[int | None] = mapped_column(BigInteger)
    filas_cargadas: Mapped[int | None] = mapped_column(BigInteger)
    filas_origen: Mapped[int | None] = mapped_column(BigInteger)
    filas_esperadas: Mapped[int | None] = mapped_column(BigInteger)
    conciliacion: Mapped[str | None] = mapped_column(Text)
    paginas: Mapped[int | None] = mapped_column(Integer)
    ventana_desde: Mapped[Any] = mapped_column(JSONB, nullable=True)
    ventana_hasta: Mapped[Any] = mapped_column(JSONB, nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    cancelada_por: Mapped[str | None] = mapped_column(Text)
    os_hostname: Mapped[str | None] = mapped_column(Text)
    os_pid: Mapped[int | None] = mapped_column(Integer)
    cargado_en: Mapped[datetime] = _loaded()


class AuditoriaTransformacionModel(Base):
    __tablename__ = "dwh_mon_auditoria_transformaciones"
    __table_args__ = (
        Index("ix_dwh_mon_auditoria_transformaciones_inicio", "inicio"),
        Index("ix_dwh_mon_auditoria_transformaciones_secuencia", "secuencia", "inicio"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    inicio: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    fin: Mapped[datetime | None] = mapped_column(_TZ)
    duracion_s: Mapped[int | None] = mapped_column(Integer)
    estado: Mapped[str | None] = mapped_column(Text)
    disparo: Mapped[str | None] = mapped_column(Text)
    secuencia: Mapped[str | None] = mapped_column(Text)
    corrida_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    selector: Mapped[str | None] = mapped_column(Text)
    version_dbt: Mapped[str | None] = mapped_column(Text)
    modelos_ok: Mapped[int | None] = mapped_column(Integer)
    modelos_error: Mapped[int | None] = mapped_column(Integer)
    modelos_omitidos: Mapped[int | None] = mapped_column(Integer)
    tests_ok: Mapped[int | None] = mapped_column(Integer)
    tests_fallidos: Mapped[int | None] = mapped_column(Integer)
    tests_aviso: Mapped[int | None] = mapped_column(Integer)
    tests_fallidos_nombres: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    os_hostname: Mapped[str | None] = mapped_column(Text)
    os_pid: Mapped[int | None] = mapped_column(Integer)
    cargado_en: Mapped[datetime] = _loaded()


class CorridaSecuenciaModel(Base):
    __tablename__ = "dwh_mon_corridas_secuencia"
    __table_args__ = (
        Index("ix_dwh_mon_corridas_secuencia_inicio", "inicio"),
        Index("ix_dwh_mon_corridas_secuencia_secuencia", "secuencia", "inicio"),
    )

    corrida_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    secuencia: Mapped[str] = mapped_column(Text, nullable=False)
    origen: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str | None] = mapped_column(Text)
    programada: Mapped[datetime | None] = mapped_column(_TZ)
    fuera_de_horario: Mapped[bool | None] = mapped_column(Boolean)
    inicio: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    extraccion_fin: Mapped[datetime | None] = mapped_column(_TZ)
    transformacion_inicio: Mapped[datetime | None] = mapped_column(_TZ)
    transformacion_fin: Mapped[datetime | None] = mapped_column(_TZ)
    datos_listos: Mapped[datetime | None] = mapped_column(_TZ)
    fase2_inicio: Mapped[datetime | None] = mapped_column(_TZ)
    fase2_fin: Mapped[datetime | None] = mapped_column(_TZ)
    fin: Mapped[datetime | None] = mapped_column(_TZ)
    extraccion_s: Mapped[int | None] = mapped_column(Integer)
    transformacion_s: Mapped[int | None] = mapped_column(Integer)
    hasta_datos_listos_s: Mapped[int | None] = mapped_column(Integer)
    duracion_s: Mapped[int | None] = mapped_column(Integer)
    retraso_inicio_s: Mapped[int | None] = mapped_column(Integer)
    extraccion_fin_desde_programada_s: Mapped[int | None] = mapped_column(Integer)
    datos_listos_desde_programada_s: Mapped[int | None] = mapped_column(Integer)
    fin_desde_programada_s: Mapped[int | None] = mapped_column(Integer)
    extracciones: Mapped[int | None] = mapped_column(Integer)
    extracciones_fallidas: Mapped[int | None] = mapped_column(Integer)
    filas_extraidas: Mapped[int | None] = mapped_column(BigInteger)
    transformaciones_fallidas: Mapped[int | None] = mapped_column(Integer)
    codigo_salida: Mapped[int | None] = mapped_column(Integer)
    os_hostname: Mapped[str | None] = mapped_column(Text)
    os_pid: Mapped[int | None] = mapped_column(Integer)
    cargado_en: Mapped[datetime] = _loaded()


class CargaModel(Base):
    """One load cycle: when it ran, how it ended and what each view did."""

    __tablename__ = "dwh_mon_cargas"
    __table_args__ = (Index("ix_dwh_mon_cargas_iniciado_en", "iniciado_en"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    iniciado_en: Mapped[datetime] = mapped_column(_TZ, nullable=False)
    terminado_en: Mapped[datetime | None] = mapped_column(_TZ)
    estado: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    detalle: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# View name -> local table, used by the generic loader and reader.
MODELS_BY_VIEW: dict[str, type[Base]] = {
    "secuencias": SecuenciaModel,
    "secuencia_tablas": SecuenciaTablaModel,
    "horario_tipico": HorarioTipicoModel,
    "estadisticas_secuencia": EstadisticaSecuenciaModel,
    "frescura_resumen": FrescuraResumenModel,
    "frescura_por_dominio": FrescuraDominioModel,
    "frescura_tablas": FrescuraTablaModel,
    "calidad_ultimo_resultado": CalidadUltimoResultadoModel,
    "auditoria_extracciones": AuditoriaExtraccionModel,
    "auditoria_transformaciones": AuditoriaTransformacionModel,
    "corridas_secuencia": CorridaSecuenciaModel,
}
