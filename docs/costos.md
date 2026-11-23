# Costos (Abastecimiento)

Costos reemplaza el aplicativo externo «Control de Precios JDH». Ese aplicativo funcionaba
cargando en Excel las cotizaciones, las referencias, las entradas de compra, el inventario y los
proveedores. Esos datos ya están en el DWH (esquema `consumo`), así que la plataforma los lee
directamente. Solo queda interno lo que el DWH no tiene: el maestro de márgenes, los motivos,
las notas crédito y las alzas que envían los proveedores.

Es un grupo del área **Abastecimiento** con cuatro vistas independientes, cada una con su
clave de permiso:

| Vista | Clave | `read` | `manage` |
| --- | --- | --- | --- |
| Análisis de entradas | `analisis-entradas` | ver las entradas y su estado | asignar el motivo de una línea |
| Novedades y notas crédito | `novedades-compras` | bandeja, historial, soporte | motivo, nota crédito, catálogo de motivos |
| Alzas de proveedor | `alzas-proveedor` | solicitudes y plantilla | cargar, crear, editar, aprobar, rechazar |
| Acumulado de alzas | `acumulado-alzas` | histórico y análisis (`/commercial/price-increases`) | fecha de SIESA y carga del libro |

La migración `0081` crea el área con tres roles:
- «Líder de área»: `*`.
- «Analista de costos»: las cuatro claves.
- «Comprador»: `novedades-compras`. Equivale al rol COMPRADOR del aplicativo anterior, que
  solo veía y editaba las novedades.

`GET /costs/permissions` dice qué puede hacer la persona en cada vista. No exige permiso:
quien no tiene acceso recibe `false`, no un 403.

Código: `app/modules/costs/`. Tablas: migración `0081_costs_novelties`.

## Análisis de entradas

Cada línea es una entrada de compra de SIESA (`EA`, motivo «ENTRADA POR COMPRAS», precio
mayor que 0). Se compara con la **cotización del proveedor vigente el día de la entrada**.

- **Cotización vigente:** la última activación anterior o igual a la fecha de la entrada, para
  el mismo NIT, referencia y unidad de medida. Si hay varias, gana la de la misma sucursal del
  proveedor.
- **Por qué no la última cotización:** el aplicativo anterior comparaba con la última aunque se
  hubiera activado después de la entrada. Eso marcaba como «Revisar» líneas que estaban bien
  el día en que entraron.
  - Ejemplo: EA-60379 (sku 1001, 2026-05-07) entró a 49.384,67. La cotización vigente ese
    día, activada el 2026-02-02, era 49.384,67, así que la línea está bien.
  - El aplicativo la comparaba con la del 2026-07-01 (50.372,37) y la marcaba como
    diferencia.
- **Precio y cotización en la misma base:** los dos van antes de descuentos e impuestos (IVA,
  ICUI, IBUA). La «Tasa 1» (`discount_pct`) es el descuento por escala de la cotización; se
  muestra, pero no se resta.

**Estado de la línea**, con umbral `COSTS_NOVELTY_THRESHOLD` (0,001 = 0,1 %):

| Estado | Etiqueta | Cuándo |
| --- | --- | --- |
| `ok` | Sin cambio | \|precio / cotización − 1\| ≤ umbral (el borde cuenta como sin cambio) |
| `novedad` | Con novedad | por encima del umbral, suba o baje |
| `sin_cotizacion` | Sin cotización | el proveedor no tenía cotización para esa referencia y unidad ese día |

«Solo novedades» (`novelty=true`) muestra `novedad` y `sin_cotizacion`.

Con las entradas del 2026-05-04 al 2026-11-20 (las mismas 89.624 líneas de la hoja Entrada):
74.053 `ok`, 13.358 `novedad` y 2.213 `sin_cotizacion`. La consulta tarda 9,4 s para el
semestre y unos 3 s para un mes.

**Precio anterior y hace 2.** Son los precios de las dos entradas de compra anteriores de la
misma referencia y unidad, en cualquier bodega y C.O., en orden de fecha, C.O. y línea.
- Los calcula el DWH sobre todo el historial (PR #160 de DWH-MIGRATION-PLATFORM), con las
  columnas `precio_anterior`, `precio_hace_2` y `unidad_medida_id` de
  `consumo.entradas_compras`.
- Mientras la vista no las tenga, la API usa la unidad de inventario del producto y calcula los
  precios anteriores dentro del periodo leído. En ese caso, las primeras compras del periodo
  salen sin precio anterior.
- La respuesta lo indica con `previous_from_warehouse`. La API revisa una vez por hora qué
  columnas tiene la vista, así que el cambio se toma solo después del despliegue.

**Velocidad.** Un periodo se lee del DWH una vez y queda en memoria
`COSTS_ENTRIES_CACHE_SECONDS` (600 s). Filtrar, ordenar y paginar se hace sobre esa copia; el
motivo y la nota crédito de cada línea se leen de la plataforma en cada consulta. El periodo
máximo es `COSTS_ENTRIES_MAX_RANGE_DAYS` (200 días).

## Novedades y notas crédito

Una novedad es una línea de entrada que la plataforma guarda para darle **un motivo** y, si el
motivo lo pide, la **nota crédito** del proveedor. Se copia la línea del DWH en dos casos:
- el job diario la encuentra con novedad;
- alguien le pone motivo desde el análisis, aunque esté dentro del umbral.

**El estado nunca se pone a mano**; sale de lo que tiene la novedad:

| Estado | Cuándo |
| --- | --- |
| `anulada` | el DWH ya no tiene la línea (el documento se anuló en SIESA). No se borra; vuelve si la línea reaparece |
| `resuelta` | tiene nota crédito, o un motivo que no la pide, o dejó de ser novedad antes de tener motivo (se fechó hacia atrás una cotización) |
| `espera_nota_credito` | su motivo pide nota crédito y no la tiene |
| `pendiente` | sin motivo |

**Motivos.** Son los 26 del aplicativo anterior, en `cost_reasons`.
- Cuatro piden nota crédito: INCREMENTO NO ANUNCIADO, SOBRECOSTO, DESCUENTO NO APLICADO y ERROR
  FACTURACION PROVEEDOR. Es una propuesta: quien gestiona `novedades-compras` la cambia con
  `PATCH /costs/reasons/{id}`, y las novedades de ese motivo cambian de estado al momento.
- Un motivo inactivo no se puede asignar.

**Nota crédito.** Número, valor y soporte (PDF, PNG o JPG, hasta `COSTS_CREDIT_NOTE_MAX_MB` =
10 MB). El soporte pasa por el mismo filtro de PQRSF: firma del archivo, imagen recodificada y
antivirus.
- **Una nota es única por NIT del proveedor y número** (sin distinguir mayúsculas). Una sola
  nota suele cubrir varias líneas.
- Si el número ya existe para ese proveedor, se reutiliza su soporte y no hace falta adjuntar
  otro (el que se envíe se ignora). Cada novedad guarda su propio valor.
- Al quitarla de la última novedad que la usa, se borra el soporte.
- Cada novedad tiene a lo sumo una nota crédito.

**Job `costs-novelties-sync`.**
- **Cuándo corre:** después de la carga `abastecimiento` del DWH. El respaldo diario corre de
  lunes a viernes.
- **Qué lee:** los últimos `COSTS_NOVELTIES_SYNC_DAYS` (45, la misma ventana que relee el DWH),
  nunca antes de `COSTS_NOVELTIES_START_DATE` (2026-11-01).
- **Qué hace:**
  - guarda las líneas nuevas con novedad;
  - refresca la copia de las que ya estaban;
  - marca `anulada` las que el DWH ya no tiene.
- **Lectura vacía:** si el DWH no devuelve ninguna línea (la vista se está reconstruyendo),
  no anula nada.
- **Desde mayo:** arrancar desde el 2026-05-04 dejaría unas 15.500 novedades pendientes.

## API (`/api/v1/costs`)

| Método y ruta | Permiso | Uso |
| --- | --- | --- |
| `GET /permissions` | abierto | `modules: {clave: {can_read, can_manage}}`, `can_set_reasons` |
| `GET /reasons` | `analisis-entradas` o `novedades-compras` | catálogo de motivos (`include_inactive`) para el desplegable filtrable |
| `PATCH /reasons/{id}` | `novedades-compras: manage` | `{name?, requires_credit_note?, is_active?}` |
| `GET /entries` | `analisis-entradas` | ver abajo |
| `PUT /entries/{line_key}/reason` | manage de `analisis-entradas` o `novedades-compras` | `{reason_id \| null}`; crea la novedad si no existía; devuelve la novedad |
| `GET /novelties` | `novedades-compras` | ver abajo |
| `GET /novelties/{id}` | `analisis-entradas` o `novedades-compras` | novedad con `events` |
| `PUT /novelties/{id}/reason` | manage de cualquiera de las dos | `{reason_id \| null}` |
| `POST /novelties/{id}/credit-note` | `novedades-compras: manage` | multipart `number`, `amount` (pesos, punto o coma decimal), `file`. 201 con la novedad; 409 si ya tiene nota o la entrada se anuló |
| `DELETE /novelties/{id}/credit-note` | `novedades-compras: manage` | quita la nota; devuelve la novedad |
| `GET /novelties/{id}/credit-note/file` | cualquiera de las dos | el soporte; `inline=true` lo muestra en el navegador |

**`GET /entries`.**
- **Periodo:** `since` y `until` (AAAA-MM-DD). Sin ellos, los últimos 30 días hasta hoy. Si el
  periodo termina antes de empezar o pasa del máximo, responde 422.
- **Filtros:**
  - `q`: prefijo de referencia, o texto de la descripción, el documento, la OC o la factura;
  - `status` (`ok`, `novedad`, `sin_cotizacion`) o `novelty=true`;
  - `supplier`: código del proveedor comercial, elegido con `GET /commercial/suppliers`;
  - `co` (`3` y `003` son el mismo), `warehouse` y `reason_id`.
- **Orden:** `sort` es `date`, `difference`, `difference_pct`, `amount`, `sku` o `supplier`,
  con `order` `asc` o `desc` (por defecto, fecha descendente). Las diferencias ordenan por
  tamaño, suban o bajen, y una línea sin cotización siempre queda al final.
- **Página:** `page` y `size` (máximo 200, por defecto 50).
- **Respuesta:**
  - `items`: la línea, más `novelty` (`null`, o `{id, status, status_label, reason_id,
    reason_name, credit_note_number, credit_note_amount}`);
  - `total`, `since`, `until`;
  - `counts: {ok, novedad, sin_cotizacion, total, with_reason}` de toda la búsqueda, antes del
    filtro de estado;
  - `fetched_at`, `previous_from_warehouse` y `threshold`.
- **Campos de la línea:** `line_key`, `entry_date`, `co`, `regional_code`, `regional_name`,
  `warehouse`, `document`, `purchase_order`, `invoice_ref`, `sku`, `description`,
  `supplier_code`, `supplier_name`, `supplier_nit`, `quantity`, `unit`, `unit_price`,
  `quotation`, `quotation_since`, `discount_pct` (Tasa 1), `difference` (pesos),
  `difference_pct` (fracción: 0,05 es 5 %), `amount` (cantidad × diferencia),
  `previous_price`, `previous_price_2`, `status` y `status_label`.

**`GET /novelties`.**
- **Filtros:** `status`, `since` y `until` (fecha de la entrada), `supplier`, `co`,
  `reason_id`, `kind` (`diferencia`, `sin_cotizacion`, `sin_novedad`) y `q` (referencia,
  descripción, documento o número de nota crédito).
- **Orden y página:** `sort` (`date`, `difference`, `difference_pct`, `sku`, `supplier`),
  `order`, `page` y `size`.
- **Respuesta:** `counts` por estado del filtro sin su estado, y `total`.
- **Cada novedad trae:** `line` (los mismos campos de arriba, copiados del DWH), `kind`,
  `status` con su etiqueta, `voided`, `reason`, `reason_by`, `reason_at`, `credit_note` (`{id,
  supplier_nit, number, amount, original_filename, content_type, size_bytes, registered_by,
  registered_at}`), `detected_at` y `updated_at`.

## Fuentes del DWH

| Vista | Uso |
| --- | --- |
| `consumo.entradas_compras` | Líneas de entrada; llave `compra_linea_key` (rowid de SIESA, estable entre cargas). El consecutivo EA se repite entre C.O.: nunca se cruza por documento sin C.O. |
| `consumo.cotizaciones_proveedores` | Todas las activaciones. `fecha_activacion` es medianoche de Bogotá guardada en UTC: se compara en hora de Bogotá |
| `consumo.productos` | Descripción y unidad de inventario |
| `consumo.centros_operacion` | Regional del C.O. del documento (`cod_regional`, `desc_regional`). No se usa `consumo.regionales`, que repite nombres |

El DWH publica estos datos de lunes a sábado hacia las 06:00, con lo movido hasta la noche
anterior. Las anulaciones de documentos con más de 45 días no llegan al DWH.

## Alzas de proveedor

El proveedor informa sus alzas en la plantilla «MAESTRA» del área o, si son pocas referencias,
alguien las digita en un formulario. En los dos casos queda una **solicitud en revisión**.
Quien gestiona `alzas-proveedor` la revisa: corrige líneas o deja alguna por fuera, y luego la
aprueba o la rechaza.

Código: `application/increases.py`, `infrastructure/increase_file.py` (plantilla y lector),
`infrastructure/supplier_items.py` (DWH) y `infrastructure/ledger.py` (paso al acumulado).
Tablas: migración `0082_costs_increase_requests`.

### La plantilla MAESTRA

Sigue el archivo del área `MAESTRA 50 - ASCENDER S.A. 23-11-2026.xlsx` (formulario P.17.F.04).

- **Nombre del archivo:** `MAESTRA <código> - <proveedor> <dd-mm-aaaa>.xlsx`.
- **Encabezado:** el del SIG, con el logo, el código P.17.F.04, la versión 01 y la fecha.
- **Instrucciones:** en las filas 4 a 7. Las columnas van en la fila 8, los datos desde la 9 y
  los paneles quedan congelados ahí.
- **Columnas:** las mismas de la MAESTRA, con sus colores.
  - Datos de JDH en gris: fecha, NIT, referencia, proveedor, descripción, unidad de empaque,
    estado, categoría, grupo impositivo y costo anterior.
  - SKU proveedor y EAN en verde: el proveedor los completa.
  - Costo nuevo sin impuesto saludable en amarillo: lo que el proveedor diligencia.
  - Al final, «Estado» (SI/NO) y una columna de descuento por cada regional.
- **Fórmulas:** costo anterior y nuevo con impuesto saludable (`costo × (1 + ICUI) + IBUA`),
  variación en pesos y en %, y estado del incremento (SUBIO, BAJO, ESTABLE), con los colores
  del área. Una fila sin costo nuevo queda en blanco; la plantilla anterior la marcaba «BAJO».
- **Listas:** OBSERVACIONES (DESCONTINUADO, CAMBIO_DE_REFERENCIA, DUPLICADO) y Estado (SI/NO).
  Validaciones de fecha, costo mayor que 0 y descuentos entre 0 % y 100 %.
- **Hoja `Motivos`:** se conserva oculta, como en la plantilla del área.

**Qué cambió frente a la MAESTRA del área (2026-11-23):**
- **Regional:** la columna «Regional» se quita y se agregan al final seis columnas de descuento:
  `Descuento ANTIOQUIA`, `BOGOTA`, `PEREIRA`, `BUCARAMANGA`, `CARTAGENA` y `BARRANQUILLA`.
  Así el ítem nunca se repite. Una regional vacía toma el «Descuento Regular».
- **ICUI:** sale del producto en SIESA, no de un BUSCARV contra `Motivos`.
- **Productos:** la plantilla trae los activos del proveedor que tienen cotización vigente,
  como la del área. Con ASCENDER (código 50), las 115 referencias del archivo del área
  coinciden con SIESA en SKU proveedor, EAN, grupo impositivo y costo anterior.
- **SKU proveedor:** conserva los ceros a la izquierda (`01080347`). El Excel del área los
  perdía.

**Al cargarla:**
- Las columnas se leen por nombre: el proveedor puede reordenarlas u ocultarlas. Solo son
  obligatorias «Referencia» y «Costo Nuevo sin Impuesto Saludable».
- Cuenta solo una fila con costo nuevo, y debe traer la fecha de incremento.
- Las fórmulas no se usan: la API recalcula los costos con impuesto, la variación y el estado.
- El ICUI y el IBUA que escriba el proveedor ganan sobre los de SIESA.
- Los porcentajes valen como fracción (0,05) o en puntos (5).
- «Estado» = NO deja la línea por fuera.
- **Problemas por fila:** referencia que no es un producto activo del proveedor, repetida,
  números ilegibles o porcentajes fuera de rango. Se reportan con su fila de la hoja, y uno
  solo bloquea la carga.

### Revisión y paso al acumulado

- **Revisión:** mientras está `en_revision`, cada línea se corrige (costo nuevo, fecha,
  descuentos, observación) o se deja por fuera (`accepted: false`).
- **Aprobar:** cada línea aceptada se escribe en el acumulado (`product_price_increases`):
  - `source = solicitud` y `request_line_id`, para saber de dónde vino;
  - fecha de notificación = día en que se creó la solicitud;
  - sin fecha de SIESA, así que el cotizador la toma como alza pendiente;
  - margen = margen Lista 1 del maestro de márgenes;
  - precio = costo nuevo con impuesto saludable / (1 − margen), redondeado al centavo;
  - los descuentos regular y por regional quedan en las observaciones.
- **Llave del acumulado:** la misma de la carga del libro (referencia + fecha de incremento +
  fecha de notificación). Una solicitud aprobada el mismo día con la misma fecha de incremento
  actualiza esa fila.
- **Rechazar:** exige el motivo, y nada llega al acumulado.

### API (`/api/v1/costs/increase-requests`, clave `alzas-proveedor`)

`read` basta para consultar y descargar; todo lo que cambia algo exige `manage`.

| Método y ruta | Uso |
| --- | --- |
| `GET /items?supplier_code=&q=` | Productos activos del proveedor con su costo anterior, para el formulario |
| `GET /template?supplier_code=` | La MAESTRA del proveedor (`Content-Disposition` con el nombre del área) |
| `POST /upload?supplier_code=&apply=` | Multipart `file` (y `note` opcional). Sin `apply`, simula. Devuelve `{applied, blocked, supplier_code, rows_read, lines, issues[{row_number, sku, problem}], request_id}` |
| `POST /` | Formulario manual: `{supplier_code, note?, lines: [{sku, new_cost, increased_on, supplier_sku?, ean?, icui_pct?, ibua?, regular_discount_pct?, regional_discounts?: {REGIONAL: fracción}, observation?}]}`. 201 con la solicitud |
| `GET /?status=&supplier=&page=&size=` | Solicitudes, la más reciente primero, con `counts` por estado |
| `GET /{id}` | Solicitud con `lines` |
| `GET /{id}/file` | El libro que devolvió el proveedor |
| `PATCH /{id}/lines/{line_id}` | `{accepted?, new_cost?, increased_on?, regular_discount_pct?, regional_discounts?, observation?}` |
| `POST /{id}/approve` | `{note?}` |
| `POST /{id}/reject` | `{note}` (obligatorio) |

**Cada línea trae:**
- **Ítem:** `sku`, `supplier_sku`, `ean`, `description`, `pack_factor`, `category`,
  `tax_group`, `icui_pct`, `ibua`.
- **Costos:** `old_cost`, `old_cost_with_tax`, `new_cost`, `new_cost_with_tax`,
  `variation_amount`, `variation_pct` (fracción), `change` (`subio`, `bajo`, `estable`) y
  `change_label`.
- **Lo que envió el proveedor:** `increased_on`, `regular_discount_pct`, `regional_discounts`,
  `observation`.
- **Revisión:** `accepted`, y `ledger_id` una vez aprobada.

El acumulado (`GET /commercial/price-increases`) ahora trae `source` y `request_line_id`.
