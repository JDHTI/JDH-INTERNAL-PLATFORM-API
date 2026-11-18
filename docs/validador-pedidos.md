# Validador de pedidos retenidos

Revisa el reporte de **pedidos retenidos** de SIESA (en su mayoría retenidos por margen)
contra el [maestro de apoyos](apoyos-proveedores.md) y el
[margen por producto](margenes-productos.md), y dice, línea por línea y pedido por pedido,
cuáles quedan **listos para liberación** y cuáles quedan **pendientes de validación**, con
el motivo. Toda la validación es del lado del servidor.

Módulo `order_validation`. Migraciones `0020`, `0021`, `0023`, `0030` a `0032` y `0035`
(motivo `margen_100`).

## El archivo

`.xlsx`, `.xlsm` o `.csv` (un `.xls` se guarda antes como `.xlsx`). Las columnas se buscan
por su encabezado, en cualquier orden, y el encabezado puede estar debajo de un título.

| Encabezado del reporte | Campo | Obligatorio |
| --- | --- | --- |
| `Nro documento` | documento (el pedido) | sí |
| `Cliente factura` | NIT del cliente (solo dígitos, sin DV) | sí |
| `Referencia` | referencia JDH | sí |
| `Precio unit.` | precio unitario | sí |
| `Margen promedio` | margen de la línea, en puntos (`2.2212` = 2,22 %) | sí |
| `PROVEEDOR` | `359 - LOCERIA COLOMBIANA S.A.` → código comercial `359` | sí |
| `Fecha` | fecha del pedido; define la vigencia de los apoyos | no |
| `Vlr. imp. IMPTOLIC` | impuesto al licor por unidad que ya trae la línea (ver [Impuesto al licor](#impuesto-al-licor)) | no |
| `Cant. pedida`, `CANAL`, `Bodega`, `Lista de precios fact`, `C.O. factura` (también `C.O.`, `CO`, `Centro de operación`), `Estado docto.`, `Razon social cliente factura`, `Nombre vendedor cliente`, `Desc. item`, `Retenido margen` | se guardan y se muestran | no |

Una celda ilegible no descarta la fila: queda vacía, la línea guarda el problema y sale
pendiente con `datos_incompletos`. Límites: `ORDER_VALIDATION_MAX_UPLOAD_MB` (10) y
`ORDER_VALIDATION_MAX_LINES` (10 000).

## Cómo se decide una línea

### Margen de referencia: el del producto para el canal

Cada producto trae un margen por lista de unidad de negocio ([margen por producto](margenes-productos.md)).
Un cliente del maestro usa su lista (`customer_margin_field`, la mayoría de sus sucursales
activas), en este orden:

| Cliente | Margen del producto (`margin_basis`) |
| --- | --- |
| Centro de facturación 200 | Margen Market Apartadó (`market`) |
| Equipo del vendedor «MASIVO FOOD» | Margen Masivo Food (`masivo_food`) |
| Canal `MAYORISTAS Y DISTRIBUIDORES`, `SUBDISTRIBUIDOR` o `SALSAMENTARIAS` | Margen Mayoristas (`mayoristas`) |
| Cualquier otro | Margen Lista 1, Food service (`lista`) |

Masivo food y Market caen en Margen Lista 1 cuando el producto no tiene ese margen. Si la línea
del archivo trae `CANAL`, decide entre Food service y Mayoristas. Un cliente que no está en el
maestro se decide solo por el canal del archivo. Un producto sin margen para esa lista no se puede
comparar y se dice en el detalle.

### Apoyos (reglas v2)

El reporte trae precio y margen, no costo. Con las fórmulas del maestro
(`margen = 1 − costo / precio`) el costo es `precio × (1 − margen)` y el margen que
devuelve un apoyo es

```
margen con apoyo = margen + apoyo / precio = margen + (apoyo / costo) × (1 − margen)
```

Cada apoyo se reduce a **apoyo / costo**, así la unidad del apoyo (caja o unidad) nunca se mezcla
con la del pedido.
- **El costo** es el costo DWH del apoyo (`supplier_support_costs`), el mismo del cotizador, con el
  factor de la familia de lista. Sin él, valen las referencias guardadas en el apoyo.
- **El precio de lista (PL)** es ese costo al margen del producto: `costo / (1 − margen)`.
- Los valores por caja se dividen por el factor de empaque del maestro de productos (vacío = 1).

| `calc_method` | $ Apoyo (÷ costo) |
| --- | --- |
| `pct_costo` | costo × % |
| `pct_precio_lista` | PL × % |
| `valor_unidad`, `valor_precio_lista` | $ por unidad |
| `costo_neto` | costo − $ costo apoyado |
| `precio_neto` (% Margen Garantizado) | `máx(0, (g − m) / (1 − m))` del costo: lo que falta para llegar al margen garantizado `g` desde el margen de la línea `m`. El margen con apoyo queda en `máx(m, g)`. No usa `supplier_value` |
| `margen_minimo` | — (margen de la línea ≥ `min_margin_pct`) |
| `puntos_margen` | — (margen de la línea ≥ margen lista − puntos) |
| `pct_ventas` | no evaluable: el rebate se liquida por periodo |

**Margen objetivo**:
- el mínimo autorizado JDH (`supported_margin_pct`);
- si no, el `target_margin_pct` del apoyo. En un % Margen Garantizado ese es el margen
  garantizado, y la línea siempre llega con el apoyo («pasa derecho», BI 2026-11-18). Si no paga
  nada porque la línea ya está en ese margen o por encima, se cumple igual: no es un apoyo sin
  efecto;
- si no, `min_margin_pct`;
- si no, `list_margin_pct`;
- si no, el margen del producto para el cliente.

Sin ninguno, el apoyo no es evaluable. Si el apoyo tiene `min_quantity` y la cantidad pedida es
menor, no cumple.

**Apoyo sin efecto** (BI, 2026-11-18): un apoyo que en la línea aporta $0 se evalúa y se lista
(`no_effect`, detalle «Apoyo sin efecto: aporta $0 …»), pero **nunca es el motivo de
liberación**. Es el caso de un costo apoyado por encima del costo de hoy, o de un margen
garantizado por debajo del margen de la línea.
- Si la línea alcanza su propio margen, el motivo es `cumple_margen_lista`.
- Si no lo alcanza, el apoyo no la sube y queda `no_cumple_acuerdo`.

**Qué apoyos aplican**: del mismo proveedor comercial (o sin proveedor y con la misma
referencia), no anulados ni desactivados, vigentes **en la fecha del pedido** y con el
alcance de la línea. Un campo de alcance vacío es «todos», igual que en el maestro:

- cliente (`customer_nit`), referencia (`product_sku`) y bodega (`warehouse`): iguales;
- canal: el del archivo o el del cliente en el maestro; `RESTAURANTE` cubre
  `RESTAURANTES`;
- regional y unidad de negocio: las del cliente en el maestro de clientes (la regional es
  su centro de facturación). Con el cliente desconocido, un apoyo restringido a ellas no
  aplica.

**No se aplican** las [condiciones especiales](condiciones-especiales.md) del libro
(`Condiciones Especiales`): algunas no son automatizables. Siguen en el maestro de apoyos
y documentadas, pero el validador las ignora.

### Resultado de la línea (en este orden)

| Motivo | Estado | Cuándo |
| --- | --- | --- |
| `margen_100` | pendiente | el margen de la línea es 100 % (no trae costo); retiene el pedido entero (ver [Producto con margen 100 %](#producto-con-margen-100-)) |
| `datos_incompletos` | pendiente | falta proveedor, precio o margen |
| `falta_impuesto_licor` | pendiente | cumpliría el margen o el apoyo, pero el producto tiene impuesto al licor y la línea no lo trae; manda sobre los dos motivos de abajo |
| `cumple_con_apoyo` | **listo** | un apoyo activo que aplica se cumple; se muestra el de mayor holgura |
| `cumple_margen_lista` | **listo** | el margen de la línea ya alcanza el margen del producto para el canal |
| `acuerdo_en_revision` | pendiente | se cumpliría, pero con un apoyo `pendiente_revision` |
| `no_cumple_acuerdo` | pendiente | hay apoyo y no se cumple (se muestra el más cercano) |
| `acuerdo_no_evaluable` | pendiente | hay apoyo, pero no se puede evaluar por pedido |
| `acuerdo_vencido` | pendiente | un apoyo cubriría la línea, pero no en la fecha del pedido |
| `sin_acuerdo` | pendiente | ningún apoyo cubre la línea |

Cada línea trae además `margin_basis` (`lista`, `mayoristas`, `salsamentarias`, `masivo_food` o
`market`: con cuál margen del producto se comparó) y todos los apoyos evaluados (`checks`). Las líneas se
resumen en dos grupos: **sin apoyo** (`sin_acuerdo`, `without_support_line_count` del pedido) y
**cumplen** (las listas, `ready_line_count`, con su descripción detallada en `detail`).

### Impuesto al licor

Un producto con impuesto al consumo de licor (`IMPTOLIC` del maestro de márgenes) debe llegar
al pedido con el impuesto. El reporte lo trae en la columna `Vlr. imp. IMPTOLIC` de cada línea
(`liquor_tax_value`, por unidad). Cuando **falta** —el producto tiene el impuesto en el maestro y
la línea llega con la columna vacía o en 0— la línea sale con `liquor_tax: true` y
`liquor_tax_amount` (lo que debería traer), el pedido con `has_liquor_tax`, y además queda
**retenida**:

- Una línea que cumple el margen o el apoyo pasa a `pendiente_validacion` con el motivo
  `falta_impuesto_licor` («Falta el impuesto al licor»). Este motivo manda sobre
  `cumple_con_apoyo` y `cumple_margen_lista`. Una línea que ya estaba pendiente por otra razón
  conserva su motivo, y sigue marcada.
- El pedido con una línea así queda pendiente. **El umbral de margen no lo libera**; una
  aprobación manual sí.
- Si la línea ya trae el impuesto, o el producto no lo tiene, no hay bandera de ningún tipo y se
  decide como cualquier otra.
- Un archivo sin la columna (formato anterior) se trata como si el impuesto faltara.
### Margen general del pedido (con apoyos)

Cada pedido trae `margin_pct`: el margen del conjunto de sus líneas, ponderado por valor
(`precio × cantidad`), que equivale a `1 − Σcosto / Σventas`, **con cada línea a su margen con
apoyo** cuando un apoyo activo con dinero la decidió (`pct_costo`, `costo_neto`, `valor_unidad`,
`precio_neto`, `pct_precio_lista`), lo cumpla o no: una línea al 1 % en el archivo que el apoyo
lleva al 22 % suma como 22 %. Un apoyo en revisión, vencido o no evaluable no cuenta, ni las
excepciones de margen mínimo (no traen dinero). Cada línea trae `order_margin_pct`: el margen
con el que suma al pedido. Quedan fuera las líneas sin precio, cantidad o margen y las de margen
100 % (sin costo). `required_margin_pct` es el objetivo ponderado de las líneas que tienen uno.

Antes el pedido se calculaba con el margen del archivo sin apoyos: en «Pedidos Retenidos 2» el
pedido `PV-02592133` daba 0,94 % (1,94 % con el umbral de 1 punto) con todas sus líneas por
encima del 19,97 % con apoyo; ahora da 24,07 %. El cálculo es al leer, así que los archivos ya
subidos lo muestran sin revalidar; sus conteos (`ready_order_count`) se actualizan al revalidar o
al cambiar el umbral.

### Reglas del pedido

Las reglas, con sus valores vigentes, se publican para los colaboradores en
`GET /order-validations/rules` (página «Reglas» del validador). Valores configurables:

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `ORDER_VALIDATION_MAX_POINTS_BELOW_TARGET` | 4 | puntos máximos por debajo del objetivo de una línea |
| `ORDER_VALIDATION_TOLERANCE_MIN_ORDER_MARGIN` | 18 | % mínimo del pedido para la tolerancia |
| `ORDER_VALIDATION_TOLERANCE_MAX_LINES` | 2 | líneas máximas por debajo del objetivo en la tolerancia |

#### Producto con margen 100 %

Una línea con margen 100 % (o más) no trae costo: sale con el motivo y la alerta `margen_100`
(«Margen 100 %, revisar»), no cuenta en el margen del pedido y **el pedido no se libera solo**:
ni por sus líneas ni por su margen. **Sí se puede aprobar a mano**, después de revisarlo; la
línea conserva su motivo y su alerta. Se detecta por el margen del archivo, así que también
aplica a los archivos subidos antes de esta regla (sus líneas dicen `datos_incompletos` hasta
revalidar).

Las alertas (`margen_100`, `bajo_objetivo`, `falta_impuesto_licor`) son para el **desglose** de
cada referencia en el detalle del pedido; en la vista general el pedido solo dice su estado
(«Pendiente de validación»).

#### Línea a más de 4 puntos de su objetivo

Una línea pendiente cuyo margen (con su apoyo) está **más de 4 puntos** por debajo de su margen
objetivo lleva la alerta `bajo_objetivo` y `points_below_target` (los puntos, como fracción), y el
pedido **no se libera por su margen**: ni por el umbral ni por la tolerancia. Solo una persona
puede aprobarlo. Exactamente 4 puntos aún se tolera.

#### Tolerancia: pedido ≥ 18 % con 1 o 2 líneas cerca del objetivo

Un pedido cuyo margen (sin umbral) es **18 % o más** se libera (`release_basis: tolerancia`)
aunque tenga **hasta 2 líneas** por debajo de su objetivo, si ninguna está a más de 4 puntos.
Ejemplo: objetivo 12 %, línea al 8 %, pedido al 18 % o más: pasa. Con 3 líneas por debajo, o
una a más de 4 puntos, queda retenido. Como el umbral, solo aplica a pedidos pendientes por
margen (`sin_acuerdo`, `no_cumple_acuerdo`).

### Alertas

Cada pedido trae `alerts`: `[{code, label, message, row_numbers}]`, y cada línea sus códigos en
`alerts`: `margen_100`, `bajo_objetivo` y `falta_impuesto_licor`. Catálogo en
`GET /order-validations/catalogs` (`alerts`).

### Margen del pedido y umbral

El margen del pedido se evalúa **siempre**, con o sin umbral. Un pedido que solo está pendiente
por margen se libera cuando

```
margen del pedido + umbral  ≥  objetivo del pedido
```

y ninguna de sus líneas está a más de 4 puntos de su objetivo.

El **umbral de margen** de cada archivo (`margin_threshold_points`, en puntos, de 0 a 20, por
defecto 0) siempre se suma al margen de cada pedido, nunca al de una línea. Con umbral `0.5`, un
pedido al 19,5 % frente a un objetivo de 20 % queda listo. Reglas:

- Solo cuenta para pedidos pendientes por margen: líneas `sin_acuerdo` o `no_cumple_acuerdo` con
  un margen contra el cual comparar. Un pedido con una línea pendiente por otra razón (datos
  incompletos, apoyo en revisión o vencido, no evaluable), sin margen de referencia o sin costo
  sigue pendiente: lo decide una persona.
- Las líneas no cambian: conservan su motivo. El pedido trae `margin_with_threshold_pct`
  (`margin_pct` más el umbral, solo con umbral mayor que 0) y `release_basis`.
- El umbral se **prueba** sin guardar con `GET /{id}/orders?margin_threshold_points=0.5` (los
  estados, `counts` y márgenes salen como si el archivo lo tuviera) y se **guarda** con
  `PUT /{id}/margin-threshold`, que no revalida las líneas. También se puede fijar al subir el
  archivo o al revalidar. Los totales del lote (`ready_order_count`) usan el umbral guardado.

### Pedido

Un **pedido** está listo para liberación si **todas** sus líneas lo están, si una persona lo
**aprobó a mano**, si su **margen** (con el umbral) alcanza el objetivo o si entra en la
**tolerancia**; con un producto en margen 100 %, solo a mano. `release_basis` dice cuál:
`lineas`, `manual`, `margen`, `tolerancia`, o `null` mientras está pendiente.

Cuando se libera por `margen`, `released_by_threshold` dice si el margen solo no alcanzaba y lo que
lo liberó fue el umbral (`margin_pct` por debajo de `required_margin_pct`); marca los pedidos que el umbral libera y el margen
solo no.

La lista de pedidos trae además 	hreshold_released_count: cuántos pedidos del archivo libera el
umbral y no liberaría el margen solo. Es un sí/no para una señal visible junto al campo del umbral
(> 0 = el umbral libera algo): se recalcula al probar otro valor con margin_threshold_points y no
depende de la búsqueda ni del filtro de estado.
## Orden

Por defecto (`sort=attention`) se listan primero los pedidos que tienen alguna línea **sin
apoyo** (`sin_acuerdo`), y dentro de cada grupo por **margen del pedido de menor a mayor**
(los que no tienen margen, al final). Las líneas de cada pedido se ordenan igual: sin apoyo
primero y por margen de menor a mayor. `sort=file` conserva el orden del archivo.

## Marcar como liberado

Quien gestiona marca pedidos como **liberados** (`liberado`), por selección y en bloque,
estén pendientes o listos para liberación: o se marcan todos los enviados o ninguno (si uno
no está en el archivo es 404; si uno ya está liberado, 409). Se guarda quién, cuándo y una
nota opcional (máximo 500 caracteres) en `manual_approval`. La marca es del pedido: sus
líneas conservan su estado y motivo, así que `ready_line_count` y el filtro `reason` no
cambian. El estado del pedido pasa a `liberado` (`release_basis: manual`); el filtro
`status=liberado`, `counts.liberado` y `released_order_count` lo cuentan aparte, y ya no
suma en `ready_order_count` ni en `pending_order_count`. **Revalidar conserva las marcas**
(son decisiones de una persona). Quitar la marca devuelve el pedido a lo que diga la
validación. Borrar un archivo borra sus líneas y marcas.

La migración `0049` pasó las aprobaciones manuales que ya existían a `liberado`.

Cada línea guarda todos los apoyos evaluados (`checks`) con su resultado, margen con
apoyo, margen objetivo y una explicación en español, y el apoyo que decidió
(`support_id`), para abrirlo desde la pantalla.

## API (`/api/v1/order-validations`)

Lectura: quien tenga `validador-pedidos: read` en BI o Comercial (además de admin, owner y
moderador). Subir, revalidar, aprobar y borrar: quien tenga `validador-pedidos: manage` en
cualquiera de esas dos áreas, el líder o responsable del área, y los administradores.
`ORDER_VALIDATION_MANAGER_AREA_SLUG` vacío (por defecto) acepta las dos áreas; con un slug, solo esa.
`GET /order-validations/permissions` responde `can_manage` con la misma regla.

| Método y ruta | Uso |
| --- | --- |
| `POST /order-validations` | Subir el archivo (`multipart/form-data`, campo `file` y opcional `margin_threshold_points`); valida todo y devuelve el lote con sus conteos y su umbral |
| `GET /order-validations` | Archivos subidos, más recientes primero (`page`, `size`) |
| `GET /order-validations/{id}` | Un archivo con sus conteos y `margin_threshold_points` |
| `GET /order-validations/{id}/orders` | Pedidos con sus líneas, `manual_approval`, `margin_pct`, `required_margin_pct`, `margin_with_threshold_pct`, `release_basis`, `released_by_threshold`, `threshold_released_count` (de la lista), `without_support_line_count` y `has_liquor_tax`. Filtros: `status`, `reason`, `q` (documento, cliente, proveedor, referencia, vendedor), `operation_center` (C.O. factura del pedido), `sort` (`attention` por defecto, o `file`), `margin_threshold_points` (probar un umbral sin guardarlo), `page`, `size`. `counts` trae los pedidos por estado antes de filtrar por estado. `operation_centers` (`[{code, order_count}]`, por código) lista todos los C.O. del archivo con sus pedidos, sin importar los filtros |
| `POST /order-validations/{id}/revalidate` | Vuelve a validar con los apoyos y márgenes como están ahora. Cuerpo opcional `{"margin_threshold_points": 0.5}`; sin valor conserva el umbral guardado. Conserva las aprobaciones manuales |
| `PUT /order-validations/{id}/margin-threshold` | `{"margin_threshold_points": 0.5}`: guarda el umbral del archivo y recalcula qué pedidos libera, sin revalidar las líneas; conserva las aprobaciones |
| `POST /order-validations/{id}/approvals` | `{"documents": [...], "note": "..."}`: marca como liberados pedidos pendientes o listos, todo o nada; devuelve el archivo con sus conteos (`released_order_count`) |
| `DELETE /order-validations/{id}/orders/{document}/approval` | Quita la marca de liberado (204) |
| `DELETE /order-validations/{id}` | Borra el archivo con sus líneas y aprobaciones (204) |
| `GET /order-validations/catalogs` | Estados, motivos y alertas con su etiqueta |
| `GET /order-validations/rules` | Las reglas que aplica el validador, por secciones (`linea`, `pedido`, `aprobacion`), cada una con `code`, `title`, `description`, `effect` (`libera`, `retiene`, `alerta`, `informa`) y `parameters` con los valores vigentes. Abierta a todo usuario autenticado |
| `GET /order-validations/permissions` | `can_manage` para mostrar u ocultar subir y revalidar |

## Pendiente de definir con el negocio

0. Si alta_impuesto_licor debe quitarle también el motivo a las líneas que ya estaban pendientes por otra razón (hoy conservan el suyo).
1. Si `cumple_margen_lista` debe liberar: hoy se libera la línea cuyo margen ya alcanza el
   margen del producto para el canal.
2. El umbral real con el que SIESA retiene por margen, para usarlo en vez del margen del
   producto.
3. Si un apoyo `pendiente_revision` puede liberar.
4. Si el costo de referencia del apoyo debe compararse con el costo actual (hoy solo se
   usa la proporción del apoyo).
5. Si el margen del pedido debe poder liberar pedidos con líneas sin apoyo (hoy sí, mientras tengan margen de referencia).
7. Cuándo aplicar Margen Masivo Food y Margen Market Apartadó, y el margen de los productos
   cuya nota dice «Margen mínimo» distinto del de lista (por ejemplo RPB: lista 57 %, mínimo
   50 %).
