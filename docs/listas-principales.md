# Listas principales

Módulo de BI, **bloqueado a BI**, para las listas que manejan los colaboradores de BI: las
generales, las mayoristas y la de Market. Tiene dos partes:

- **Un tablero con las 14 listas juntas**: una sola tabla, una fila por referencia y lista, que se
  filtra por una o varias listas cuando hace falta. Usa el motor, la revisión por ítem y el staging
  de [Actualización de listas](actualizacion-listas.md) (`kind = 'principal'`).
- **Impuesto licor** de todas las listas: revisión y cambio masivo.

## El tablero

Por debajo, cada lista principal tiene **una** actualización vigente (la última creada): de ahí
salen su costo, sus apoyos, su staging y sus archivos. Cada lista se arma o refresca por separado
(la lista 1 sola tiene unas 7.600 referencias) y el tablero lee las líneas de todas juntas: unas
50 mil filas, filtradas, contadas, ordenadas y paginadas en la base (`board_repository.py`).

- **Sincronización** (tarea `main-lists-sync`, `MainBoardService.sync_all`): arma las 14 listas una
  tras otra. La primera vez crea la actualización de cada lista; después la refresca con SIESA
  conservando precios nuevos, estados y observaciones por SKU. Una lista que falla no detiene las
  demás: el resultado de la corrida dice cuáles fallaron y por qué. Corre:
  - **sola**, tras la carga de `abastecimiento` del DWH (lunes a viernes);
  - **a mano**, con «Armar lista general» (la primera vez) o «Sincronizar con la carga del día»
    (`POST /main/sync`, `manage`). Responde 202 con la corrida en cola; 409 si ya hay una en cola o
    en curso, o si las tareas están apagadas (`JOBS_ENABLED`).

  Es una tarea porque no cabe en una petición: la lista 1 y la 1M tardan más de 30 s solo en leer
  sus últimas ventas (31,7 s y 33 s medidos el 2026-11-13). Dentro de la tarea esa lectura tiene
  180 s. La corrida la hace el usuario de sistema «Sincronización de listas».
- **Apoyos de listas con muchos clientes:** la lista 1 tiene 22.397 NITs, más que el máximo de
  parámetros de una consulta de PostgreSQL (32.767 entre todos). Por encima de 2.000 NITs, los
  apoyos por cliente se filtran en Python en lugar de en la consulta, y los clientes se leen en
  bloques de 5.000.
- **Filtros:** listas (ninguna es todas), texto (SKU o descripción), proveedores, categorías y un
  chip: revisión (`sin_marcar`, `por_validar`, `validada`, `cargada`) o margen (`alza`, `objetivo`,
  `encima`, `alerta`, con ±0,5 puntos). Cada chip trae cuántas filas mostraría con los demás
  filtros; `todos`, el total.
- **Tendencia y sin precio** (2026-11-25), combinables con el chip y los demás filtros, también
  en `/main/keys`:
  - `trend` compara el precio nuevo con el precio actual de la lista:
    - `sube`: el nuevo es mayor;
    - `baja`: el nuevo es menor;
    - `estable`: igual, o sin precio nuevo.
  - Una fila sin precio actual no entra en ninguna tendencia: está en `without_list_price=true`
    (sin precio de lista, o con el precio marcador de $1).
  - Con «Con existencia» encendido (por defecto), es «con existencia y sin precio».
  - Conteos `sube`, `baja`, `estable` y `sin_precio`:
    - son un solo grupo de opciones, así que se cuentan con todos los filtros menos la
      tendencia y «sin precio»;
    - los chips se cuentan con la tendencia y «sin precio» puestos.
- **Con existencia** (`in_stock`, filtro rápido desde el 2026-11-25): encendido por defecto, deja
  fuera lo que la sincronización leyó en 0, como la regla de abajo. Con `in_stock=false` esas filas
  aparecen, también en los conteos y en `/main/keys`. En la UI, la tendencia va en un desplegable y
  «Con existencia» y «Sin precio de lista» son botones que se encienden o se apagan.
- **Reglas del 2026-11-23:**
  - **Solo lo que tiene existencia** (salvo `in_stock=false`): una fila que la sincronización leyó en 0 no aparece en el
    tablero, ni en los conteos ni en «todas las filas del filtro». Una cuyo inventario no se leyó
    (`null`, por ejemplo con el DWH caído) sí aparece.
  - **«Alza» solo desde el 1 %:** una fila bajo el objetivo cuenta como `alza` solo si su precio
    debe subir al menos 1 % sobre el actual (el precio nuevo elegido o, si no hay, el óptimo).
    Si sube menos, cuenta como `objetivo`.
  - **Fecha del alza:** cada fila trae `increase_on`, la fecha de la última alza de precio del
    proveedor para ese SKU (cuándo rige, o cuándo se notificó), si tiene una
    ([alzas-precios.md](alzas-precios.md)).
- **Acciones sobre filas de cualquier lista:** precio nuevo por fila, precio óptimo, mínimo o
  limpiar sobre la selección, el estado de revisión (en la UI se cambia fila por fila, en el chip
  de cada una o en su detalle) y la observación. Cada una va a la actualización de su lista, en la
  versión vigente; si otra persona la cambió en el mismo momento, se intenta una vez más.
- **Un precio nuevo pasa a «Por validar» de inmediato,** a nombre de quien lo puso, si la
  referencia está activa y tiene unidad. Borrar el precio la deja «Sin marcar». Actualización de
  listas ya no revisa por ítem (2026-11-19): se valida entera, como la cotización.
- **Las ediciones solo leen y escriben sus filas,** nunca la lista entera (la lista 1 tiene miles):
  `price_rows` en `QuotationService`, `mark` y `set_note` con `full=False`. Los conteos del
  encabezado se mueven por lo que cambiaron esas filas. Antes cada precio leía la lista completa
  tres veces y tardaba varios segundos en la lista 1. La respuesta de precios y revisión trae las
  filas como quedaron (`rows`), para que la UI las muestre sin releer la página.
- **Compartido:** cualquiera con `manage` en el módulo edita todas las listas, aunque las haya
  armado otra persona; cualquiera con `read` las ve todas.
- **Existencias:** la sincronización guarda la existencia de cada referencia en las bodegas de los
  C.O. de su lista (desde el 2026-11-19, `pricing.stock_scope`; sin C.O., las 17 principales)
  (`quotation_lines.stock_on_hand` y `stock_available`, migración `0060`: 0 sin existencia,
  NULL sin leer). La página la toma de ahí y se puede ordenar por ella (`sort=existencia`, sin
  leer al final). Las filas sin leer (una lista sin sincronizar desde la `0060`) se leen del DWH
  por página; si no responde, salen sin ella (`stock_read: false`).

## Datos generales

| | |
| --- | --- |
| Módulo del catálogo | `listas-principales`, bloqueado a `bi` (`locked=True`: el panel no lo enciende en otra área) |
| Validación | `validacion-listas`, también bloqueado a `bi`, sin pantalla propia: su `manage` valida y carga los ítems |
| Impuesto licor | `impuesto-licor`, también bloqueado a `bi`, sin pantalla propia: su `manage` cambia el impuesto y genera el plano, sin validación |
| Listas | `LIST_UPDATES_MAIN_LISTS` = `1,001,002,003,004,005,009,1M,10T,10U,11N,10N,1DR,35P` (códigos exactos de SIESA) |
| Regional del impuesto licor | `LIQUOR_TAX_COAST_LISTS` = `004,11N,22N,36J,46V` pagan el valor Costa; el resto, el Nacional |
| Listas fuera del impuesto licor | `LIQUOR_TAX_EXCLUDED_LISTS` = `25,EMP,PRM` (2026-11-25): no cuentan en los valores, en lo que no cuadra, en las listas del licor ni en los archivos del cambio masivo |
| Migraciones | `0059_main_lists_review`, `0060_main_lists_stock` |
| Validado contra el DWH | 2026-11-12 ([cotizador-dwh.md](cotizador-dwh.md) §10) |

- **1 y 001 son dos listas distintas:** 1 es GENERAL (28.093 sucursales activas de todos los C.O.)
  y 001 es GENERAL ANTIOQUIA (84).
- **La 11N, no la 11M:** 11N es MAYORISTAS CARTAGENA. 11M es la lista de un solo cliente, sin
  sucursales activas desde 2023, y se descartó el 2026-11-12.
- **1DR no tiene sucursales asignadas:** sale con el aviso `lista_sin_clientes`, margen Lista 1 y
  costo Nacional.

## Quién hace qué

| Acción | Quién |
| --- | --- |
| Ver el tablero, el staging, las cargas y el impuesto licor | `listas-principales: read` (todas las listas) |
| Sincronizar las listas, poner precios, marcar «Por validar», escribir observaciones | `listas-principales: manage` (los colaboradores de BI) |
| Validar ítems, descargar el staging y marcarlo cargado | `validacion-listas: manage` (analistas y líder de BI) y los administradores |
| Cambiar el impuesto licor, descargar sus archivos, marcarlo cargado o anularlo | `impuesto-licor: manage` (los colaboradores puntuales que lo administran) y los administradores |

`actualizacion-listas` nunca abre estas listas, ni para quien valida allá: responde 403. Al revés,
`listas-principales` no toma una lista que no sea principal (422). Como los dos módulos nuevos
están bloqueados a BI, un comercial con `actualizacion-listas` no los recibe nunca.

## Impuesto licor

Un licor (clase de impuesto `99`: llave `0024` nacional o `0011` importado) lleva su impuesto como
un **valor por unidad en cada precio de cada lista** (SIESA `t127`, «impuestos en valor»). El
valor depende de la regional de la lista, y la regional no sale del C.O.: la lista 009 es de la
Costa y paga el Nacional. Por eso es un setting.

### La pantalla

`GET /price-list-updates/liquor-taxes` trae cada producto **activo** con llave de licor (17 el
2026-11-12, con 2.028 precios en listas):

- el valor Nacional (el de la lista 1, si no el más común entre las nacionales) y el Costa (el más
  común entre las de la Costa);
- cada valor distinto con sus listas, por regional;
- los valores del maestro de márgenes;
- lo que no cuadra: `varios_valores` (valores distintos en la misma regional, como el Vino Rosado
  12842 con 5.344 en 24 listas viejas), `sin_valor` (una lista con precio real y sin impuesto) y
  `difiere_maestro`.

Aparte trae los **licores sin llave** (`suspects`): productos activos de un grupo impositivo de
licores (`0503`, `0507`, `0509`) sin la llave de la clase 99. Eran 12 del grupo 0507 el
2026-11-12; el área decide si deben llevarla.

**Listas que no cuentan** (2026-11-25): 25, EMP y PRM (`LIQUOR_TAX_EXCLUDED_LISTS`, se comparan en
mayúsculas). No entran en los valores Nacional y Costa, en lo que no cuadra, en las listas de cada
licor (`lists_with_price`), en el conteo de los licores sin llave, ni en las filas y archivos de un
cambio masivo. La pantalla las recibe en `excluded_lists`.

### Cambio masivo

**No pasa por validación.** Lo administran unos colaboradores puntuales (`impuesto-licor: manage`),
y el plano sale de inmediato:

1. El colaborador escribe los valores Nacional y Costa de uno o más licores y la fecha de activación
   (`POST /liquor-taxes/updates`, mañana si no la da). En ese momento se leen los precios vigentes
   de **todas las listas activas** donde el licor tiene precio (fila de la unidad de inventario) y
   se fijan en el cambio (`export_rows`): unas 2.000 filas para todos los licores. Queda
   `pendiente_carga`.
2. Descarga los archivos (`.../file.xlsx` o `.../file.txt`) con **las dos páginas de SIESA**:
   - `Precios` / `0126`: el precio de cada lista, igual al que tiene, en su unidad;
   - `Impuestos en valor` / `0127`: el valor nuevo, Costa para las listas de la Costa y Nacional
     para el resto, justo después de su precio.

   Un valor de 0 escribe el precio sin registro de impuesto.
3. Carga los archivos en SIESA y marca el cambio cargado (`.../uploaded`). El **maestro de
   márgenes** toma los valores nuevos (impuesto licor Nacional, Costa y grupo).

Un cambio que no se cargó se puede anular (`.../cancel`, con un motivo opcional): queda `devuelta`
(«Anulada») y sus archivos ya no se descargan.

## Endpoints

Todos bajo `/api/v1/price-list-updates`. El impuesto licor lo abren `listas-principales` o
`impuesto-licor`; sus escrituras piden `impuesto-licor: manage`.
La revisión por ítem, el staging y las cargas son los de
[Actualización de listas](actualizacion-listas.md#api) con `kind=principal`.

| Endpoint | Qué hace |
| --- | --- |
| `GET /main` | las 14 listas, armadas o no, con sus conteos, los proveedores y categorías para filtrar, la última sincronización (`sync`) y si las tareas están encendidas (`sync_enabled`) |
| `POST /main/sync` | pone en cola la sincronización de las 14 listas (202); 409 si ya hay una o las tareas están apagadas |
| `GET /main/rows?lists=&q=&suppliers=&categories=&chip=&trend=&without_list_price=&in_stock=&sort=&desc=&page=&size=` | una página del tablero (hasta 200 filas) con los conteos de cada chip, de `sube`, `baja`, `estable` y `sin_precio` |
| `GET /main/keys?…` | todas las filas del filtro (máximo 10.000), para actuar sobre todas |
| `PUT /main/prices` `{changes: [{quotation_id, sku, new_price}]}` | precios nuevos (pasan a `por_validar`); `null` lo borra. Trae las filas como quedaron (`rows`) |
| `POST /main/apply` `{keys, mode}` | `optimo`, `minimo` o `limpiar` sobre las filas (sin `rows`: pueden ser 10.000) |
| `PUT /main/review` `{keys, status}` | marca las filas; trae las filas como quedaron (`rows`) |
| `PUT /main/note` `{quotation_id, sku, note}` | la observación de una fila |
| `GET /liquor-taxes` | la pantalla: licores, valores por lista, licores sin llave, listas de la Costa |
| `GET /liquor-taxes/updates` | cambios, el más nuevo primero |
| `POST /liquor-taxes/updates` `{changes: [{sku, national, coast}], note, activation_date}` | cambia el impuesto y fija los precios: los archivos quedan listos |
| `POST /liquor-taxes/updates/{id}/cancel` `{version, note}` | anula un cambio no cargado |
| `GET /liquor-taxes/updates/{id}/file.xlsx\|txt` | los archivos de SIESA |
| `POST /liquor-taxes/updates/{id}/uploaded` `{version}` | marca cargado y escribe el maestro de márgenes |

## UI

Ruta `/bi/listas-principales`, con las vistas Listas (el tablero), Por cargar e Impuesto licor.
El tablero es `main-lists-board.tsx`; el detalle del ítem y los chips son los de Actualización de
listas. `?lista=10U&q=2537` abre el tablero ya filtrado (así enlaza «Por cargar»).
