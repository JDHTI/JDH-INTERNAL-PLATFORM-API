# Histórico de alza de precios

El acumulado de alzas del área de compras (`Acumulado_Alza_Precios.xlsx`) vive ahora en la
plataforma, bajo el maestro de productos. Cada fila es un cambio de costo que un proveedor
notificó para una referencia:

- cuándo se notificó y cuándo rige;
- cuándo se cargó en SIESA (`system_updated_on`);
- el costo anterior y el nuevo, con y sin impuesto saludable;
- la variación, y si subió, bajó o quedó estable.

Desde el 2026-11-25 **no se carga el libro**: las alzas entran solas cuando se aprueba una
solicitud de proveedor en Costos ([costos.md](costos.md)), y llegan a SIESA con un **lote de
cargue** (abajo). La fecha de SIESA también se puede poner o quitar fila por fila.

Código: `app/modules/commercial_masters/domain/price_increases.py` (reglas y lectura de
celdas), `domain/siesa_loads.py` y `infrastructure/siesa_load_files.py` (cargue a SIESA), `infrastructure/
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

## Cargue a SIESA (lotes)

Una alza sin fecha de SIESA está **pendiente**: el cotizador compara su costo nuevo. Para que
SIESA la tenga, quien gestiona el acumulado la carga como **cotización de proveedor** con el
plano de Impodatos `0212` (2026-11-25), como en el cotizador:

1. **Elegir** alzas pendientes en el histórico: una a una, o todas las del filtro («Seleccionar
   todas»; hasta 2.000), con los mismos filtros del histórico (búsqueda, proveedor, estado, C.O.,
   instalación).
2. **Revisar** (`POST /price-increases/siesa-loads`, sin `apply`): qué va, con el proveedor, la
   unidad, el precio y la fecha que toma cada una, y qué queda por fuera y por qué.
3. **Generar** (`apply=true`): se guarda el **lote** (número consecutivo, estado `generado`) y
   se descargan sus dos archivos: el plano `.txt` para SIESA y el libro `IncrementoCostos
   Entradas` (`.xlsx`). Una alza en un lote abierto no entra en otro (`open_load` en el
   histórico dice en cuál está).
4. **Cargar** el `.txt` en SIESA (Impodatos).
5. **Confirmar el cargue** (`/confirm`, con la fecha; hoy por defecto): las alzas del lote
   toman esa fecha de SIESA y la marca «Cambio en SIESA», y dejan de estar pendientes (el
   cotizador deja de compararlas). El comunicado de Alzas de proveedor ya las incluye.
   Si el archivo no se cargó, **anular** el lote (`/void`) las libera.

**Qué lleva cada cotización:**

- **Proveedor, sucursal, unidad y días de entrega:** los de la cotización vigente de la
  referencia en su unidad de inventario (`UNID`, `KL`), la de activación más reciente: la misma
  de la que la MAESTRA toma el costo anterior. 503 referencias tienen cotización vigente de más
  de un proveedor; el lote muestra los otros NIT (`other_nits`).
- **Precio:** el costo nuevo **sin** impuesto saludable (el precio unitario de SIESA, antes de
  impuestos).
- **Activación:** la fecha de incremento; si ya pasó, el día en que se genera el lote, para no
  mover costos hacia atrás en SIESA (decisión del 2026-11-25).
- **Fin:** 2099-12-31, como los archivos del área.

**Qué queda por fuera** (con el motivo en la revisión): una alza de **una regional** (la
cotización de SIESA es de toda la compañía; decisión del 2026-11-25), sin costo nuevo sin
impuesto saludable o sin fecha de incremento, ya cargada, en otro lote abierto, una referencia
sin cotización vigente en su unidad de inventario (la primera se crea a mano en SIESA), o una
alza más vieja de una referencia que va dos veces (va la más reciente).

**El plano** (registro `0212` versión 02, 200 caracteres, ASCII con CRLF, entre `0000` y
`9999`, como el del cotizador):

| Posición | Tamaño | Campo |
| --- | --- | --- |
| 1 | 7 | Consecutivo del registro |
| 8 | 4 | `0212` |
| 12 | 2 | Subtipo `00` |
| 14 | 2 | Versión `02` |
| 16 | 3 | Compañía `001` |
| 19 | 1 | `1`: actualiza la cotización si existe |
| 20 | 15 | NIT del proveedor |
| 35 | 3 | Sucursal del proveedor (`001`) |
| 38 | 3 | Moneda (`COP`) |
| 41 | 7 | Id del ítem en ceros (va por referencia) |
| 48 | 50 | Referencia |
| 98 | 60 | Código de barras y extensiones, en blanco |
| 158 | 8 | Fecha de activación `AAAAMMDD` |
| 166 | 4 | Unidad de medida |
| 170 | 20 | Precio unitario `000000000000000.0000` |
| 190 | 3 | Días de entrega |
| 193 | 8 | Fecha final `20991231` |

Se armó con el archivo del área del 2026-11-18 (`34524H11M48…txt`, ocho cotizaciones) y se
comprobó contra lo que SIESA guardó de él en el DWH: proveedor, sucursal, unidad, precio, fecha,
días de entrega y fecha final coinciden en las ocho. La prueba vuelve a generar ese archivo y
sale idéntico, byte por byte. SIESA crea solo las cotizaciones de las otras unidades (`C12`,
`C6`) con el factor de cada una: el plano lleva solo la de inventario.

**El libro** (`IncrementoCostos Entradas <dd-mm-aaaa> L<n>.xlsx`): hoja «Encabezado Cotizaciones
», columnas `ID_TERCERO`, `F212_ID_SUCURSAL`, `REFERENCIA_ITEM`, `AAAAMMDD`, `UM` (cuatro
caracteres) y `PRECIO` (dos decimales), todo como texto, como el del área. El plano se llama
igual con `.txt`.

Los archivos se escriben de nuevo desde lo guardado del lote, así que una segunda descarga es
idéntica. Tablas `price_increase_siesa_loads` y `price_increase_siesa_load_rows` (migración
`0090`).

**Alzas encadenadas el mismo día** (migración `0088`): el libro del área repetía 99 llaves en
101 filas que eran alzas seguidas con las mismas fechas, y la carga se quedaba con la última.
Cada fila guarda su `occurrence` y, desde la segunda, la llave lleva `#2`. Con el libro fuera de
uso, esas 101 filas históricas siguen perdidas salvo que se recuperen una vez a mano.

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

Poner la fecha en el portal (o confirmar el lote de cargue a SIESA) saca la referencia de la regla en
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

**C.O. e instalación** (2026-11-24). Dos filtros generales que cambian el análisis, el histórico
y el detalle de cada referencia (`co` e `installation`, códigos de `GET /price-increases/options`):

- **Qué referencias:** las que están activas en una bodega activa de ese C.O. o esa instalación
  (la «instalación» de SIESA, donde se lleva el costo promedio). Sale del inventario del DWH
  (`consumo.inventario_fecha` con `consumo.bodegas`), unas 25.000 combinaciones referencia, C.O.
  e instalación que la API lee en 0,2 s y guarda una hora.
- **Qué filas de cada referencia:** las nacionales (regional vacía o NACIONAL) y las de la
  regional del C.O.: ANTIOQUIA 001, BOGOTA 003, PEREIRA 002, BUCARAMANGA 005, CARTAGENA 004,
  BARRANQUILLA 009 (las regionales de la MAESTRA de Costos). En Bogotá no cuenta el costo que
  se le aprobó solo a Antioquia, así que el costo con el que empieza y termina una referencia
  es el de ese C.O. Un C.O. sin regional en la MAESTRA (Urabá, Valledupar…) ve solo las
  nacionales. Una instalación toma las regionales de los C.O. donde tiene bodegas.
- **Los dos a la vez** se cruzan: las referencias de esa instalación en ese C.O.
- Si el DWH no responde, el análisis con C.O. o instalación responde 503 (no se muestra un
  filtro a medias) y las opciones llegan sin C.O. ni instalaciones.

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
última modificación): una alza aprobada, un lote confirmado o una fecha cambiada cambian la versión. Analiza cada periodo
en un hilo aparte la primera vez que se pide y lo guarda en memoria del proceso
(`AnalysisCache`, hasta 32 periodos por versión; sale el más viejo). Filtrar, buscar, ordenar y paginar se hace sobre ese resultado. Con el
acumulado real, analizar todo el histórico toma unos 0,2 s y el último año 0,08 s. La tabla
tiene índice por fecha de incremento (`0056_price_increase_dates`), que también ordena el
histórico.

**Detalle de una referencia.** Al abrir una fila se ve todo su histórico: una gráfica del
costo en el tiempo y la tabla de sus alzas (`GET /price-increases?sku=`, con `co` e
`installation` cuando el análisis los tiene).

## Permisos

Las tres vistas (maestro de productos, histórico de alzas y análisis) son de la misma página del
módulo `maestros`: **dar acceso al módulo da acceso a las tres**, sin permisos por vista.
Cambiar algo (generar, confirmar o anular un lote de cargue a SIESA, poner la fecha de SIESA,
editar márgenes) exige **gestionar** el
módulo (BI). En la API, leer está abierto a quien tenga cualquiera de los módulos comerciales
(`maestros`, `apoyos`, `cotizador`, `validador-pedidos`) o de Costos.

Desde el 2026-11-23 el acumulado también es una vista de Costos (Abastecimiento), con la clave
`acumulado-alzas`. Quien la gestiona genera y confirma los lotes de cargue a SIESA y pone o
quita la fecha de SIESA, igual que
`maestros` en BI. Las alzas que aprueba la vista «Alzas de proveedor» llegan aquí solas; ver
[`costos.md`](costos.md).

Cada fila dice de dónde vino (`source`, migración `0082`):
- `libro`: el libro del área, que se cargó hasta el 2026-11-25. Es lo que tienen todas las
  filas anteriores.
- `solicitud`: una solicitud de proveedor aprobada en Costos. Trae `request_line_id`, está
  notificada y queda sin fecha de SIESA.

## API (`/api/v1/commercial`)

| Método y ruta | Uso |
| --- | --- |
| `GET /price-increases/options` | Los proveedores comerciales con referencias en el histórico (`code`, `name`, `references`, por nombre) y los meses con alzas desde el año 2000 (`month` «AAAA-MM», `changes`, `references`, el más reciente primero), los C.O. (`cos`: `code`, `name`) y las instalaciones (`installations`: `code`, `name`, `cos` donde tiene bodegas, por nombre) con referencias activas: los dropdowns |
| `GET /price-increases` | `q` (prefijo de referencia o descripción), `supplier` (código del proveedor comercial), `co`, `installation`, `status` (`subio`, `bajo`, `estable`), `pending` (`true`: sin fecha en SIESA), `sku`, `page`, `size` (≤ 100). Devuelve `items`, `total`, `counts: {subio, bajo, estable, pendientes}` del filtro completo y `can_manage` |
| `GET /price-increases/analysis` | `period` (`12m` por defecto, `1m`, `24m`, `all`), o `since` y `until` (AAAA-MM-DD, ambos incluidos; reemplazan a `period`), `supplier`, `co`, `installation` (503 si el DWH no responde), `movement` (`subio`, `bajo`, `estable`), `q`, `sort` (`accumulated` por defecto, `increases`, `frequency`, `last_change`, `sku`), `order` (`desc` por defecto), `page`, `size` (≤ 100). Devuelve `items` (por referencia: `start_cost`, `end_cost`, `accumulated_pct`, `movement`, `increases`, `decreases`, `changes`, `frequency_days`, `last_increased_on`, `last_status`, `last_change_pct`, `is_pending` y los datos del maestro, con `supplier_code`), `total`, `since`, `until` (null: abierto) y `summary: {references, increases, typical_increase_pct, pending, movements}` de la búsqueda, antes del filtro de movimiento |
| `PATCH /price-increases/{id}` | `{system_updated_on: "AAAA-MM-DD" \| null}`. `null` la deja pendiente otra vez. 403 sin permiso de gestión; 422 antes del año 2000 (a mano es un error de digitación; el libro sí guarda fechas viejas) |
| `POST /price-increases/siesa-loads?apply=` | Body `{increase_ids: [...]}` o `{all: true, filters: {q, status, sku, supplier, co, installation}}` (todas las pendientes del filtro, hasta 2.000). Sin `apply` es la revisión. Devuelve `{applied, rows: [{increase_id, sku, description, nit, branch, currency, unit, price, activation_date, delivery_days, increased_on, other_nits}], excluded: [{increase_id, sku, reason}], load}`. 403 sin gestión; 409 si una alza entró a otro lote en el camino; 422 si nada se puede cargar; 503 sin DWH |
| `GET /price-increases/siesa-loads?status=&page=&size=` | Lotes, el más reciente primero, con `counts` por estado (`generado`, `cargado`, `anulado`) y `can_manage` |
| `GET /price-increases/siesa-loads/{id}` | El lote con sus `rows` |
| `GET /price-increases/siesa-loads/{id}/file?kind=txt\|xlsx` | El plano `0212` o el libro `IncrementoCostos Entradas` |
| `POST /price-increases/siesa-loads/{id}/confirm` | `{loaded_on?}` (hoy por defecto; no puede ser futura). Fecha las alzas. 409 si ya se confirmó o anuló |
| `POST /price-increases/siesa-loads/{id}/void` | Anula un lote generado; sus alzas quedan libres. 409 si ya se confirmó o anuló |

Cada fila trae los campos guardados (con `occurrence`: 1, o 2, 3… para alzas encadenadas el
mismo día) más `description`, `supplier_name`, `category` e `is_known_product` del maestro,
`status_label`, `is_pending` y `open_load` (el número del lote abierto en que está, o null).
