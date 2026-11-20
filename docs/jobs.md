# Tareas en segundo plano (jobs)

Las sincronizaciones de los maestros comerciales y el calentamiento de la caché de maestros corren
solos cuando el DWH publica datos frescos, sin horarios fijos en la plataforma. Módulo: `app/modules/jobs`.

## Horario del DWH

El DWH carga por *secuencias* (systemd en su servidor) y publica su estado en
`monitoreo.secuencias`. Una secuencia deja frescas sus vistas de `consumo` en el hito `vistas_fin`
(`ultima_datos_listos`). Horario medido por el equipo del DWH el 2026-11-05 (hora Colombia):

| Secuencia | Días | Arranca | Datos listos (mediana / p90) | Vistas que refresca |
| --- | --- | --- | --- | --- |
| `identidad` | todos | 03:00 | 03:42 / 03:44 | clientes, proveedores, vendedores, supervisores, equipos, regionales, unidades de negocio, centros |
| `reporte_ventas` | lun-sáb | 07:30 | 07:56 / 08:01 | productos, ventas, impuestos, cotizaciones de proveedores |
| `abastecimiento` | lun-vie | 08:30, espera a `reporte_ventas` | 08:56 / 08:58 | listas de precios, inventario, producto-proveedor, compras |
| `reporte_contabilidad` | lun-vie | 09:00, espera a `abastecimiento` | 09:18 / 09:19 | movimientos auxiliares |
| `cartera_pedidos` | lun-sáb | 11:50 | 13:06 / 13:13 | cartera, cuentas por cobrar y pagar, pedidos, recaudos |

Los timers del DWH tienen `Persistent=false`: si el servidor estaba apagado a la hora, la corrida no
se recupera sola. Para eso está el respaldo diario.

## Trabajos

| Tarea | Secuencia | Qué hace |
| --- | --- | --- |
| `masters-warm-identity` | `identidad` | Lee del DWH los maestros de identidad para la caché (`docs/masters.md`) |
| `commercial-sync-identity` | `identidad` | Maestros comerciales: proveedores y clientes |
| `masters-warm-products` | `reporte_ventas` | Lee del DWH el maestro de productos para la caché |
| `commercial-sync-products` | `reporte_ventas` | Maestros comerciales: proveedores comerciales y productos |
| `commercial-sync-supply` | `abastecimiento` | Costo unitario promedio y el puente producto-proveedor |
| `supplier-supports-costs` | `abastecimiento` | Costo DWH de cada apoyo con la regla del cotizador |
| `main-lists-sync` | `abastecimiento` | Arma o refresca las 14 listas principales con SIESA, conservando precios nuevos, estados y observaciones (`docs/listas-principales.md`). También la encola el tablero con `POST /price-list-updates/main/sync` (`manage` del módulo, no solo admin). Va después de los costos |
| `supplier-supports-rollover` | ninguna: **00:05** todos los días | Vence el día anterior el apoyo que reemplaza un programado (`docs/apoyos-proveedores.md`). No lee el DWH; hasta el 2026-11-20 esperaba a `identidad` (~03:42) |

El registro está en `app/modules/jobs/domain/registry.py` y el trabajo de cada uno en
`infrastructure/handlers.py`.

## Cuándo corre cada uno

- **Por el DWH (`trigger=dwh`).** Cada `JOBS_DWH_POLL_SECONDS` (60) el worker lee
  `monitoreo.secuencias` en vivo, no la copia de `dwh_monitoring`, que va 10 minutos atrás. Una
  secuencia con `ultima_estado = success`, sin `en_curso` y con `ultima_datos_listos` más nuevo que el
  último despachado encola sus trabajos. La marca queda en `dwh_sequence_watermarks`, así que un
  reinicio no los repite.
- **Respaldo (`scheduled`).** A las `JOBS_FALLBACK_AT` (10:00) corre lo que el DWH no disparó ese día,
  solo en los días en que corre su secuencia.
- **A hora fija (`scheduled`).** Una tarea sin secuencia y con hora (`at` en el registro y en
  `GET /jobs`) corre todos los días a esa hora local. Si la API arranca después de esa hora y la
  tarea no ha corrido desde entonces, corre al arrancar. Hoy solo `supplier-supports-rollover`, a
  las 00:05.
- **Al arrancar (`startup`).** Cierra como fallidas las corridas que un proceso detenido dejó abiertas,
  y corre lo que tenga datos más frescos que su último éxito.
- **A mano (`manual`).** `POST /jobs/{key}/run`.

## Reglas

- **Una corrida abierta por trabajo.** El índice `uq_job_runs_open` lo garantiza; otra solicitud
  responde 409.
- **De a una.** El worker ejecuta una corrida a la vez y en el orden del registro (identidad, productos,
  abastecimiento): los costos necesitan los productos.
- **Espera al DWH.** Si la secuencia está recargando (dbt borra y recrea las vistas), la corrida espera
  hasta `JOBS_WAIT_FOR_DWH_SECONDS` (30 min) y luego falla.
- **Resultado.** `success`, `partial` (algún maestro falló, por ejemplo una vista que no respondió) o
  `failed`, con el detalle por maestro en `detail`.
- **DWH caído.** El sondeo lo registra en `dwh.error` de `GET /jobs`, conserva las marcas y reintenta
  en el siguiente ciclo.
- **Una instancia.** Como los demás workers, asume una sola instancia de la API.

## Endpoints (`/jobs`)

| Método y ruta | Quién | Uso |
| --- | --- | --- |
| `GET /jobs` | admin o moderador | `{enabled, poll_seconds, fallback_at, timezone, dwh: {last_poll_at, error}, items}`. Cada tarea trae `last_run`, `last_success_at`, `next_expected_at` (la ETA del DWH), `sequence_state` y `at` (la hora fija, `HH:MM`, o `null`) |
| `GET /jobs/runs?job=&limit=` | admin o moderador | Historial, más reciente primero |
| `POST /jobs/{key}/run` | admin | 202 `{run_id}`; 404 si no existe; 409 si `JOBS_ENABLED=false` o ya hay una abierta |

## Configuración

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `JOBS_ENABLED` | `false` | Enciende el worker |
| `JOBS_DWH_POLL_SECONDS` | `60` | Intervalo del sondeo, mínimo 10 |
| `JOBS_FALLBACK_AT` | `10:00` | Hora local del respaldo |
| `JOBS_TIMEZONE` | `America/Bogota` | Zona de esa hora |
| `JOBS_WAIT_FOR_DWH_SECONDS` | `1800` | Espera máxima a una secuencia en curso |

El usuario del DWH (`DWH_USER`) necesita leer `monitoreo`. En producción ya lo lee, porque el
monitoreo del DWH usa el mismo esquema.

## Despliegue

1. Migraciones `0047_jobs` y `0048_masters_live`.
2. `JOBS_ENABLED=true` en `api.env` y reiniciar la API.
3. El primer sondeo encola los cinco trabajos una vez; los comerciales tardan unos minutos y el
   calentamiento de maestros, segundos.
4. Revisar `GET /jobs`.
