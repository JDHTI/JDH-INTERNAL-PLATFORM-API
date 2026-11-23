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

## Análisis de alzas

La tercera vista del maestro de productos responde cuánto subió cada referencia en un periodo,
cada cuánto sube y cómo terminó su último cambio. Las filas se fechan por la **fecha de
incremento**, no por la de notificación (que trae errores de digitación y no dice cuándo se
movió el costo).

**Periodo.** Es una ventana de días, `since` a `until`, ambos incluidos:

- **Predefinidos** (`period`): el **último año** por defecto, el **último mes** (`1m`), **2 años**
  (`24m`) o **todo el histórico** (`all`). Van desde el mismo día de hace uno, doce o veinticuatro
  meses hasta la última alza cargada, aunque rija después de hoy.
- **Un mes o un rango:** `since` y `until` reemplazan a `period`. Un mes es su primer y último día
  (la UI ofrece los meses con alzas). Cualquiera de los dos puede quedar abierto. Si el rango
  termina antes de empezar, la API responde 422.
- **Qué cuenta:** las alzas posteriores al periodo no cuentan. Las anteriores solo dan el costo con
  el que empezó.
- **Respuesta:** trae `since` y `until`, así la UI describe el periodo exacto.

**Filtros.** La referencia (prefijo) o la descripción se escriben en `q`. El **proveedor** no se
busca como texto: se elige de la lista de `GET /price-increases/options` y viaja como código
(`supplier`). El histórico usa el mismo filtro.

El acumulado está digitado a mano: las filas de una referencia no forman una cadena limpia (el
costo anterior de una fila no siempre es el costo nuevo de la anterior) y `Variacion %` a veces
trae puntos (16,5) en lugar de fracción. Por eso las cifras comparan **costos**, en una misma
base (sin impuesto saludable; con él solo si falta el otro), y no encadenan variaciones:

- **Cambio en el periodo:** el costo al final del periodo contra el costo justo antes de que
  empezara (el costo nuevo de la última alza anterior; si no hay, el costo anterior de la primera
  del periodo). Un error en una fila intermedia no se acumula. Un costo de $1 o menos es un
  marcador y no cuenta.
- **Movimiento:** subió, bajó o estable según el signo de ese cambio (estable dentro de media
  centésima de punto). Es lo que filtran los chips «Subieron», «Bajaron» y «Estables».
- **Alzas:** filas del periodo con estado «Subió». **Cada cuánto:** la mediana de días entre
  alzas consecutivas (hacen falta dos).
- **Última variación:** la última fila del periodo, con su estado, su variación (costo nuevo
  contra costo anterior) y su fecha.
- **Alza típica:** la mediana de las alzas del periodo. Una digitación de +1.457 % arrastra un
  promedio, no una mediana.

Con el acumulado de noviembre de 2026, el último año tiene 6.262 referencias con cambios: 4.635
subieron, 930 bajaron y 389 quedaron estables. El alza típica es 6,49 %. Unas 40 referencias pasan
de +100 %: son saltos como 3.520 → 211.200 (×60), casi seguro un cambio de unidad (costo por
unidad contra costo por caja) que hay que revisar en el libro.

**Velocidad.** La API lee el histórico fechado una vez por día y versión de los datos (filas y
última modificación): cargar el libro o cambiar una fecha cambia la versión. Analiza cada periodo
en un hilo aparte la primera vez que se pide y lo guarda en memoria del proceso
(`AnalysisCache`, hasta 32 periodos por versión; sale el más viejo). Filtrar, buscar, ordenar y paginar se hace sobre ese resultado. Con el
acumulado real, analizar todo el histórico toma unos 0,2 s y el último año 0,08 s. La tabla
tiene índice por fecha de incremento (`0056_price_increase_dates`), que también ordena el
histórico.

**Detalle de una referencia.** Al abrir una fila se ve todo su histórico: una gráfica
escalonada del costo en el tiempo y la tabla de sus alzas (`GET /price-increases?sku=`).

## Permisos

Las tres vistas (maestro de productos, histórico de alzas y análisis) son de la misma página del
módulo `maestros`: **dar acceso al módulo da acceso a las tres**, sin permisos por vista.
Cambiar algo (cargar el libro, poner la fecha de SIESA, editar márgenes) exige **gestionar** el
módulo (BI). En la API, leer está abierto a quien tenga cualquiera de los módulos comerciales
(`maestros`, `apoyos`, `cotizador`, `validador-pedidos`) o de Costos.

Desde el 2026-11-23 el acumulado también es una vista de Costos (Abastecimiento), con la clave
`acumulado-alzas`. Quien la gestiona carga el libro y pone o quita la fecha de SIESA, igual que
`maestros` en BI. Las alzas que aprueba la vista «Alzas de proveedor» llegan aquí solas; ver
[`costos.md`](costos.md).

## API (`/api/v1/commercial`)

| Método y ruta | Uso |
| --- | --- |
| `GET /price-increases/options` | Los proveedores comerciales con referencias en el histórico (`code`, `name`, `references`, por nombre) y los meses con alzas desde el año 2000 (`month` «AAAA-MM», `changes`, `references`, el más reciente primero): los dropdowns de proveedor y de mes |
| `GET /price-increases` | `q` (prefijo de referencia o descripción), `supplier` (código del proveedor comercial), `status` (`subio`, `bajo`, `estable`), `pending` (`true`: sin fecha en SIESA), `sku`, `page`, `size` (≤ 100). Devuelve `items`, `total`, `counts: {subio, bajo, estable, pendientes}` del filtro completo y `can_manage` |
| `GET /price-increases/analysis` | `period` (`12m` por defecto, `1m`, `24m`, `all`), o `since` y `until` (AAAA-MM-DD, ambos incluidos; reemplazan a `period`), `supplier`, `movement` (`subio`, `bajo`, `estable`), `q`, `sort` (`accumulated` por defecto, `increases`, `frequency`, `last_change`, `sku`), `order` (`desc` por defecto), `page`, `size` (≤ 100). Devuelve `items` (por referencia: `start_cost`, `end_cost`, `accumulated_pct`, `movement`, `increases`, `decreases`, `changes`, `frequency_days`, `last_increased_on`, `last_status`, `last_change_pct`, `is_pending` y los datos del maestro, con `supplier_code`), `total`, `since`, `until` (null: abierto) y `summary: {references, increases, typical_increase_pct, pending, movements}` de la búsqueda, antes del filtro de movimiento |
| `POST /price-increases/upload` | Multipart `file`; `apply` (por defecto simulación). Devuelve `{applied, rows_read, inserted, updated, unchanged, duplicated, unknown_skus, pending, issues, issues_truncated}`. 403 sin permiso de gestión; 422 si el archivo no sirve |
| `PATCH /price-increases/{id}` | `{system_updated_on: "AAAA-MM-DD" \| null}`. `null` la deja pendiente otra vez. 403 sin permiso de gestión; 422 antes del año 2000 (a mano es un error de digitación; el libro sí guarda fechas viejas) |

Cada fila trae los campos guardados más `description`, `supplier_name`, `category` e
`is_known_product` del maestro, `status_label` e `is_pending`.
