"""Data warehouse monitoring: local copy of the warehouse ``monitoreo`` schema.

One table per view (``dwh_mon_<view>``) with the contract's column names; intervals
are stored in whole seconds (``*_s``). ``cargado_en`` is when the platform last wrote
the row.

- Snapshot views, replaced on every cycle: ``secuencias``, ``secuencia_tablas``,
  ``horario_tipico``, ``estadisticas_secuencia``, ``frescura_por_dominio``,
  ``frescura_tablas`` and ``calidad_ultimo_resultado``.
- ``frescura_resumen`` is appended on every cycle: the newest row is the KPI and the
  rest is its history.
- Run history, upserted by key: ``auditoria_extracciones``, ``auditoria_transformaciones``
  and ``corridas_secuencia``.
- ``dwh_mon_cargas``: one row per load cycle with what each view did.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_dwh_monitoring"
down_revision: str | None = "0022_merge_pqrsf_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_UUID = postgresql.UUID(as_uuid=True)


def _loaded() -> sa.Column:
    return sa.Column("cargado_en", _TZ, nullable=False)


def upgrade() -> None:
    """Create the copy of every view and the load log."""

    op.create_table(
        "dwh_mon_secuencias",
        sa.Column("secuencia", sa.Text, primary_key=True),
        sa.Column("descripcion", sa.Text),
        sa.Column("estado_programacion", sa.Text),
        sa.Column("calendario", sa.Text),
        sa.Column("hora_inicio", sa.Time),
        sa.Column("tope_s", sa.Integer),
        sa.Column("tablas_fase1", sa.Integer),
        sa.Column("tablas_fase2", sa.Integer),
        sa.Column("transformacion", sa.Text),
        sa.Column("ultima_corrida_id", _UUID),
        sa.Column("ultima_estado", sa.Text),
        sa.Column("ultima_inicio", _TZ),
        sa.Column("ultima_datos_listos", _TZ),
        sa.Column("ultima_fin", _TZ),
        sa.Column("ultima_duracion_s", sa.Integer),
        sa.Column("en_curso", sa.Boolean),
        sa.Column("eta_datos_listos_en_curso", _TZ),
        sa.Column("eta_fin_en_curso", _TZ),
        sa.Column("proxima_ejecucion", _TZ),
        sa.Column("eta_datos_listos_proxima", _TZ),
        sa.Column("eta_fin_proxima", _TZ),
        sa.Column("hora_tipica_datos_listos", sa.Time),
        sa.Column("extraccion_mediana_min", sa.Numeric),
        sa.Column("transformacion_mediana_min", sa.Numeric),
        sa.Column("datos_listos_mediana_min", sa.Numeric),
        sa.Column("datos_listos_p90_min", sa.Numeric),
        sa.Column("duracion_mediana_min", sa.Numeric),
        sa.Column("pct_exito", sa.Numeric),
        sa.Column("corridas_medidas", sa.Integer),
        sa.Column("systemd_proximo", _TZ),
        sa.Column("systemd_ultimo", _TZ),
        sa.Column("sincronizado_en", _TZ),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_secuencia_tablas",
        sa.Column("secuencia", sa.Text, primary_key=True),
        sa.Column("fase", sa.SmallInteger, primary_key=True),
        sa.Column("pipeline_name", sa.Text, primary_key=True),
        sa.Column("carril", sa.SmallInteger),
        sa.Column("orden", sa.SmallInteger),
        sa.Column("dominio", sa.Text),
        sa.Column("alcance", sa.Text),
        sa.Column("tabla", sa.Text),
        sa.Column("estrategia", sa.Text),
        sa.Column("habilitada", sa.Boolean),
        sa.Column("ultima_extraccion_ok", _TZ),
        sa.Column("ultimo_estado", sa.Text),
        sa.Column("ultimo_inicio", _TZ),
        sa.Column("ultimo_fin", _TZ),
        sa.Column("ultimo_filas", sa.BigInteger),
        sa.Column("ultimo_error", sa.Text),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_horario_tipico",
        sa.Column("secuencia", sa.Text, primary_key=True),
        sa.Column("calendario", sa.Text, primary_key=True),
        sa.Column("dias_semana", postgresql.ARRAY(sa.SmallInteger)),
        sa.Column("hora_programada", sa.Time),
        sa.Column("hora_inicio_real", sa.Time),
        sa.Column("hora_fin_extraccion", sa.Time),
        sa.Column("hora_datos_listos", sa.Time),
        sa.Column("hora_datos_listos_p90", sa.Time),
        sa.Column("hora_fin", sa.Time),
        sa.Column("corridas_medidas", sa.Integer),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_estadisticas_secuencia",
        sa.Column("secuencia", sa.Text, primary_key=True),
        sa.Column("corridas_medidas", sa.Integer),
        sa.Column("extraccion_mediana_s", sa.Float),
        sa.Column("transformacion_mediana_s", sa.Float),
        sa.Column("datos_listos_mediana_s", sa.Float),
        sa.Column("datos_listos_p90_s", sa.Float),
        sa.Column("duracion_mediana_s", sa.Float),
        sa.Column("duracion_p90_s", sa.Float),
        sa.Column("retraso_inicio_mediana_s", sa.Float),
        sa.Column("extraccion_fin_desde_programada_s", sa.Float),
        sa.Column("datos_listos_desde_programada_s", sa.Float),
        sa.Column("datos_listos_p90_desde_programada_s", sa.Float),
        sa.Column("fin_desde_programada_s", sa.Float),
        sa.Column("pct_exito", sa.Numeric),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_frescura_resumen",
        sa.Column("calculado_en", _TZ, primary_key=True),
        sa.Column("tablas_habilitadas", sa.Integer),
        sa.Column("tablas_deshabilitadas", sa.Integer),
        sa.Column("tablas_programadas", sa.Integer),
        sa.Column("frescas_programadas", sa.Integer),
        sa.Column("pct_fresca_programadas", sa.Numeric),
        sa.Column("frescas_total", sa.Integer),
        sa.Column("pct_fresca_total", sa.Numeric),
        sa.Column("atrasadas", sa.Integer),
        sa.Column("nunca_extraidas", sa.Integer),
        sa.Column("ultima_extraccion_ok", _TZ),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_frescura_por_dominio",
        sa.Column("dominio", sa.Text, primary_key=True),
        sa.Column("tablas", sa.Integer),
        sa.Column("frescas", sa.Integer),
        sa.Column("atrasadas", sa.Integer),
        sa.Column("nunca_extraidas", sa.Integer),
        sa.Column("programadas", sa.Integer),
        sa.Column("pct_fresca", sa.Numeric),
        sa.Column("ultima_extraccion_ok", _TZ),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_frescura_tablas",
        sa.Column("pipeline_name", sa.Text, primary_key=True),
        sa.Column("dominio", sa.Text),
        sa.Column("tabla", sa.Text),
        sa.Column("estrategia", sa.Text),
        sa.Column("programacion", sa.Text),
        sa.Column("secuencias", sa.Text),
        sa.Column("ultima_extraccion_ok", _TZ),
        sa.Column("edad_s", sa.BigInteger),
        sa.Column("edad_horas", sa.Numeric),
        sa.Column("max_staleness_s", sa.BigInteger),
        sa.Column("ultima_pasada_secuencia", _TZ),
        sa.Column("estado_frescura", sa.Text),
        sa.Column("motivo_deshabilitada", sa.Text),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_calidad_ultimo_resultado",
        sa.Column("tipo", sa.Text, primary_key=True),
        sa.Column("nombre", sa.Text, primary_key=True),
        sa.Column("objeto", sa.Text),
        sa.Column("capa", sa.Text),
        sa.Column("estado", sa.Text),
        sa.Column("severidad", sa.Text),
        sa.Column("filas_fallidas", sa.BigInteger),
        sa.Column("ejecutado_en", _TZ),
        sa.Column("duracion_ms", sa.Integer),
        sa.Column("selector", sa.Text),
        sa.Column("run_id", _UUID),
        _loaded(),
    )
    op.create_table(
        "dwh_mon_auditoria_extracciones",
        sa.Column("run_id", _UUID, primary_key=True),
        sa.Column("inicio", _TZ, nullable=False),
        sa.Column("fin", _TZ),
        sa.Column("duracion_s", sa.Integer),
        sa.Column("estado", sa.Text),
        sa.Column("disparo", sa.Text),
        sa.Column("secuencia", sa.Text),
        sa.Column("corrida_id", _UUID),
        sa.Column("dominio", sa.Text),
        sa.Column("tabla", sa.Text),
        sa.Column("pipeline_name", sa.Text),
        sa.Column("estrategia", sa.Text),
        sa.Column("filas_extraidas", sa.BigInteger),
        sa.Column("filas_cargadas", sa.BigInteger),
        sa.Column("filas_origen", sa.BigInteger),
        sa.Column("filas_esperadas", sa.BigInteger),
        sa.Column("conciliacion", sa.Text),
        sa.Column("paginas", sa.Integer),
        sa.Column("ventana_desde", postgresql.JSONB),
        sa.Column("ventana_hasta", postgresql.JSONB),
        sa.Column("batch_id", _UUID),
        sa.Column("error", sa.Text),
        sa.Column("cancelada_por", sa.Text),
        sa.Column("os_hostname", sa.Text),
        sa.Column("os_pid", sa.Integer),
        _loaded(),
    )
    op.create_index(
        "ix_dwh_mon_auditoria_extracciones_inicio", "dwh_mon_auditoria_extracciones", ["inicio"]
    )
    op.create_index(
        "ix_dwh_mon_auditoria_extracciones_secuencia",
        "dwh_mon_auditoria_extracciones",
        ["secuencia", "inicio"],
    )
    op.create_index(
        "ix_dwh_mon_auditoria_extracciones_dominio",
        "dwh_mon_auditoria_extracciones",
        ["dominio", "inicio"],
    )
    op.create_index(
        "ix_dwh_mon_auditoria_extracciones_corrida",
        "dwh_mon_auditoria_extracciones",
        ["corrida_id"],
    )
    op.create_table(
        "dwh_mon_auditoria_transformaciones",
        sa.Column("run_id", _UUID, primary_key=True),
        sa.Column("inicio", _TZ, nullable=False),
        sa.Column("fin", _TZ),
        sa.Column("duracion_s", sa.Integer),
        sa.Column("estado", sa.Text),
        sa.Column("disparo", sa.Text),
        sa.Column("secuencia", sa.Text),
        sa.Column("corrida_id", _UUID),
        sa.Column("selector", sa.Text),
        sa.Column("version_dbt", sa.Text),
        sa.Column("modelos_ok", sa.Integer),
        sa.Column("modelos_error", sa.Integer),
        sa.Column("modelos_omitidos", sa.Integer),
        sa.Column("tests_ok", sa.Integer),
        sa.Column("tests_fallidos", sa.Integer),
        sa.Column("tests_aviso", sa.Integer),
        sa.Column("tests_fallidos_nombres", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("os_hostname", sa.Text),
        sa.Column("os_pid", sa.Integer),
        _loaded(),
    )
    op.create_index(
        "ix_dwh_mon_auditoria_transformaciones_inicio",
        "dwh_mon_auditoria_transformaciones",
        ["inicio"],
    )
    op.create_index(
        "ix_dwh_mon_auditoria_transformaciones_secuencia",
        "dwh_mon_auditoria_transformaciones",
        ["secuencia", "inicio"],
    )
    op.create_table(
        "dwh_mon_corridas_secuencia",
        sa.Column("corrida_id", _UUID, primary_key=True),
        sa.Column("secuencia", sa.Text, nullable=False),
        sa.Column("origen", sa.Text),
        sa.Column("estado", sa.Text),
        sa.Column("programada", _TZ),
        sa.Column("fuera_de_horario", sa.Boolean),
        sa.Column("inicio", _TZ, nullable=False),
        sa.Column("extraccion_fin", _TZ),
        sa.Column("transformacion_inicio", _TZ),
        sa.Column("transformacion_fin", _TZ),
        sa.Column("datos_listos", _TZ),
        sa.Column("fase2_inicio", _TZ),
        sa.Column("fase2_fin", _TZ),
        sa.Column("fin", _TZ),
        sa.Column("extraccion_s", sa.Integer),
        sa.Column("transformacion_s", sa.Integer),
        sa.Column("hasta_datos_listos_s", sa.Integer),
        sa.Column("duracion_s", sa.Integer),
        sa.Column("retraso_inicio_s", sa.Integer),
        sa.Column("extraccion_fin_desde_programada_s", sa.Integer),
        sa.Column("datos_listos_desde_programada_s", sa.Integer),
        sa.Column("fin_desde_programada_s", sa.Integer),
        sa.Column("extracciones", sa.Integer),
        sa.Column("extracciones_fallidas", sa.Integer),
        sa.Column("filas_extraidas", sa.BigInteger),
        sa.Column("transformaciones_fallidas", sa.Integer),
        sa.Column("codigo_salida", sa.Integer),
        sa.Column("os_hostname", sa.Text),
        sa.Column("os_pid", sa.Integer),
        _loaded(),
    )
    op.create_index(
        "ix_dwh_mon_corridas_secuencia_inicio", "dwh_mon_corridas_secuencia", ["inicio"]
    )
    op.create_index(
        "ix_dwh_mon_corridas_secuencia_secuencia",
        "dwh_mon_corridas_secuencia",
        ["secuencia", "inicio"],
    )
    op.create_table(
        "dwh_mon_cargas",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("iniciado_en", _TZ, nullable=False),
        sa.Column("terminado_en", _TZ),
        sa.Column("estado", sa.String(20)),
        sa.Column("error", sa.Text),
        sa.Column("detalle", postgresql.JSONB, nullable=False),
    )
    op.create_index("ix_dwh_mon_cargas_iniciado_en", "dwh_mon_cargas", ["iniciado_en"])


def downgrade() -> None:
    """Drop the copy and the load log."""

    for table in (
        "dwh_mon_cargas",
        "dwh_mon_corridas_secuencia",
        "dwh_mon_auditoria_transformaciones",
        "dwh_mon_auditoria_extracciones",
        "dwh_mon_calidad_ultimo_resultado",
        "dwh_mon_frescura_tablas",
        "dwh_mon_frescura_por_dominio",
        "dwh_mon_frescura_resumen",
        "dwh_mon_estadisticas_secuencia",
        "dwh_mon_horario_tipico",
        "dwh_mon_secuencia_tablas",
        "dwh_mon_secuencias",
    ):
        op.drop_table(table)
