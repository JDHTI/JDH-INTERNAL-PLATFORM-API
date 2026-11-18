# Apoyos en el DWH: publicación, trazabilidad y costo

Los apoyos de proveedores se publican en el datawarehouse (esquema `plataforma`) para tenerlos
trazados y consultarlos desde BI y el MCP. La fuente de verdad sigue siendo la base de la
plataforma, que además guarda una copia de cada versión publicada (`dwh_outbox`). Módulos:
`dwh_publish` (transversal: cualquier módulo puede publicar) y `supplier_supports`.
Migración `0057`.

## Cómo se escribe sin afectar la disponibilidad

**Ninguna petición de la plataforma espera al DWH.** Cada cambio de un apoyo deja su versión
completa en `dwh_outbox` dentro de **la misma transacción** que lo guarda (patrón *transactional
outbox*): o quedan los dos o ninguno. Un proceso en segundo plano la envía después. Si el DWH está
caído, se siguen creando, cargando y aprobando apoyos; la cola crece y se vacía cuando vuelve.

Escriben en la cola:

| Acción | `origen` |
| --- | --- |
| Crear a mano / lote JSON | `manual` / `lote` |
| Editar, desactivar, anular | `edicion` / `anulacion` |
| Carga masiva por archivo (con su `carga_id`) | `carga` |
| Apoyo que la carga ya no trae y se pidió vencer | `vencimiento` |
| Panel de solicitudes JDH: aprobar / cancelar el aporte | `aprobacion_jdh` / `cancelacion_jdh` |
| Script de importación del libro | `importacion` |

El publicador (`DwhPublishWorker`):

- Usa **su propia conexión** (`DWH_WRITER_*`, rol `svc_plataforma_apoyos`), con un pool de 1–2
  conexiones, separado del usuario de solo lectura. Así nunca le quita conexiones a las pantallas
  que leen el DWH.
- **Techos** en la sesión: `statement_timeout` 60 s, `lock_timeout` 10 s,
  `idle_in_transaction_session_timeout` 60 s y `application_name = plataforma-apoyos-worker`. Un
  DWH lento falla rápido y se reintenta; nunca se queda esperando con bloqueos.
- Envía **lotes de hasta 1.000** eventos, una transacción del DWH por lote. Antes de llamar al
  DWH ya confirmó en la base local que esos eventos son suyos (*lease* de 5 min), así que no deja
  transacciones locales abiertas esperando la red.
- **Es idempotente.** La tabla actual solo se actualiza si la versión que llega es mayor
  (`WHERE t.version < EXCLUDED.version`); el historial ignora una versión que ya tiene. Un
  reintento o un evento atrasado no cambia nada.
- Distingue dos tipos de falla:
  - **DWH no disponible** (red, credenciales, tablas o permisos aún no creados, tiempo agotado,
    bloqueo): los eventos esperan 2 min y no se les cobra el intento. Tras 3 fallas seguidas el
    publicador se pausa 5 min (*circuit breaker*) en vez de insistir.
  - **Dato rechazado** (una restricción, un tipo): el lote se parte y cada evento va solo, para
    que uno malo no frene a los demás. Al rechazado se le cobra el intento y se reintenta con
    espera exponencial con *jitter* (30 s, 1 min, 2 min… hasta 1 h). Tras 12 intentos queda
    `fallido` hasta que un administrador lo reintente.
- **Horario tranquilo del DWH.** Una carga que escribe más de 5.000 apoyos se publica desde las
  18:00 (Bogotá), fuera de las ventanas del ETL. Las aprobaciones y cargas pequeñas van de
  inmediato. El ETL no toca `plataforma`, así que no hay choque de bloqueos; solo se comparte CPU,
  disco y conexiones.
- **Conciliación diaria** (19:00): compara `(id, version)` de la plataforma con el DWH y vuelve a
  encolar lo que falte o esté atrasado. Si algo se pierde (una tabla recreada), se repara solo.

`dwh_outbox` no se purga: es el respaldo local de cada versión enviada al DWH.

### Estado y operación (`/api/v1/dwh-publish`, solo administradores)

| Método y ruta | Uso |
| --- | --- |
| `GET /dwh-publish` | Pendientes, fallidos y publicados por tema; última pasada, último error, pausa y última conciliación |
| `GET /dwh-publish/failed` | Eventos sin más intentos (`limit` ≤ 500) |
| `POST /dwh-publish/retry` | `{"ids": [...]}` o vacío para todos: vuelven a la cola desde cero; levanta la pausa |
| `POST /dwh-publish/reconcile` | Conciliar ahora |

### Configuración

| Variable | Por defecto | |
| --- | --- | --- |
| `DWH_PUBLISH_ENABLED` | `false` | Arranca el publicador. Apagado, la cola se llena y se envía al encenderlo |
| `DWH_WRITER_HOST`, `_PORT`, `_DATABASE`, `_USER`, `_PASSWORD` | — | Rol de escritura. `_DATABASE` vacío usa `DWH_DATABASE` |
| `DWH_WRITER_SSLMODE` | `require` | `verify-full` si la CA del DWH está en el servidor |
| `DWH_WRITER_POOL_SIZE` | `2` | Máximo 5 (límite del rol) |
| `DWH_WRITER_STATEMENT_TIMEOUT_SECONDS` / `_LOCK_TIMEOUT_SECONDS` | `60` / `10` | |
| `DWH_PUBLISH_POLL_SECONDS`, `_BATCH_SIZE`, `_MAX_ATTEMPTS` | `10`, `1000`, `12` | |
| `DWH_PUBLISH_QUIET_START` / `_END`, `_DEFER_OVER_ROWS` | `18:00` / `03:00`, `5000` | |
| `DWH_PUBLISH_RECONCILE_AT`, `_TIMEZONE` | `19:00`, `America/Bogota` | |

**Puesta en marcha:** (1) migración `0057` en la plataforma; (2) el equipo del DWH aplica
[`dwh/plataforma-apoyos.sql`](dwh/plataforma-apoyos.sql) en `dwh-platform`, crea el rol, sus
techos, `pg_hba` y la vista `consumo.apoyos_proveedor`; (3) se configuran `DWH_WRITER_*`; (4)
`DWH_PUBLISH_ENABLED=true`; (5) `POST /dwh-publish/reconcile` publica todo lo existente.

## Contrato en el DWH (`plataforma`)

El DDL lo aplica el equipo del DWH, no la plataforma: [`dwh/plataforma-apoyos.sql`](dwh/plataforma-apoyos.sql).
La prueba de integración aplica ese mismo archivo a una base de prueba, así que el contrato y el
SQL del publicador se validan juntos.

| Tabla | Llave | Qué guarda |
| --- | --- | --- |
| `apoyo_proveedor` | `id` | Versión vigente de cada apoyo (también los anulados, con `estado = anulado`) |
| `apoyo_proveedor_historial` | `(id, version)` | Todas las versiones, solo se agregan |
| `carga_apoyos` | `id` | Cada carga masiva: archivo, SHA-256, proveedor, usuario, conteos |

Las columnas con tipo son las que BI filtra: proveedor, cliente, SKU, tipo y método, valores,
aporte JDH, vigencia, estado, `origen`, `carga_id`, quién y cuándo, y el costo del DWH con su
fuente y fecha. `datos` (JSONB) lleva el registro completo, así que un campo nuevo **no** requiere
`ALTER TABLE` en el DWH.

Vista sugerida para el consumo (modelo dbt hoja, `consumo.apoyos_proveedor`):

```sql
select a.*,
       case
         when a.estado = 'anulado' then 'anulado'
         when not a.habilitado then 'inactivo'
         when a.vigencia_desde > current_date then 'programado'
         when a.vigencia_hasta < current_date then 'vencido'
         else 'vigente'
       end as estado_vigencia
from {{ source('plataforma', 'apoyo_proveedor') }} a
```

### Reglas v2: borrados, clases aparte y bodegas promocionales (2026-11-17)

- **Borrado** (tópico `supplier_support_delete`): la purga de las clases descontinuadas
  (`scripts/purge-discontinued-supports.py`) encola un evento por apoyo con `version` = la última
  + 1 y descarta en la misma transacción los eventos pendientes de ese apoyo, para que ninguna
  versión vieja llegue después. El publicador llama
  `plataforma.borrar_apoyo_proveedor(id, version, borrado_por, borrado_en)`: función
  `SECURITY DEFINER` del equipo del DWH (propuesta `086_plataforma_apoyos_borrado.sql`, sin
  aplicar) que solo borra las tres clases descontinuadas y agrega la fila `operacion = 'borrado'`
  al historial. El escritor sigue sin `DELETE`. Requiere enmendar ADR-008 (decisión del dueño).
- **Rebate y presupuesto** se publican con `habilitado = false`, así la vista no los muestra
  vigentes sin que el DWH conozca las clases.
- **Grupo de bodega**: SIESA lo tiene (`t151`/`t152`, ya en `staging`); es N:M. El DWH propone
  `consumo.bodegas_grupos` y `bodega_promocional` en `consumo.inventario_fecha` (sin aplicar).
  Mientras tanto la plataforma lleva las 18 bodegas del grupo 23 en
  `vocabulary.PROMO_WAREHOUSES`.
- **Cotización por C.O.**: `t212` tiene `f212_id_co` vacío en el 100 % de las filas; SIESA no
  registra la cotización por C.O. El costo por C.O. solo existe como último costo de compra por
  instalación (`inventario_fecha.ultimo_costo_unit`). Pendiente: confirmar con el usuario qué
  comparación esperaba para Antioquia.

## Costo del DWH (regla del cotizador)

El costo de un apoyo **siempre** sale del DWH, con la regla del cotizador
(`quotations/domain/pricing.py`, skill `lista-precios`):

- **Costo** = el mayor entre la cotización vigente del proveedor y el costo promedio de inventario,
  por unidad de inventario, redondeado hacia arriba. Un costo de $1 o menos no cuenta. Una
  cotización más de 3 veces por encima de la mediana se descarta (error de captura).
- **Bodegas**: las de la lista de precios del cliente (o de su centro de facturación), como al
  cotizarle. Un cliente con varias sucursales activas toma el **mayor** de sus planes. Un apoyo
  para todos los clientes usa la lista 1: el mayor entre sus 17 bodegas.
- **Con el factor de la familia de lista del cliente** (BI, 2026-11-18): el mismo costo del
  cotizador (`pricing.COST_FACTOR`): × 1,004 en Food service, Masivo food, Salsamentarias y
  Market; × 1 en mayoristas. Se redondea hacia arriba y luego se aplica el factor. La familia es
  la de la lista de precios del cliente cuando el skill la nombra; si no, la de su lista de margen
  (`customer_margin_field`). Un apoyo para todos usa la familia general (× 1,004). Ejemplo: NIT
  900635048, SKU 9951: 189.944 × 1,004 = 190.703,78, igual que el cotizador.
  - Este costo con factor es el que usan el maestro (precio de lista, precios apoyados, $ Apoyo y
    margen recompuesto), el `/preview`, el validador de pedidos y `costo_dwh_unidad` en el DWH.

Lo calcula la carga masiva al aplicarse, para sus apoyos, en la misma transacción. Además, el job
`supplier-supports-costs` lo actualiza para todos cuando el DWH publica `abastecimiento` (L–V,
respaldo diario). Se guarda en `supplier_support_costs`; las pantallas no consultan el DWH. Corrida de
prueba (10-nov-2026, base de desarrollo): 13.022 apoyos en 6 s, 12.963 con costo, 8 planes.

En las cifras (`derived`) el costo del DWH reemplaza al costo de referencia cuando se puede llevar
a la unidad del apoyo: por unidad tal cual; por caja, × empaque, salvo cuando el SKU ya es la caja
(Ohra: el SKU es la caja de 18; gana la lectura más cercana al costo de referencia). Si no se puede
(unidad desconocida), se usa el de referencia. `derived.cost_basis` dice cuál (`dwh` o
`referencia`) y `dwh_cost` trae el detalle: plan de bodegas, fuente, cotización, inventario y fecha.

Contra el Excel (13-oct), el costo del DWH coincide en la mediana para Familia, Unilever, Kimberly,
Team, Azteca, Diversey y McCain.
