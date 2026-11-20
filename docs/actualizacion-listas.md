# Actualización de listas

Módulo para actualizar una **lista de precios vigente de SIESA**. Usa el motor del
[cotizador](cotizador.md) (costo, margen objetivo, regla general, Excel y plano), con cuatro
diferencias:

- **Origen de las líneas.** Las líneas salen de la lista, no de las ventas de un cliente.
- **Sin oferta.** No hay oferta al cliente, cotización con cantidades ni productos solo para
  cotización.
- **Siempre actualización.** Los archivos actualizan siempre esa misma lista.
- **Validación completa, como la cotización** (desde el 2026-11-19). La actualización se envía a
  validación entera, quien valida la aprueba, se descargan sus archivos y se marca cargada (ver
  [Flujo de validación](#flujo-de-validación)). La revisión por ítem y el staging quedan solo para
  [Listas principales](listas-principales.md).

Las **listas principales** (`LIST_UPDATES_MAIN_LISTS`: 1, 001, 002, 003, 004, 005, 009, 1M, 10T,
10U, 11N, 10N, 1DR y 35P) no se actualizan aquí: van por [Listas principales](listas-principales.md),
un módulo bloqueado a BI con el mismo motor (`kind = 'principal'`).

## Datos generales

- **Módulo del catálogo:** `actualizacion-listas`. Por defecto está activo en `bi`; un
  administrador lo activa en otras áreas desde el panel.
- **Migraciones:** `0058_list_updates` (el tipo `lista`) y `0059_main_lists_review` (revisión por
  ítem, staging, cargas, el tipo `principal` y el impuesto licor).
- **Datos del DWH:** se validaron contra el DWH el 2026-11-12. Ver
  [cotizador-dwh.md](cotizador-dwh.md) §4 para las vistas `listas_precios` y
  `precios_lista_vigentes`, y §10 para existencias, historial y licores.

## Dónde se guarda

Una actualización es una fila de `quotations` con `kind = 'lista'` (o `principal`), y comparte
endpoints con el cotizador (`/api/v1/quotations`). Lo que solo tienen las actualizaciones va en
`/api/v1/price-list-updates`. El servicio revisa cada cotización contra el módulo de su tipo, así
que el permiso de un módulo no abre las del otro.

| Campo | Valor en una actualización |
| --- | --- |
| `kind` | `lista`, o `principal` en Listas principales (`cotizacion` en el cotizador) |
| `status` | siempre `borrador`: el estado está en cada ítem |
| línea `review_status` | `sin_marcar`, `por_validar` o `validada`, con `reviewed_by` y `reviewed_at` |
| línea `note` | la observación del ítem, hasta 500 caracteres |
| línea `load_id` | la carga (`price_list_loads`) que llevó el precio validado a SIESA |
| `source` | `cliente` (se eligió un cliente y se tomó su lista) o `lista` (por código o nombre) |
| `price_list_id`, `price_list_name` | la lista que se actualiza |
| `customer_nit`, `customer_name` | si se creó por cliente, el cliente; si no, `""` y el nombre de la lista |
| `list_action` | siempre `actualizacion` |
| `window_months` | 12, la ventana de la última venta |
| línea `origin` | `lista` (tiene precio vigente en la lista) o `agregado` |
| línea `last_price` | el **precio vigente de la lista** por unidad de inventario: contra él se calculan el margen actual, la variación y la alerta bajo objetivo |
| línea `last_sale_price`, `last_sale_cost` | precio neto y costo de ventas unitario de la última venta, con su `last_sale_date` y `last_document` |
| línea `support_options` | todos los apoyos de los clientes de la lista, cada uno con su precio mínimo y óptimo. `customer_nit` dice que es de un cliente; `customer_group`, que es de un grupo empresarial (2026-11-20); los dos vacíos, que es general |

## Cómo se arma

1. **Lista** (`POST /quotations/list-updates`), por una de estas dos vías:
   - `{price_list_id}`: el código de la lista, que el buscador de la UI encuentra por código o
     por nombre. El código se compara en mayúsculas contra el maestro y desde ahí se usa el
     código guardado, porque hay listas reales con código en minúscula.
   - `{customer_nit}`: la lista que SIESA le tiene asignada al cliente, por mayoría entre sus
     sucursales activas, como en el cotizador.
   - Un cliente sin lista responde 422. Una lista que no existe responde 404.
2. **Líneas:** cada referencia con precio vigente en la lista
   (`consumo.precios_lista_vigentes`, una fila por SKU, la de la unidad de inventario).
   - Se lee por el código exacto. Con `trim` se pierde el índice: 316 ms en vez de 35 ms.
   - Un precio de $1 o menos es un marcador de SIESA, como las 101 referencias de la lista 1 y
     la lista `PRM`. Queda sin precio actual y con el problema `precio_marcador`.
   - Máximo `LIST_MAX_LINES` = 10.000 referencias. La lista 1 tiene unas 7.600.
3. **Contexto:** se toman todas las sucursales asignadas a la lista (`consumo.clientes`) y se
   combinan igual que las de un cliente.
   - El canal de la mayoría decide el margen objetivo.
   - Los CO de facturación deciden el costo: gana un CO de la Costa, y con varios CO, si la lista
     no tiene bodegas propias, el costo es Nacional.
   - No hay vendedor.
   - Avisos:
     - `lista_compartida`: más de un NIT activo en la lista.
     - `lista_sin_clientes`: sin clientes activos; el margen es Lista 1 y el costo Nacional.
     - `lista_sin_precios`: la lista no tiene precios vigentes.
4. **Costo, impuesto y regla:** los mismos del cotizador (`pricing.py`): el mayor entre la
   cotización del proveedor, el inventario de la bodega de la lista y el alza pendiente, con el
   factor de la familia.
5. **Última venta:** sale de `consumo.ventas` en los últimos 12 meses (solo ventas, con cantidad
   y subtotal mayores que cero).
   - Precio = `subtotal / cantidad`. Costo = `costo / cantidad`, porque `ventas.costo` es el
     costo total de la línea en unidad de inventario. Un costo de 0 o menos no se muestra.
   - De quién se toma:
     - Creada por cliente: las ventas a ese cliente, en sus sucursales de la lista.
     - Creada por lista: las ventas a las sucursales activas de la lista.
     - Lista con más de `LIST_SALES_BRANCH_LIMIT` = 1.000 sucursales activas: toda la compañía,
       en dos pasos (el último mes y después el resto de la ventana). Leer las 28 mil
       sucursales de la lista 1 tardaba más de 20 s.
   - Si el DWH no responde a tiempo, la actualización se crea igual con el aviso
     `ultima_venta_no_leida`, y un refresco vuelve a intentar sin perder la última venta que ya
     tenía cada línea.
   - Tiempos medidos el 2026-11-12 en la 10U: 0,7 s por sus clientes y 2,4 s de toda la
     compañía.
6. **Apoyos:** cuentan los de **todos los clientes activos de la lista**.
   - Un apoyo cubre la lista si cubre al menos a uno de sus clientes. Un apoyo de un cliente se
     revisa contra ese cliente; los demás, una vez por cada combinación distinta de canal,
     unidad de negocio y regional entre los clientes.
   - Cada apoyo trae su precio mínimo y su precio óptimo con ese apoyo (`support_options`). Se
     aplica el que deja el **precio mínimo más bajo**; si empatan, el de óptimo más bajo y luego
     el que dura más.
   - Los apoyos en revisión, de una bodega, los rebates y los que no se pueden calcular se
     listan con su motivo y no se aplican.
   - El cotizador sigue aplicando el apoyo que trae más dinero.

## Flujo de validación

Desde el 2026-11-19 una actualización sigue el flujo de la cotización, entera:

1. **Borrador.** Quien edita pone los precios nuevos y la refresca cuando quiera.
2. **Pendiente de validación.** `POST /quotations/{id}/confirm` con `list_action` vacío o
   `actualizacion` (otra acción responde 422: siempre escribe en su misma lista) y la fecha de
   activación (mañana si no se da). Necesita al menos una referencia activa con precio nuevo y
   unidad. No se edita mientras espera; quien la creó la retira, o quien valida la devuelve con
   un motivo (`POST /return`).
3. **Pendiente de carga.** Quien valida el tipo la aprueba (`POST /validate`, sin código: la
   lista es la suya). Se liberan los archivos de SIESA: `GET /quotations/{id}/export.xlsx|txt`
   (las dos hojas, `Precios` e `Impuestos en valor`, o el plano con `0126` y `0127`).
4. **Cargada.** Quien valida la marca cargada (`POST /uploaded`).

Una actualización **pendiente de carga o cargada está cerrada**: sus datos no cambian, ni con
un refresco. Para volver a actualizar la lista se crea una nueva. Las pendientes de validación
salen en el panel de validación del módulo, junto con las cotizaciones de cliente
(`GET /quotations?kind=lista&status=pendiente_validacion&mine=false`).

**Sin revisión por ítem.** `PUT /price-list-updates/{id}/review` responde 422 en una
actualización. El estado de cada ítem (`review_status`) ya no cambia. La observación por ítem
sigue.

**Lo que se validó antes por ítem.** Los ítems que ya estaban en `validada` sin cargar siguen en
el staging de su lista: `GET /staging?kind=lista`, sus archivos y su carga siguen funcionando
para vaciarlo, y `GET /loads?kind=lista` guarda el historial. No entran ítems nuevos.

La revisión por ítem y el staging se describen en [Listas principales](listas-principales.md),
que los conserva.

## Detalle del ítem

- **Existencias por bodega** (`consumo.inventario_fecha`, la foto de SIESA de la mañana),
  consolidadas en las **bodegas vendibles de los C.O. de la actualización**
  (`pricing.stock_scope`, 2026-11-19):
  - las de cada C.O. elegido, sumadas;
  - un C.O. sin fila se lee por las bodegas que SIESA le pone;
  - sin C.O. (*Nacional*), las 17 principales, cualquiera sea la lista. Antes una lista con
    bodegas propias mostraba solo esas.

  Las de obsoletos, tránsito o devoluciones nunca cuentan. La tabla trae el total por
  referencia (`GET /{id}/stock`), y el desglose por C.O. y bodega sale de
  `GET /quotations/{id}/stock-breakdown` (ver [cotizador](cotizador.md)).
- **Historial del precio** (`precios_lista_historico`, últimos 24 meses): versiones del precio de
  la lista por unidad de inventario. Las listas principales solo tienen versiones desde 2026 (la
  lista 1 desde el 2026-08-12): las anteriores ya no están en SIESA. Los precios marcador no
  salen y dos versiones seguidas con el mismo precio se juntan.
- **Precio de venta por mes** (últimos 12 meses): precio neto por unidad de inventario a los
  clientes de la lista (al cliente, si se creó por cliente; a toda la compañía, si la lista tiene
  más de 1.000 sucursales). Toda la compañía se lee mes por mes: 0,6 s contra 7,5 s de una sola
  lectura de 12 meses. Si el DWH no responde, el detalle sale sin ventas (`sales_read: false`).

## Quién hace qué

| Acción | Quién |
| --- | --- |
| Ver, y ver el staging y las cargas | `actualizacion-listas: read` (solo las propias), y quien valida (todas) |
| Crear, editar, enviar a validación, retirar, escribir observaciones | `actualizacion-listas: manage` en cualquier área donde esté activo |
| Validar o devolver, descargar los archivos y marcarla cargada (y vaciar el staging anterior) | `actualizacion-listas: manage` desde el área de `QUOTATIONS_APPROVER_AREA_SLUG` (BI) y los administradores |
| Una **lista principal** | nadie desde aquí (403): va por Listas principales |

## API

- **Crear:**
  - `GET /quotations/price-lists?q=&kind=lista|principal`: listas por código exacto (sin importar
    mayúsculas) o por parte del nombre (sin tildes), activas y más usadas primero.
    - Máximo 50, en una página con la forma de los demás buscadores (`items`, `total`, `page`,
      `size`).
    - Cada una trae `main` (es principal) y `restricted` (es del otro módulo: no se puede elegir).
    - `kind=principal` sin `q` trae todas las listas principales.
    - Solo lee las columnas del maestro: con las columnas de conteo, la búsqueda por nombre
      tardaba 24 s.
  - `POST /quotations/list-updates` con `{customer_nit}` o `{price_list_id}` y `kind`: crea la
    actualización.
- **`kind=lista` (o `principal`) en los endpoints comunes:** `GET /quotations`,
  `GET /quotations/permissions` y `GET /quotations/customers`. Para una actualización, este último
  busca entre todos los clientes, sin cartera.
- **Endpoints del cotizador:** el resto funciona igual por `id`.
  - Responden 422 en una actualización: `PATCH /offer`, `GET /offer.xlsx`, `POST /local-lines` y
    `PUT /lines` con cantidades. `confirm`, `validate`, `return`, `uploaded` y `export` funcionan
    como en la cotización desde el 2026-11-19; solo en una lista principal responden 422.
- **`/price-list-updates`:**

  | Endpoint | Qué hace |
  | --- | --- |
  | `PUT /{id}/review` `{version, skus, status}` | solo listas principales; 422 en una actualización |
  | `PUT /{id}/lines/{sku}/note` `{version, note}` | escribe o borra la observación |
  | `GET /{id}/lines/{sku}/insight` | existencias por bodega, historial del precio y venta por mes |
  | `GET /{id}/stock` | existencias de todas las referencias, sumadas |
  | `GET /staging?kind=` | listas con ítems validados sin cargar |
  | `GET /staging/{lista}/file.xlsx\|txt?kind=&activation_date=` | archivos del staging de la lista (quien valida) |
  | `POST /staging/{lista}/loads` `{kind, line_ids, activation_date}` | marca la carga (quien valida) |
  | `GET /loads?kind=` | últimas cargas |

- **Archivos del staging:** `ACTUALIZACION LISTA <lista>_<AAAAMMDD>.xlsx` y
  `precios-lista<lista>-staging-<AAAAMMDD>.txt`. La fecha de activación es mañana si no se da, y
  nunca antes de hoy.

## UI

Ruta `/<área>/actualizacion-listas` (`src/app/(app)/(workspace)/[area]/actualizacion-listas`).
Usa los componentes del cotizador con `QuotationKindProvider kind="lista"`
(`src/features/quotations/lib/kind.tsx`).

- **Lo que no aparece:** oferta, «Cotizar con cantidades» y «Crear producto».
- **Columna de precio:** dice «Precio actual». La última venta va en un tooltip: «Última venta
  $X», o «Sin venta en los últimos 12 meses».
- **Sin estado por ítem** (2026-11-19): los chips Sin marcar, Por validar, Validada y Cargada
  quedan solo en Listas principales. La actualización tiene el estado de la cotización y su botón
  «Continuar proceso» (envío a validación).
- **Filtros:** por margen actual contra el objetivo, con ±0,5 puntos de
  tolerancia: «Con alza» (bajo el objetivo), «En el objetivo», «Por encima» (para bajar el precio o
  revisar el costo) y «Alerta» (bajo el mínimo, sin costo o con precio marcador).
- **Detalle de la línea:** apoyos con su mínimo y su óptimo, existencias por bodega, la gráfica
  del precio de lista y del precio de venta por mes, y la observación.
- **Por cargar:** las actualizaciones pendientes de carga, con sus archivos y «Marcar cargada»;
  mientras queden, los ítems validados por ítem antes del 2026-11-19 (staging de `kind=lista`).
