# Monitoreo del datawarehouse

Módulo del área de TI: **Monitoreo → Datawarehouse**. Muestra las secuencias de
extracción, la frescura de los datos, los horarios con su ETA y la auditoría de
extracciones y transformaciones del DWH.

## De dónde salen los datos

Del mismo DWH y con la misma conexión `DWH_*` de los maestros comerciales. La API
lee **solo** el esquema `monitoreo`: vistas sobre `meta` que ya calculan frescura,
ETA y horario típico. Su contrato, columna por columna, está en el vault DWH-JDH
("08 - Observabilidad/Monitoreo — Contrato del esquema monitoreo") y en la
migración `082_monitoreo_secuencias.sql` del DWH, que manda ante cualquier
diferencia. El usuario de `DWH_USER` necesita `USAGE` y `SELECT` sobre `monitoreo`.

Las pantallas nunca consultan el DWH en una petición. Leen una copia local
(`dwh_mon_*`, migración `0023_dwh_monitoring`) que refresca un ciclo en segundo
plano dentro del proceso de la API:

| Vistas | Carga |
|---|---|
| `corridas_secuencia`, `auditoria_extracciones`, `auditoria_transformaciones` | Incremental por llave. Se relee desde el `inicio` más reciente menos 2 días, y las filas que siguen abiertas (`running`, `abandonada` o sin `fin`) se releen por llave aunque hayan salido de esa ventana. La primera carga toma 90 días. |
| `secuencias`, `secuencia_tablas`, `horario_tipico`, `estadisticas_secuencia`, `frescura_por_dominio`, `frescura_tablas`, `calidad_ultimo_resultado` | Copia completa en cada ciclo. Una respuesta vacía no borra la copia anterior. |
| `frescura_resumen` | Se agrega una fila por ciclo: la más reciente es el KPI y las demás son su histórico. |

Cada ciclo queda en `dwh_mon_cargas`, con su estado (`success`, `partial` o
`failed`) y lo que hizo cada vista. Una vista que falla conserva su última copia.

## Configuración

| Variable | Uso |
|---|---|
| `DWH_MONITORING_SYNC_ENABLED` | Enciende la carga en segundo plano. Apagada por defecto hasta que exista `monitoreo`. |
| `DWH_MONITORING_SYNC_INTERVAL_SECONDS` | Segundos entre ciclos (600). Las secuencias corren a las 03:00, 07:30, 08:30, 09:00 y 11:50. |

Para cargar a mano, o probar la conexión:

```powershell
poetry run python scripts/sync-dwh-monitoring.py
poetry run python scripts/sync-dwh-monitoring.py --only secuencias
```

## Acceso

Miembros del área `ti` y administradores de la plataforma. A los demás la API les
responde 403.

## Endpoints

Prefijo `/api/v1/dwh-monitoring`. Los campos tienen los nombres del contrato, con
dos excepciones: los `interval` llegan en segundos con sufijo `_s` (`tope_s`,
`edad_s`, `max_staleness_s`) y las columnas `time` llegan como `HH:MM:SS` en
America/Bogota. Las fechas van en ISO 8601 con zona.

| Endpoint | Pantalla |
|---|---|
| `GET /overview` | Encabezado: KPI de frescura, secuencias activas y en curso, datos listos de hoy (o su ETA) y estado de la carga |
| `GET /sequences` | Secuencias con estado, horario, última corrida, en curso, próxima ejecución, ETA y `corridas_medidas` |
| `GET /sequences/{secuencia}` | Una secuencia con `tablas`, `horario` y `estadisticas` |
| `GET /sequence-tables?secuencia&dominio&fase` | Qué tablas extrae cada secuencia |
| `GET /freshness` | `resumen` (KPI `pct_fresca_programadas`) y `dominios` |
| `GET /freshness/tables?estado&dominio&programacion&q` | Frescura y última modificación por tabla. Para las atrasadas: `estado=Atrasada&estado=Nunca extraída` |
| `GET /freshness/history?desde&hasta` | % de frescura en el tiempo, un punto por ciclo |
| `GET /schedule?fecha` | Gantt: `tipico` (horario típico) contra `corridas` del día, más `secuencias` con sus ETA |
| `GET /sequence-runs?secuencia&estado&desde&hasta&page&size` | Historial de corridas |
| `GET /extractions?desde&hasta&secuencia&dominio&estado&disparo&q&page&size` | Auditoría de extracciones |
| `GET /transformations?desde&hasta&secuencia&estado&q&page&size` | Auditoría de dbt con modelos y tests |
| `GET /quality?estado&tipo` | Último resultado de cada test y modelo |
| `GET /statistics` | Medianas y p90 detrás de las ETA |
| `GET /sync` / `POST /sync` | Estado de la carga / pedir un ciclo ya (202; 409 si la carga está apagada) |

`desde` y `hasta` son días de Bogotá, inclusivos. Las auditorías siempre filtran
por fecha: si no se envían, toman los últimos 7 días. Las listas paginadas
devuelven `{items, total, page, size}`, con `size` de máximo 500.
