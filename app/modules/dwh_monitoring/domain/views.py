"""The read contract of the warehouse ``monitoreo`` schema, view by view.

The platform keeps a local copy of each view under the same column names, so the
screens line up one to one with the contract in the DWH vault
("08 - Observabilidad/Monitoreo — Contrato del esquema monitoreo"). Two changes on
the way in: ``interval`` columns are stored as whole seconds with an ``_s`` suffix,
and ``jsonb`` columns arrive from the driver as text and are parsed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LoadMode(StrEnum):
    """How a view is copied on each cycle."""

    # Small views: read whole and replace the local copy.
    SNAPSHOT = "snapshot"
    # One row per load (``frescura_resumen``): appended, which keeps its history.
    HISTORY = "history"
    # Large views keyed by a run: read from the newest ``inicio`` minus a look-back
    # window and upserted by key; rows still open are re-read by key.
    INCREMENTAL = "incremental"


class Kind(StrEnum):
    """What the copy does to a column value."""

    VALUE = "value"
    INTERVAL = "interval"
    JSON = "json"


@dataclass(frozen=True)
class Column:
    """A column of a view and its local name."""

    source: str
    kind: Kind = Kind.VALUE

    @property
    def local(self) -> str:
        """Local column name: ``<name>_s`` for intervals, the same name otherwise."""

        return f"{self.source}_s" if self.kind is Kind.INTERVAL else self.source


@dataclass(frozen=True)
class ViewSpec:
    """One view of ``monitoreo`` and how the platform copies it."""

    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    mode: LoadMode

    @property
    def source_columns(self) -> tuple[str, ...]:
        """Columns selected from the warehouse."""

        return tuple(column.source for column in self.columns)


def _cols(*names: str, intervals: tuple[str, ...] = (), json: tuple[str, ...] = ()) -> tuple:
    return tuple(
        Column(
            name,
            Kind.INTERVAL if name in intervals else Kind.JSON if name in json else Kind.VALUE,
        )
        for name in names
    )


SECUENCIAS = ViewSpec(
    "secuencias",
    _cols(
        "secuencia",
        "descripcion",
        "estado_programacion",
        "calendario",
        "hora_inicio",
        "tope",
        "tablas_fase1",
        "tablas_fase2",
        "transformacion",
        "ultima_corrida_id",
        "ultima_estado",
        "ultima_inicio",
        "ultima_datos_listos",
        "ultima_fin",
        "ultima_duracion_s",
        "en_curso",
        "eta_datos_listos_en_curso",
        "eta_fin_en_curso",
        "proxima_ejecucion",
        "eta_datos_listos_proxima",
        "eta_fin_proxima",
        "hora_tipica_datos_listos",
        "extraccion_mediana_min",
        "transformacion_mediana_min",
        "datos_listos_mediana_min",
        "datos_listos_p90_min",
        "duracion_mediana_min",
        "pct_exito",
        "corridas_medidas",
        "systemd_proximo",
        "systemd_ultimo",
        "sincronizado_en",
        intervals=("tope",),
    ),
    ("secuencia",),
    LoadMode.SNAPSHOT,
)
SECUENCIA_TABLAS = ViewSpec(
    "secuencia_tablas",
    _cols(
        "secuencia",
        "fase",
        "carril",
        "orden",
        "dominio",
        "alcance",
        "tabla",
        "pipeline_name",
        "estrategia",
        "habilitada",
        "ultima_extraccion_ok",
        "ultimo_estado",
        "ultimo_inicio",
        "ultimo_fin",
        "ultimo_filas",
        "ultimo_error",
    ),
    ("secuencia", "fase", "pipeline_name"),
    LoadMode.SNAPSHOT,
)
HORARIO_TIPICO = ViewSpec(
    "horario_tipico",
    _cols(
        "secuencia",
        "calendario",
        "dias_semana",
        "hora_programada",
        "hora_inicio_real",
        "hora_fin_extraccion",
        "hora_datos_listos",
        "hora_datos_listos_p90",
        "hora_fin",
        "corridas_medidas",
    ),
    ("secuencia", "calendario"),
    LoadMode.SNAPSHOT,
)
ESTADISTICAS_SECUENCIA = ViewSpec(
    "estadisticas_secuencia",
    _cols(
        "secuencia",
        "corridas_medidas",
        "extraccion_mediana_s",
        "transformacion_mediana_s",
        "datos_listos_mediana_s",
        "datos_listos_p90_s",
        "duracion_mediana_s",
        "duracion_p90_s",
        "retraso_inicio_mediana_s",
        "extraccion_fin_desde_programada_s",
        "datos_listos_desde_programada_s",
        "datos_listos_p90_desde_programada_s",
        "fin_desde_programada_s",
        "pct_exito",
    ),
    ("secuencia",),
    LoadMode.SNAPSHOT,
)
FRESCURA_RESUMEN = ViewSpec(
    "frescura_resumen",
    _cols(
        "calculado_en",
        "tablas_habilitadas",
        "tablas_deshabilitadas",
        "tablas_programadas",
        "frescas_programadas",
        "pct_fresca_programadas",
        "frescas_total",
        "pct_fresca_total",
        "atrasadas",
        "nunca_extraidas",
        "ultima_extraccion_ok",
    ),
    ("calculado_en",),
    LoadMode.HISTORY,
)
FRESCURA_POR_DOMINIO = ViewSpec(
    "frescura_por_dominio",
    _cols(
        "dominio",
        "tablas",
        "frescas",
        "atrasadas",
        "nunca_extraidas",
        "programadas",
        "pct_fresca",
        "ultima_extraccion_ok",
    ),
    ("dominio",),
    LoadMode.SNAPSHOT,
)
FRESCURA_TABLAS = ViewSpec(
    "frescura_tablas",
    _cols(
        "dominio",
        "tabla",
        "pipeline_name",
        "estrategia",
        "programacion",
        "secuencias",
        "ultima_extraccion_ok",
        "edad",
        "edad_horas",
        "max_staleness",
        "ultima_pasada_secuencia",
        "estado_frescura",
        "motivo_deshabilitada",
        intervals=("edad", "max_staleness"),
    ),
    ("pipeline_name",),
    LoadMode.SNAPSHOT,
)
CALIDAD_ULTIMO_RESULTADO = ViewSpec(
    "calidad_ultimo_resultado",
    _cols(
        "tipo",
        "nombre",
        "objeto",
        "capa",
        "estado",
        "severidad",
        "filas_fallidas",
        "ejecutado_en",
        "duracion_ms",
        "selector",
        "run_id",
    ),
    ("tipo", "nombre"),
    LoadMode.SNAPSHOT,
)
AUDITORIA_EXTRACCIONES = ViewSpec(
    "auditoria_extracciones",
    _cols(
        "run_id",
        "inicio",
        "fin",
        "duracion_s",
        "estado",
        "disparo",
        "secuencia",
        "corrida_id",
        "dominio",
        "tabla",
        "pipeline_name",
        "estrategia",
        "filas_extraidas",
        "filas_cargadas",
        "filas_origen",
        "filas_esperadas",
        "conciliacion",
        "paginas",
        "ventana_desde",
        "ventana_hasta",
        "batch_id",
        "error",
        "cancelada_por",
        "os_hostname",
        "os_pid",
        json=("ventana_desde", "ventana_hasta"),
    ),
    ("run_id",),
    LoadMode.INCREMENTAL,
)
AUDITORIA_TRANSFORMACIONES = ViewSpec(
    "auditoria_transformaciones",
    _cols(
        "run_id",
        "inicio",
        "fin",
        "duracion_s",
        "estado",
        "disparo",
        "secuencia",
        "corrida_id",
        "selector",
        "version_dbt",
        "modelos_ok",
        "modelos_error",
        "modelos_omitidos",
        "tests_ok",
        "tests_fallidos",
        "tests_aviso",
        "tests_fallidos_nombres",
        "error",
        "os_hostname",
        "os_pid",
    ),
    ("run_id",),
    LoadMode.INCREMENTAL,
)
CORRIDAS_SECUENCIA = ViewSpec(
    "corridas_secuencia",
    _cols(
        "corrida_id",
        "secuencia",
        "origen",
        "estado",
        "programada",
        "fuera_de_horario",
        "inicio",
        "extraccion_fin",
        "transformacion_inicio",
        "transformacion_fin",
        "datos_listos",
        "fase2_inicio",
        "fase2_fin",
        "fin",
        "extraccion_s",
        "transformacion_s",
        "hasta_datos_listos_s",
        "duracion_s",
        "retraso_inicio_s",
        "extraccion_fin_desde_programada_s",
        "datos_listos_desde_programada_s",
        "fin_desde_programada_s",
        "extracciones",
        "extracciones_fallidas",
        "filas_extraidas",
        "transformaciones_fallidas",
        "codigo_salida",
        "os_hostname",
        "os_pid",
    ),
    ("corrida_id",),
    LoadMode.INCREMENTAL,
)

# Load order: the run history first, so a sequence card never points at a run the
# platform has not copied yet.
VIEWS: tuple[ViewSpec, ...] = (
    CORRIDAS_SECUENCIA,
    AUDITORIA_EXTRACCIONES,
    AUDITORIA_TRANSFORMACIONES,
    SECUENCIAS,
    SECUENCIA_TABLAS,
    HORARIO_TIPICO,
    ESTADISTICAS_SECUENCIA,
    FRESCURA_RESUMEN,
    FRESCURA_POR_DOMINIO,
    FRESCURA_TABLAS,
    CALIDAD_ULTIMO_RESULTADO,
)
VIEWS_BY_NAME = {view.name: view for view in VIEWS}

# Every incremental view orders and filters by this column (indexed in the warehouse).
INCREMENTAL_TIME_COLUMN = "inicio"
