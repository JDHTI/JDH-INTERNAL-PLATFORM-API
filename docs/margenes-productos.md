# Margen por producto

Cada producto del maestro de productos tiene un **margen por canal**. Reemplaza al margen
por proveedor comercial (`Margenes Lista1`), que se eliminó: los productos de un mismo
proveedor tienen márgenes distintos y el archivo `Maestro Margen.xlsx` ya trae uno por producto.
Lo usa el [validador de pedidos](validador-pedidos.md).

Módulo `commercial_masters`. Migración `0022`.

## Los márgenes

Una columna por canal en `products`, como fracción (0,18 = 18 %). **Vacío significa «sin margen
definido»**: el libro escribe 0 donde no tiene, y al cargarlo queda vacío.

| Columna | Hoja `Margen Producto` | Lo aplica el validador a |
| --- | --- | --- |
| `margin_list` | Margen Lista 1 | todo canal que no sea mayorista, subdistribuidor ni salsamentarias |
| `margin_wholesale` | Margen Mayorista | `MAYORISTAS Y DISTRIBUIDORES` y `SUBDISTRIBUIDOR` |
| `margin_salsamentarias` | Margen Salsamentarias | `SALSAMENTARIAS` |
| `margin_mass_food` | Margen Masivo Food | todavía a ninguno (se carga y se muestra) |
| `margin_market` | Margen Market Apartadó | todavía a ninguno (se carga y se muestra) |
| `margin_notes` | Observaciones | texto libre («Aseo 20 %», «Mixes Maquinas 14 %»…) |

Además: `margin_source` (`importacion` o `manual`), `margin_updated_by` y `margin_updated_at`, para saber
de dónde viene el valor y quién lo cambió por última vez.

**Qué toca la sincronización con el DWH**: solo los campos que vienen del DWH. Los márgenes, sus
notas y el impuesto al licor **no se tocan** al actualizar los productos. En cambio, cualquier campo
del DWH que se edite a mano (descripción, marca, categoría…) se sobrescribe en la siguiente
sincronización. El estado y el proveedor comercial vienen del DWH y no se pueden editar.

## Impuesto al licor

`liquor_tax` (`IMPTOLIC`, valor por unidad en la región Nacional), `liquor_tax_coast` (Costa) y
`liquor_tax_group` (grupo impositivo, `0503`, `0509`) de los 12 productos con impuesto. El validador
marca las líneas de esos productos: el pedido pudo montarse sin el impuesto. Algunas referencias pagan
más en la Costa (18973: 4.036 Nacional y 4.144 Costa); el [cotizador](cotizador.md) usa el valor Costa
para los clientes que facturan desde el CO `004`.

El actualizador de márgenes también carga el impuesto: lee `IMPTOLIC` de `Margen Producto` y la tabla
`IMPUESTOS` (`Sku`, `Nacional`, `Costa`, `Grupo impositivo`) de `Margen Proveedor-Impuestos`, que manda
sobre `IMPTOLIC`. `IMPTOLIC` en 0 sin fila en la tabla quita el impuesto. El reporte muestra el antes y el
después de cada impuesto (`taxes`).

## Carga inicial desde el libro

El libro `Maestro Margen.xlsx` tiene varias hojas; el margen vigente está en **`Margen Producto`**
(27.739 referencias, ninguna repetida). `Margen Productos` es una copia de trabajo antigua con 9.269
filas y 110 diferencias, y `Margen Proveedor` / `Margen Proveedor-Impuestos` traen la estructura por
proveedor (ya no se usa) y la tabla de impuestos.

```bash
poetry run python scripts/import-product-margins.py "Maestro Margen.xlsx"          # simulación
poetry run python scripts/import-product-margins.py "Maestro Margen.xlsx" --apply
```

Necesita los productos ya sincronizados (`scripts/sync-dwh-masters.py`). Se puede repetir: solo escribe
diferencias, y un margen editado a mano se reemplaza por el del libro. Carga de octubre de 2026:
24.009 productos con margen, 3.729 sin margen (todo en 0 en el libro), 12 con impuesto al licor y 1
referencia del libro (`222879`) que no está en el maestro.

## Actualizador de márgenes

Para cambiar muchos márgenes a la vez se sube un archivo `.xlsx` o `.csv` con la plantilla:

| Columna | |
| --- | --- |
| `Referencia` | referencia JDH (SKU); obligatoria |
| `Margen Lista 1`, `Margen Mayorista`, `Margen Masivo Food`, `Margen Market Apartadó`, `Margen Salsamentarias` | al menos una; el resto de columnas se ignora |
| `IMPTOLIC` | opcional: impuesto al licor Nacional por unidad; 0 lo quita |
| `Observaciones` | opcional |

Reglas de cada celda:

- **Vacía**: deja el margen como está.
- **`0`**: borra el margen («sin margen definido»).
- Fracción (`0.18`) o puntos (`18`, `18 %`); lo que sea menor que 1 es fracción. Fuera de 0–100 o
  con texto es inválido y esa fila **no se aplica** (el resto sí).
- Una referencia que no existe en el maestro se reporta y no se aplica; si se repite, gana la última fila.

Todo archivo se **simula** primero: el reporte dice cuántos productos cambian, cuántos quedan igual,
qué referencias no existen y qué filas son inválidas, con el antes y el después de cada cambio (los
primeros 500). Solo con `apply=true` se escribe. La plantilla se descarga con los márgenes actuales
(`with_data=true`) o vacía; se edita y se vuelve a subir. Tiene el formato del `Maestro Margen.xlsx`:
la hoja `Margen Producto` (con `IMPTOLIC`) y `Margen Proveedor-Impuestos` con la tabla `IMPUESTOS` en
las columnas I:N, así que el libro del área se sube tal cual. Si el libro tiene varias hojas se lee la
`Margen Producto`; `sheet` elige otra.

## API (`/api/v1/commercial`)

Lectura: cualquier usuario autenticado. Escritura: administradores y miembros del área
`SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG` (por defecto `comercial`).

| Método y ruta | Uso |
| --- | --- |
| `GET /products` | Lista de productos con sus márgenes. Filtros: `q`, `brand`, `category`, `commercial_supplier_code`, `is_active`, `has_margin` (con o sin algún margen). Los activos van primero |
| `GET /products/{sku}` | Un producto con sus márgenes e impuesto |
| `PATCH /products/{sku}` | Cambia solo los campos enviados: descripción, marca, categoría, empaque…, los cinco márgenes (fracción entre 0 y 1; `null` borra), `margin_notes` e impuesto al licor. El estado y el proveedor no se editan. Un campo desconocido es 422 |
| `PUT /products/{sku}/margins` | Actualización manual de un producto, sin archivo. Ver abajo |
| `POST /products/margins` | Actualizador (`multipart/form-data`, campo `file`; `apply=true` para escribir; `sheet`) |
| `GET /products/margins/template` | Plantilla `.xlsx`; `with_data=false` la trae vacía |

### Actualización manual de un producto (`PUT /products/{sku}/margins`)

Para corregir uno o dos productos sin armar un archivo. Todos los campos son obligatorios:

```json
{
  "margin_list": 0.18,
  "margin_wholesale": 0.12,
  "margin_mass_food": 0.15,
  "margin_market": 0.20,
  "margin_salsamentarias": 0.10,
  "notes": "Revisado con compras",
  "liquor_tax": {"national": 1200, "coast": 1350, "group": "0509"}
}
```

- **Márgenes.** Los cinco, como fracción entre 0 y 1 (`0.18` = 18 %). Ninguno puede ir vacío ni en 0;
  para dejar un producto sin margen está el `PATCH` o el archivo.
- **`notes`.** Se envía siempre; `null` o vacío las borra.
- **`liquor_tax`.** Se envía siempre. `null` si el producto no paga impuesto al licor. Si el producto ya lo
  paga (tiene Nacional, Costa o grupo guardados) es obligatorio, con los tres valores: Nacional y Costa
  en pesos por unidad y el grupo impositivo (`0503` vino, `0509` licores). Un producto que no lo pagaba
  puede empezar a pagarlo.
- **Respuesta.** El producto actualizado, con `margin_source = manual` y quién y cuándo lo cambió.
- **Errores.** 403 si no gestionas; 404 si el SKU no existe; 422 si falta un campo, un margen está fuera
  de rango o el producto paga impuesto al licor y no llega.

### El sync del DWH no toca los márgenes

Los márgenes, sus notas y el impuesto al licor solo cambian a mano o por archivo. El sync de productos
(job `commercial-sync-products`) escribe únicamente las columnas del catálogo de `consumo.productos`,
y un producto que sale del DWH queda inactivo, no se borra. Una prueba
(`test_the_product_sync_never_carries_margins_or_liquor_taxes`) lo vigila.

Los maestros generales (`/suppliers`, `/legal-suppliers`, `/customers`, `/products`) listan **primero los
activos y al final los inactivos**, y se pueden filtrar con `is_active=true|false`. Para un cliente o
un proveedor legal, activo significa estado y sucursal activos (`A`).

## Cambio en proveedores comerciales

`PATCH /suppliers/{code}` ya no acepta `list_margin_pct` ni `list_margin_notes`, y la respuesta no los
trae: solo confirma NITs (`confirmed_nits`, `primary_nit`). La migración `0022` borra esas columnas
(los valores no se recuperan al revertir).
