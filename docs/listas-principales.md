# Listas principales

Módulo de BI, **bloqueado a BI**, para las listas que manejan los colaboradores de BI: las
generales, las mayoristas y la de Market. Tiene dos partes:

- **Un tablero con las 14 listas juntas**: una sola tabla, una fila por referencia y lista, que se
  filtra por una o varias listas cuando hace falta. Usa el motor, la revisión por ítem y el staging
  de [Actualización de listas](actualizacion-listas.md) (`kind = 'principal'`).
- **Impuesto licor** de todas las listas: revisión y cambio masivo.

## El tablero

Por debajo, cada lista principal tiene **una** actualización vigente (la última creada): de ahí
salen su costo, sus apoyos, su staging y sus archivos. El tablero arma o refresca cada lista por
separado (la lista 1 sola tiene unas 7.600 referencias) y lee las líneas de todas juntas: unas
50 mil filas, filtradas, contadas, ordenadas y paginadas en la base (`board_repository.py`).

- **Armar o refrescar** (`POST /main/lists/{código}/build`): la primera vez crea la actualización de
  la lista; después la refresca con SIESA conservando precios nuevos, estados y observaciones por
  SKU. La UI arma «las que faltan» una tras otra, mostrando el avance.
- **Filtros:** listas (ninguna es todas), texto (SKU o descripción), proveedores, categorías y un
  chip: revisión (`sin_marcar`, `por_validar`, `validada`, `cargada`) o margen (`alza`, `objetivo`,
  `encima`, `alerta`, con ±0,5 puntos). Cada chip trae cuántas filas mostraría con los demás
  filtros; `todos`, el total.
- **Acciones sobre filas de cualquier lista:** precio nuevo por fila, precio óptimo, mínimo o
  limpiar sobre la selección, marcar por validar, validar (quien valida) y desmarcar, y la
  observación. Cada una va a la actualización de su lista, en la versión vigente; si otra persona
  la cambió en el mismo momento, se intenta una vez más.
- **Compartido:** cualquiera con `manage` en el módulo edita todas las listas, aunque las haya
  armado otra persona; cualquiera con `read` las ve todas.
- **Existencias:** cada página trae la existencia de sus filas en las bodegas de su lista (una
  lectura del DWH por lista de la página). Si el DWH no responde, la página sale sin ellas
  (`stock_read: false`).

## Datos generales

| | |
| --- | --- |
| Módulo del catálogo | `listas-principales`, bloqueado a `bi` (`locked=True`: el panel no lo enciende en otra área) |
| Validación | `validacion-listas`, también bloqueado a `bi`, sin pantalla propia: su `manage` valida y carga |
| Listas | `LIST_UPDATES_MAIN_LISTS` = `1,001,002,003,004,005,009,1M,10T,10U,11N,10N,1DR,35P` (códigos exactos de SIESA) |
| Regional del impuesto licor | `LIQUOR_TAX_COAST_LISTS` = `004,11N,22N,36J,46V` pagan el valor Costa; el resto, el Nacional |
| Migración | `0059_main_lists_review` |
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
| Ver las actualizaciones, el staging, las cargas y el impuesto licor | `listas-principales: read` (solo las propias), y quien valida (todas) |
| Crear y editar actualizaciones, marcar «Por validar», escribir observaciones, proponer un cambio del impuesto licor | `listas-principales: manage` (los colaboradores de BI) |
| Validar ítems y cambios del impuesto, descargar sus archivos y marcarlos cargados | `validacion-listas: manage` (analistas y líder de BI) y los administradores |

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

### Cambio masivo

1. Un colaborador propone los valores Nacional y Costa de uno o más licores
   (`POST /liquor-taxes/updates`). Queda `pendiente_validacion`.
2. Quien valida lo valida con una fecha de activación (`.../validate`). En ese momento se leen los
   precios vigentes de **todas las listas activas** donde el licor tiene precio (fila de la unidad
   de inventario) y se fijan en el cambio (`export_rows`). Unas 2.000 filas para todos los licores.
3. Descarga los archivos (`.../file.xlsx` o `.../file.txt`) con **las dos páginas de SIESA**:
   - `Precios` / `0126`: el precio de cada lista, igual al que tiene, en su unidad;
   - `Impuestos en valor` / `0127`: el valor nuevo, Costa para las listas de la Costa y Nacional
     para el resto, justo después de su precio.

   Un valor de 0 escribe el precio sin registro de impuesto.
4. Carga los archivos en SIESA y marca el cambio cargado (`.../uploaded`). El **maestro de
   márgenes** toma los valores nuevos (impuesto licor Nacional, Costa y grupo).

Quien valida puede devolver el cambio con un motivo, y quien lo propuso puede retirarlo
(`.../return`): queda `devuelta`.

## Endpoints

Todos bajo `/api/v1/price-list-updates`, con el gate `listas-principales` para el impuesto licor.
La revisión por ítem, el staging y las cargas son los de
[Actualización de listas](actualizacion-listas.md#api) con `kind=principal`.

| Endpoint | Qué hace |
| --- | --- |
| `GET /main` | las 14 listas, armadas o no, con sus conteos, y los proveedores y categorías para filtrar |
| `POST /main/lists/{código}/build` | arma o refresca una lista |
| `GET /main/rows?lists=&q=&suppliers=&categories=&chip=&sort=&desc=&page=&size=` | una página del tablero (hasta 200 filas) con los conteos de cada chip |
| `GET /main/keys?…` | todas las filas del filtro (máximo 10.000), para actuar sobre todas |
| `PUT /main/prices` `{changes: [{quotation_id, sku, new_price}]}` | precios nuevos; `null` lo borra |
| `POST /main/apply` `{keys, mode}` | `optimo`, `minimo` o `limpiar` sobre las filas |
| `PUT /main/review` `{keys, status}` | marca las filas |
| `PUT /main/note` `{quotation_id, sku, note}` | la observación de una fila |
| `GET /liquor-taxes` | la pantalla: licores, valores por lista, licores sin llave, listas de la Costa |
| `GET /liquor-taxes/updates` | cambios, el más nuevo primero |
| `POST /liquor-taxes/updates` `{changes: [{sku, national, coast}], note}` | propone un cambio |
| `POST /liquor-taxes/updates/{id}/validate` `{version, activation_date}` | valida y fija los precios |
| `POST /liquor-taxes/updates/{id}/return` `{version, note}` | devuelve o retira |
| `GET /liquor-taxes/updates/{id}/file.xlsx\|txt` | los archivos de SIESA |
| `POST /liquor-taxes/updates/{id}/uploaded` `{version}` | marca cargado y escribe el maestro de márgenes |

## UI

Ruta `/bi/listas-principales`, con las vistas Listas (el tablero), Por cargar e Impuesto licor.
El tablero es `main-lists-board.tsx`; el detalle del ítem y los chips son los de Actualización de
listas. `?lista=10U&q=2537` abre el tablero ya filtrado (así enlaza «Por cargar»).
