# Histórico de alza de precios

El acumulado de alzas del área de compras (`Acumulado_Alza_Precios.xlsx`) vive ahora en la
plataforma, bajo el maestro de productos. Cada fila es un cambio de costo que un proveedor
notificó para una referencia:

- cuándo se notificó y cuándo rige;
- cuándo se cargó en SIESA (`system_updated_on`);
- el costo anterior y el nuevo, con y sin impuesto saludable;
- la variación, y si subió, bajó o quedó estable.

Por ahora se actualiza subiendo ese mismo libro. La fecha de actualización en SIESA también se
puede poner o quitar fila por fila desde el portal.

Código: `app/modules/commercial_masters/domain/price_increases.py` (reglas y lectura de
celdas), `infrastructure/price_increases_file.py` (lector), `infrastructure/
price_increases_repository.py`, `application/price_increases.py`. Tabla
`product_price_increases` (migración `0055_price_increases`).

## Qué se guarda y qué no

Solo las columnas del alza:

- **Fechas:** de notificación, de actualización en el sistema y de incremento.
- **Identificadores:** SKU del proveedor y EAN.
- **Regional, ICUI e IBUA.**
- **Costos:** anterior y nuevo, con y sin impuesto saludable.
- **Variación** en pesos y en porcentaje.
- **Estado.**
- **Observaciones, margen y precio.**
- **Marcas** de notificación y de cambio en SIESA.

Las columnas del producto (`Nit`, `Proveedor`, `Desc. item`, `Unidad de Empaque`, `Estado el
Item`, `Categoria`, `Grupo Impositivo`, `Descuento Regular`) **no se cargan**: lo que ya está
en el maestro de productos se queda como está. La vista lee la descripción, el proveedor y la
categoría del maestro.

El histórico conserva referencias que el maestro ya no tiene; se marcan como «No está en el
maestro de productos».

## Carga del libro (upsert)

`POST /commercial/price-increases/upload`, multipart con `file` (`.xlsx`). Es una simulación
salvo `apply=true`, como el actualizador de márgenes.

- El encabezado se busca por nombre en las primeras 10 filas, en cualquier orden. Se necesita
  `Referencia` y un costo nuevo (con o sin impuesto saludable).
- **Llave:** referencia + fecha de incremento + fecha de notificación. Una fila con una llave
  nueva se inserta; con una existente se actualiza si cambió algo; si no, queda igual. Si el
  archivo repite una llave, queda la última fila (se reportan como repetidas).
- **El libro tal como lo lleva el área:** todo carga, sin errores (decisión del 2026-11-11).
  - Montos como texto en dos formatos (`$ 81.960,00` y `$ 95.484`) y guiones: se leen.
  - Errores de fórmula de Excel (`#N/D`, `#¡REF!`, `#¡VALOR!`): son celdas sin valor; quedan
    vacías y no se reportan.
  - Palabras donde va un monto (`DESCODIFICADO`, `CODIGO YA NO EXISTE`, `SALIO DEL MERCADO`):
    son una nota de la fila; pasan a Observaciones («Costo nuevo sin impuesto saludable:
    DESCODIFICADO») y la celda queda vacía.
  - Las fechas se guardan como vienen, por viejas que sean: 1900-01-18 (un número en una celda
    de fecha) o `18/09/0206` (año 0206; en el acumulado son 340 filas cuyo incremento es
    2026-09-25, un error de digitación de 2026 que hay que corregir en el libro).
  - Solo se reporta lo que no es nada de eso: un monto con números que no se puede leer
    (`12 mil`). Esa celda queda vacía y la fila se carga igual.
- **Estado:** el del archivo (`SUBIO`, `BAJO`, `ESTABLE`). Si falta, se calcula comparando el
  costo nuevo con el anterior. La variación en pesos que falte también se calcula.
- El libro se lee con openpyxl en modo de solo lectura y en un hilo aparte: las 50.208 filas
  del acumulado de noviembre de 2026 se leen en unos 8 s, sin frenar la API.
- El reporte dice:
  - filas leídas;
  - nuevas, que cambian y sin cambios;
  - repetidas;
  - referencias fuera del maestro;
  - filas pendientes en SIESA;
  - las primeras 200 celdas que no se pudieron leer.

Con el acumulado de noviembre de 2026 no se reporta ninguna celda: 227 errores de fórmula quedan
vacíos, 11 notas pasan a Observaciones y las 342 fechas raras se guardan como vienen. De las
11.718 referencias, 270 quedan con el alza más reciente pendiente en SIESA.

Subir y cambiar fechas exige gestionar los maestros (BI, módulo `maestros`). Leer está
abierto a quien lee los maestros comerciales.

## Regla en el cotizador

Para cada referencia se toma su **alza más reciente**: la que rige último y, entre esas, la
última notificada. Si **no tiene fecha de actualización en SIESA**, su costo nuevo entra como
**tercer valor** del costo, junto a la cotización del proveedor y el inventario:

- Se usa el costo nuevo **con** impuesto saludable cuando la fila lo trae, y si no, el costo sin
  él (la cotización del proveedor también lleva ICUI e IBUA).
- El costo de la línea es el **mayor** de los tres. El alza solo gana si es estrictamente mayor:
  en un empate se queda la cotización y luego el inventario.
- La línea guarda el valor del alza (`increase_cost`) y, si ganó, `cost_source = "alza"`
  («Alza de precios pendiente»).
- Si la alza más reciente ya tiene fecha, la referencia no compara nada extra, aunque una más
  vieja haya quedado sin marcar.

Poner la fecha en el portal (o cargar el libro con la fecha) saca la referencia de la regla en
la siguiente construcción o refresco de la cotización.

## API (`/api/v1/commercial`)

| Método y ruta | Uso |
| --- | --- |
| `GET /price-increases` | `q` (prefijo de referencia, descripción o proveedor), `status` (`subio`, `bajo`, `estable`), `pending` (`true`: sin fecha en SIESA), `sku`, `page`, `size` (≤ 100). Devuelve `items`, `total`, `counts: {subio, bajo, estable, pendientes}` del filtro completo y `can_manage` |
| `POST /price-increases/upload` | Multipart `file`; `apply` (por defecto simulación). Devuelve `{applied, rows_read, inserted, updated, unchanged, duplicated, unknown_skus, pending, issues, issues_truncated}`. 403 sin permiso de gestión; 422 si el archivo no sirve |
| `PATCH /price-increases/{id}` | `{system_updated_on: "AAAA-MM-DD" \| null}`. `null` la deja pendiente otra vez. 403 sin permiso de gestión; 422 antes del año 2000 (a mano es un error de digitación; el libro sí guarda fechas viejas) |

Cada fila trae los campos guardados más `description`, `supplier_name`, `category` e
`is_known_product` del maestro, `status_label` e `is_pending`.
