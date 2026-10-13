# Validador de pedidos retenidos

Revisa el reporte de **pedidos retenidos** de SIESA (en su mayoría retenidos por margen)
contra el [maestro de apoyos](apoyos-proveedores.md) y dice, línea por línea y pedido por
pedido, cuáles quedan **listos para liberación** y cuáles quedan **pendientes de
validación**, con el motivo. Toda la validación es del lado del servidor.

Módulo `order_validation`. Migración `0020`.

## El archivo

`.xlsx`, `.xlsm` o `.csv` (un `.xls` se guarda antes como `.xlsx`). Las columnas se buscan
por su encabezado, en cualquier orden, y el encabezado puede estar debajo de un título.

| Encabezado del reporte | Campo | Obligatorio |
| --- | --- | --- |
| `Nro documento` | documento (el pedido) | sí |
| `Cliente factura` | NIT del cliente (solo dígitos, sin DV) | sí |
| `Referencia` | referencia JDH | sí |
| `Precio unit.` | precio unitario | sí |
| `Margen promedio` | margen del pedido, en puntos (`2.2212` = 2,22 %) | sí |
| `PROVEEDOR` | `359 - LOCERIA COLOMBIANA S.A.` → código comercial `359` | sí |
| `Fecha` | fecha del pedido; define la vigencia de los apoyos | no |
| `Cant. pedida`, `CANAL`, `Bodega`, `Lista de precios fact`, `C.O. factura`, `Estado docto.`, `Razon social cliente factura`, `Nombre vendedor cliente`, `Desc. item`, `Retenido margen` | se guardan y se muestran | no |

Una celda ilegible no descarta la fila: queda vacía, la línea guarda el problema y sale
pendiente con `datos_incompletos`. Límites: `ORDER_VALIDATION_MAX_UPLOAD_MB` (10) y
`ORDER_VALIDATION_MAX_LINES` (10 000).

## Cómo se decide una línea

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
| `margen_minimo` | — | margen del pedido ≥ `min_margin_pct` |
| `puntos_margen` | — | margen del pedido ≥ margen lista − puntos |
| `pct_ventas` | no evaluable: el rebate se liquida por periodo | — |

**Margen objetivo**: `target_margin_pct` del apoyo; si no, `min_margin_pct`; si no,
`list_margin_pct`; si no, el margen lista del proveedor comercial. Sin ninguno, el apoyo
no es evaluable. Si el apoyo tiene `min_quantity` y la cantidad pedida es menor, no cumple.

**Qué apoyos aplican**: del mismo proveedor comercial (o sin proveedor y con la misma
referencia), no anulados ni desactivados, vigentes **en la fecha del pedido** y con el
alcance de la línea. Un campo de alcance vacío es «todos», igual que en el maestro:

- cliente (`customer_nit`), referencia (`product_sku`) y bodega (`warehouse`): iguales;
- canal: el del archivo o el del cliente en el maestro; `RESTAURANTE` cubre
  `RESTAURANTES`;
- regional y unidad de negocio: las del cliente en el maestro de clientes (la regional es
  su centro de facturación). Con el cliente desconocido, un apoyo restringido a ellas no
  aplica.

**Resultado** (en este orden):

| Motivo | Estado | Cuándo |
| --- | --- | --- |
| `datos_incompletos` | pendiente | falta proveedor, precio o margen, o el margen es 100 % (la línea no trae costo) |
| `cumple_con_apoyo` | **listo** | un apoyo activo que aplica se cumple; se muestra el de mayor holgura |
| `cumple_margen_lista` | **listo** | el margen del pedido ya alcanza el margen lista del proveedor |
| `acuerdo_en_revision` | pendiente | se cumpliría, pero con un apoyo `pendiente_revision` |
| `no_cumple_acuerdo` | pendiente | hay apoyo y no se cumple (se muestra el más cercano) |
| `acuerdo_no_evaluable` | pendiente | hay apoyo, pero no se puede evaluar por pedido |
| `acuerdo_vencido` | pendiente | un apoyo cubriría la línea, pero no en la fecha del pedido |
| `sin_acuerdo` | pendiente | ningún apoyo cubre la línea |

Un **pedido** está listo para liberación solo si **todas** sus líneas lo están.

Cada línea guarda todos los apoyos evaluados (`checks`) con su resultado, margen con
apoyo, margen objetivo y una explicación en español, y el apoyo que decidió
(`support_id`), para abrirlo desde la pantalla.

## API (`/api/v1/order-validations`)

Lectura: cualquier usuario autenticado. Subir y revalidar: administradores y miembros del
área `ORDER_VALIDATION_MANAGER_AREA_SLUG` (por defecto `comercial`).

| Método y ruta | Uso |
| --- | --- |
| `POST /order-validations` | Subir el archivo (`multipart/form-data`, campo `file`); valida todo y devuelve el lote con sus conteos |
| `GET /order-validations` | Archivos subidos, más recientes primero (`page`, `size`) |
| `GET /order-validations/{id}` | Un archivo con sus conteos |
| `GET /order-validations/{id}/orders` | Pedidos con sus líneas, en el orden del archivo. Filtros: `status`, `reason`, `q` (documento, cliente, proveedor, referencia, vendedor), `page`, `size`. `counts` trae los pedidos por estado antes de filtrar por estado |
| `POST /order-validations/{id}/revalidate` | Vuelve a validar con los apoyos como están ahora |
| `GET /order-validations/catalogs` | Estados y motivos con su etiqueta |
| `GET /order-validations/permissions` | `can_manage` para mostrar u ocultar subir y revalidar |

## Pendiente de definir con el negocio

1. Si `cumple_margen_lista` debe liberar: hoy se libera la línea cuyo margen ya alcanza el
   margen lista del proveedor (solo 291 de 649 proveedores lo tienen cargado).
2. El umbral real con el que SIESA retiene por margen, para usarlo en vez del margen lista.
3. Si un apoyo `pendiente_revision` puede liberar.
4. Si el costo de referencia del apoyo debe compararse con el costo actual (hoy solo se
   usa la proporción del apoyo).
