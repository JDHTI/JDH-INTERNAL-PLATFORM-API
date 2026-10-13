# Validador de pedidos retenidos

Revisa el reporte de **pedidos retenidos** de SIESA (en su mayoría retenidos por margen)
contra el [maestro de apoyos](apoyos-proveedores.md) y el
[margen por producto](margenes-productos.md), y dice, línea por línea y pedido por pedido,
cuáles quedan **listos para liberación** y cuáles quedan **pendientes de validación**, con
el motivo. Toda la validación es del lado del servidor.

Módulo `order_validation`. Migraciones `0020`, `0021` y `0023`.

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
| `Cant. pedida`, `CANAL`, `Bodega`, `Lista de precios fact`, `C.O. factura`, `Estado docto.`, `Razon social cliente factura`, `Nombre vendedor cliente`, `Desc. item`, `Retenido margen` | se guardan y se muestran | no |

Una celda ilegible no descarta la fila: queda vacía, la línea guarda el problema y sale
pendiente con `datos_incompletos`. Límites: `ORDER_VALIDATION_MAX_UPLOAD_MB` (10) y
`ORDER_VALIDATION_MAX_LINES` (10 000).

## Cómo se decide una línea

### Margen de referencia: el del producto para el canal

Cada producto trae un margen por canal ([margen por producto](margenes-productos.md)). La
línea se compara con el que corresponde a su **canal** (el `CANAL` del archivo; si no lo
trae, el del cliente en el maestro):

| Canal | Margen del producto |
| --- | --- |
| `MAYORISTAS Y DISTRIBUIDORES`, `SUBDISTRIBUIDOR` | Margen Mayoristas |
| `SALSAMENTARIAS` | Margen Salsamentarias |
| Cualquier otro | Margen Lista 1 |

Margen Masivo Food y Margen Market Apartadó se cargan y se muestran, pero **ninguna regla
los aplica por ahora**. Un producto sin margen para ese canal no se puede comparar y se
dice en el detalle.

### Apoyos

El reporte trae precio y margen, no costo. Con las fórmulas del maestro
(`margen = 1 − costo / precio`) el costo es `precio × (1 − margen)` y el margen que
devuelve un apoyo es

```
margen con apoyo = margen + apoyo / precio = margen + (apoyo / costo) × (1 − margen)
```

Cada apoyo se reduce a **apoyo / costo** usando solo valores del propio apoyo, así la
unidad del apoyo (caja o unidad) nunca se mezcla con la del pedido:

| `calc_method` | apoyo / costo | Se compara contra |
| --- | --- | --- |
| `pct_costo` | `supplier_value` | margen objetivo |
| `costo_neto` | `1 − supplier_value / reference_cost` | margen objetivo |
| `valor_unidad` | `supplier_value / reference_cost` | margen objetivo |
| `precio_neto` | `(reference_cost − precio × (1 − margen garantizado)) / reference_cost` | margen objetivo |
| `pct_precio_lista` | `supplier_value × lista / costo` de referencia; sin referencias, `supplier_value` sobre el precio del pedido (se queda corto, nunca de más) | margen objetivo |
| `margen_minimo` | — | margen de la línea ≥ `min_margin_pct` |
| `puntos_margen` | — | margen de la línea ≥ margen lista − puntos |
| `pct_ventas` | no evaluable: el rebate se liquida por periodo | — |

**Margen objetivo**: `target_margin_pct` del apoyo; si no, `min_margin_pct`; si no,
`list_margin_pct` del apoyo; si no, el margen del producto para el canal. Sin ninguno, el
apoyo no es evaluable. Si el apoyo tiene `min_quantity` y la cantidad pedida es menor, no
cumple.

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
| `datos_incompletos` | pendiente | falta proveedor, precio o margen, o el margen es 100 % (la línea no trae costo) |
| `cumple_con_apoyo` | **listo** | un apoyo activo que aplica se cumple; se muestra el de mayor holgura |
| `cumple_margen_lista` | **listo** | el margen de la línea ya alcanza el margen del producto para el canal |
| `acuerdo_en_revision` | pendiente | se cumpliría, pero con un apoyo `pendiente_revision` |
| `no_cumple_acuerdo` | pendiente | hay apoyo y no se cumple (se muestra el más cercano) |
| `acuerdo_no_evaluable` | pendiente | hay apoyo, pero no se puede evaluar por pedido |
| `acuerdo_vencido` | pendiente | un apoyo cubriría la línea, pero no en la fecha del pedido |
| `sin_acuerdo` | pendiente | ningún apoyo cubre la línea |

Cada línea trae además `margin_basis` (`lista`, `mayoristas` o `salsamentarias`: con cuál
margen del producto se comparó) y todos los apoyos evaluados (`checks`). Las líneas se
resumen en dos grupos: **sin apoyo** (`sin_acuerdo`, `without_support_line_count` del pedido) y
**cumplen** (las listas, `ready_line_count`, con su descripción detallada en `detail`).

### Impuesto al licor

Un producto con impuesto al consumo de licor (`IMPTOLIC` del maestro de márgenes) puede
haberse montado en el pedido sin él. El reporte trae el impuesto de cada línea en la columna
`Vlr. imp. IMPTOLIC` (`liquor_tax_value`, por unidad). La bandera sale **solo cuando falta**: el
producto tiene el impuesto en el maestro y la línea trae la columna vacía o en 0. Entonces la
línea sale con `liquor_tax: true` y `liquor_tax_amount` (lo que debería traer, por unidad) y el
pedido con `has_liquor_tax`. Si la línea ya trae el impuesto, o el producto no lo tiene, no
hay bandera de ningún tipo. Un archivo sin esa columna (formato anterior) se trata como si el
impuesto faltara. Es solo una **bandera**: no cambia la decisión.

### Margen general del pedido

Cada pedido trae `margin_pct`: el margen del conjunto de sus líneas, ponderado por valor
(`precio × cantidad`), que equivale a `1 − Σcosto / Σventas`. Quedan fuera las líneas sin
precio, cantidad o margen y las de margen 100 % (sin costo). `required_margin_pct` es el
objetivo ponderado de las líneas que tienen uno.

### Margen del pedido y umbral

El margen del pedido se evalúa **siempre**, con o sin umbral. Un pedido que solo está pendiente
por margen se libera cuando

```
margen del pedido + umbral  ≥  objetivo del pedido
```

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
**aprobó a mano** o si su **margen** (con el umbral) alcanza el objetivo. `release_basis` dice
cuál: `lineas`, `manual`, `margen`, o `null` mientras está pendiente.
## Orden

Por defecto (`sort=attention`) se listan primero los pedidos que tienen alguna línea **sin
apoyo** (`sin_acuerdo`), y dentro de cada grupo por **margen del pedido de menor a mayor**
(los que no tienen margen, al final). Las líneas de cada pedido se ordenan igual: sin apoyo
primero y por margen de menor a mayor. `sort=file` conserva el orden del archivo.

## Aprobación manual

Quien gestiona puede liberar pedidos pendientes a mano, por selección y en bloque: o se
aprueban todos los enviados o ninguno (si uno no está en el archivo es 404; si uno ya no
está pendiente, 409). Se guarda quién, cuándo y una nota opcional (máximo 500
caracteres). La aprobación es del pedido: sus líneas conservan su estado y motivo, así
que `ready_line_count` y el filtro `reason` no cambian, mientras que el estado del pedido,
el filtro `status`, `counts` y `ready_order_count` lo cuentan como listo. **Revalidar
conserva las aprobaciones** (son decisiones de una persona). Quitar la aprobación devuelve
el pedido a lo que diga la validación. Borrar un archivo borra sus líneas y aprobaciones.

Cada línea guarda todos los apoyos evaluados (`checks`) con su resultado, margen con
apoyo, margen objetivo y una explicación en español, y el apoyo que decidió
(`support_id`), para abrirlo desde la pantalla.

## API (`/api/v1/order-validations`)

Lectura: cualquier usuario autenticado. Subir, revalidar, aprobar y borrar: administradores y miembros del
área `ORDER_VALIDATION_MANAGER_AREA_SLUG` (por defecto `comercial`).

| Método y ruta | Uso |
| --- | --- |
| `POST /order-validations` | Subir el archivo (`multipart/form-data`, campo `file` y opcional `margin_threshold_points`); valida todo y devuelve el lote con sus conteos y su umbral |
| `GET /order-validations` | Archivos subidos, más recientes primero (`page`, `size`) |
| `GET /order-validations/{id}` | Un archivo con sus conteos y `margin_threshold_points` |
| `GET /order-validations/{id}/orders` | Pedidos con sus líneas, `manual_approval`, `margin_pct`, `required_margin_pct`, `margin_with_threshold_pct`, `release_basis`, `without_support_line_count` y `has_liquor_tax`. Filtros: `status`, `reason`, `q` (documento, cliente, proveedor, referencia, vendedor), `sort` (`attention` por defecto, o `file`), `margin_threshold_points` (probar un umbral sin guardarlo), `page`, `size`. `counts` trae los pedidos por estado antes de filtrar por estado |
| `POST /order-validations/{id}/revalidate` | Vuelve a validar con los apoyos y márgenes como están ahora. Cuerpo opcional `{"margin_threshold_points": 0.5}`; sin valor conserva el umbral guardado. Conserva las aprobaciones manuales |
| `PUT /order-validations/{id}/margin-threshold` | `{"margin_threshold_points": 0.5}`: guarda el umbral del archivo y recalcula qué pedidos libera, sin revalidar las líneas; conserva las aprobaciones |
| `POST /order-validations/{id}/approvals` | `{"documents": [...], "note": "..."}`: aprueba a mano pedidos pendientes, todo o nada; devuelve el archivo con sus conteos |
| `DELETE /order-validations/{id}/orders/{document}/approval` | Quita la aprobación manual (204) |
| `DELETE /order-validations/{id}` | Borra el archivo con sus líneas y aprobaciones (204) |
| `GET /order-validations/catalogs` | Estados y motivos con su etiqueta |
| `GET /order-validations/permissions` | `can_manage` para mostrar u ocultar subir y revalidar |

## Pendiente de definir con el negocio

1. Si `cumple_margen_lista` debe liberar: hoy se libera la línea cuyo margen ya alcanza el
   margen del producto para el canal.
2. El umbral real con el que SIESA retiene por margen, para usarlo en vez del margen del
   producto.
3. Si un apoyo `pendiente_revision` puede liberar.
4. Si el costo de referencia del apoyo debe compararse con el costo actual (hoy solo se
   usa la proporción del apoyo).
5. Si el margen del pedido debe poder liberar pedidos con líneas sin apoyo (hoy sí, mientras tengan margen de referencia).
6. Qué hacer con los productos con impuesto al licor además de marcarlos.
7. Cuándo aplicar Margen Masivo Food y Margen Market Apartadó, y el margen de los productos
   cuya nota dice «Margen mínimo» distinto del de lista (por ejemplo RPB: lista 57 %, mínimo
   50 %).
