# Cotizador

Cada asesor comercial revisa, por cliente, la lista de precios que ese cliente viene pagando
y la ajusta. Parte del **último precio de venta** de cada referencia que se le vendió en una
ventana de 6 o 12 meses, lo cruza con el **costo** (el mayor entre la cotización del proveedor
y el inventario), el **margen objetivo** del [maestro de márgenes](margenes-productos.md) y los
**apoyos vigentes** del [maestro de apoyos](apoyos-proveedores.md) (igual que el
[validador de pedidos](validador-pedidos.md)). El asesor confirma la lista, BI la valida y
entonces se generan el Excel `ACTUALIZACION LISTAS PRECIOS` y el plano de importación de SIESA
(registros `0112`/`0126`/`0127`) para actualizar la lista del cliente o crear una nueva.

Módulo `quotations`. Migraciones `0037_quotations`, `0038_quotation_review`,
`0050_quotation_local_lines` (categoría de la línea y productos solo para cotización) y
`0051_quotation_offer` (oferta al cliente, cantidades y registro de productos solicitados). Área: Comercial
(`/comercial/cotizador`). Datos del DWH: [cotizador-dwh.md](cotizador-dwh.md).

Las reglas de costo, bodega, redondeo e impuesto son las del área de listas de precios, escritas
en la skill del proyecto [`lista-precios`](../.claude/skills/lista-precios/SKILL.md). El cotizador
las centraliza en `app/modules/quotations/domain/pricing.py`; si la skill cambia, se cambia ahí.

## Cómo se arma una cotización

1. **Cliente**: un NIT, con todas sus sucursales. Del DWH (`consumo.clientes`) se toman, por
   mayoría entre las sucursales activas, el canal (`cod_canal`), la lista de precios SIESA
   (`lista_precio_id`), el centro de operación que factura (`cod_co_factura`) y el vendedor.
   Si las sucursales no coinciden, la cotización lo advierte (`listas_distintas`, `co_distintos`).
   Las referencias del histórico quedan atadas a la lista que el cliente tiene asignada.
2. **Último precio** (`consumo.ventas`): por referencia, la última venta de la ventana (fecha,
   número de documento, precio, llave de línea). Solo ventas (no devoluciones) con cantidad y
   subtotal mayores que cero. El precio es `subtotal / cantidad`: neto de descuentos, **antes de
   IVA** y **por unidad de inventario** (una caja C45 vendida a 149.551 es 3.323,36 por unidad).
3. **Costo**: el **mayor** entre:
   - la **cotización vigente del proveedor** (`consumo.cotizaciones_proveedores`: vigente, ítem
     activo, unidad `UNID` o `KL`): `redondear(costo_bruto) × (1 + ICUI/100) + IBUA`, la mayor por
     referencia. **Lactalis** (NIT 800245795) cotiza por sucursal: la `003` para las listas de
     Centro (003, 10U, 10S, o un cliente que factura desde el CO 003) y la `001` para el resto;
   - el **costo promedio de inventario** (`consumo.inventario_fecha`, la foto de SIESA de la
     mañana) de la bodega de la lista, redondeado al peso. La bodega 2 aplica a las líneas
     CONGELADOS y REFRIGERADOS.

   El mayor se redondea hacia arriba y se multiplica por el factor de la familia de la lista:
   **1,004** en Generales, Salsamentarias y Market; **1** en Mayoristas. La cotización indica de
   dónde salió (`cost_source`: `cotizacion` o `inventario`) y trae los dos valores.
   - **Cotización fuera de rango**: una cotización mayor que **3 veces la mediana del último
     costo** de las instalaciones del ítem es un error de captura en SIESA (por ejemplo, el precio
     de la caja cargado como unidad). No se usa, se muestra (`discarded_quote_cost`) y la línea
     avisa `cotizacion_descartada`.
   - Un costo de **$1 o menos** (bodegas de obsoletos) no cuenta.
4. **Bodega de la lista** (de la skill; el DWH no tiene la relación lista → bodega):

   | Listas | Bodega 1 | Bodega 2 (congelados y refrigerados) |
   | --- | --- | --- |
   | 001, 1M, 10X | MDE1 | MDE3 |
   | 002, 10T, 10R | OCC1 | OCC16 |
   | 003, 10U, 10S | BTA9 | BTA8 |
   | 004, 11N | CTG1 | CTG15 |
   | 005, 10N | BGA1 | BGA1 |
   | 009, 1DR | BAQ1 | BAQ2 |
   | 35P | URA6 | URA6 |
   | 1 | el mayor entre MDE1, MDE3, MDE4, OCC1, OCC16, OCC14, BTA9, BTA8, BTA20, CTG1, CTG15, CTG14, BGA1, BGA11, BAQ1, BAQ2, BAQ10 | |

   Una lista que no está en la tabla (31A, 62Y…) usa las bodegas del **CO que factura** al
   cliente (001 → MDE1/MDE3, 002 → OCC1/OCC16, 003 → BTA9/BTA8, 004 → CTG1/CTG15, 005 → BGA1,
   009 → BAQ1/BAQ2). Sin CO, o con un CO fuera de esa lista (006, 007, 008, 010, 100, 200, 201),
   el mayor de las bodegas de la lista 1. La cabecera dice cuál se usó (`stock_source`).
5. **Impuesto**: solo el **impuesto al consumo de licores** (`consumo.producto_impuestos`, llave
   `0011` importados o `0024` nacionales). El valor es el que SIESA tiene en la lista del cliente
   (`precios_lista_vigentes.impoconsumo_valor`); si la lista no lo trae, el del maestro de
   márgenes según la **regional** del cliente: **Costa** si factura desde el CO `004`, Nacional
   para el resto (`regional` en la cabecera). El IVA no se muestra ni se suma.
6. **Margen objetivo**: el del producto para el canal del cliente, como en el validador:
   `MAYORISTAS Y DISTRIBUIDORES` y `SUBDISTRIBUIDOR` → Margen Mayoristas; `SALSAMENTARIAS` →
   Margen Salsamentarias; cualquier otro canal (o sin canal) → Margen Lista 1. Si un apoyo aplica
   y trae su propio objetivo (`target_margin_pct`, `min_margin_pct` o `list_margin_pct`), manda el
   del apoyo.
7. **Apoyo**: los apoyos activos (no anulados ni desactivados, sin el origen `Condiciones
   Especiales`) que cubren al cliente y la referencia hoy, con el mismo alcance que en el
   validador. Se aplica el de mayor importe entre los que traen dinero. Un apoyo en revisión o
   restringido a una bodega no se aplica: se lista como candidato con el motivo.
8. **Referencias inactivas**: una referencia inactiva en SIESA (`consumo.productos.estado`) se
   muestra marcada (`is_active: false`, `referencia_inactiva`) para reemplazarla. **No sale en el
   Excel ni en el plano**, no cuenta en las líneas cambiadas y no bloquea la confirmación. Se puede
   quitar de la cotización.
9. **Referencias agregadas**: el asesor puede sumar referencias que el cliente no compró
   (`origin: agregado`), una lista pegada o varias elegidas en la búsqueda. Se calculan igual pero
   no tienen último precio, margen actual ni variación. Se conservan al refrescar y se pueden
   quitar. Un cliente sin ventas en la ventana arranca vacío con el aviso `sin_ventas`.
10. **Productos solo para cotización** (`origin: local`): el asesor puede cotizar un producto que
    no existe en SIESA con su descripción, proveedor (opcional), precio antes de IVA e IVA (19, 5
    o 0). La API le da la referencia `LOC-<n>` (1, 2, 3… por cotización; un número no se reusa
    aunque se quite el producto). No tiene costo, objetivo, apoyo, unidad ni categoría, así que la
    regla no lo evalúa: `compliance` vacío, no cuenta en `changed_count`, `below_rule_count` ni
    `below_target_count`, y no impide confirmar (pero un precio local solo no basta: confirmar
    sigue exigiendo al menos un precio nuevo de una referencia de SIESA). **No sale en el Excel,
    en el plano ni en la plantilla.** Avisa `producto_local`. Se le cambia o quita el precio con
    `PUT /lines` (con `margin_pct` se omite: no tiene costo), se conserva igual al refrescar
    (después de las líneas reconstruidas) y se puede quitar.

## Oferta al cliente

La cotización también es la oferta que recibe el cliente (`GET /quotations/{id}/offer.xlsx`).
Se arma en `app/modules/quotations/domain/offer.py`, de donde leen la API y el Excel, así nunca
dicen cosas distintas. **Nunca lleva costo, márgenes, apoyos ni datos de la regla.**

- **Vigencia**: `validity_days` (1 a 365, 30 por defecto) desde `valid_from`, el día en que se fijó
  (hora de Bogotá); `valid_until = valid_from + validity_days`. Se fija al crear y cada vez que
  cambia la vigencia. Las cotizaciones guardadas antes de `0051` quedaron con 30 días desde el día
  en que se crearon.
- **Ajustes** (`PATCH /offer`, en cualquier estado y sin cambiarlo): `conditions` (hasta 20
  textos de 1 a 300 caracteres; se recortan los espacios y se quitan los vacíos),
  `include_seller` (sí por defecto), `quote_quantities` (no por defecto) y `offer_scope`:
  `changed` (por defecto: las líneas activas con precio nuevo, también los productos solo para
  cotización) o `all` (toda línea activa con precio de oferta). Una línea inactiva nunca va.
- **Asesor** (`seller_contact`): el vendedor asignado al cliente (`salesperson_code`) en
  `consumo.vendedores` (`nombre`, `correo` en minúscula, `celular`). Se guarda al crear y al
  refrescar; `null` sin código o sin fila. Las guardadas antes lo traen al refrescar.
- **Precio de oferta** (`offer_price`): el precio nuevo y, si no hay, el último; un producto solo
  para cotización, su precio nuevo. `in_offer` dice si la línea va en la oferta.
- **Cantidades** (`quantity`): unidades de inventario, mayor que cero, hasta dos decimales y
  10.000.000. Se escriben con `PUT /lines`; un lote que solo trae cantidades se acepta en
  cualquier estado (también pendiente de validación), no cambia el estado pero sí sube la versión.
  La plantilla masiva no las lee. Se conservan al refrescar, al revalidar y al promover.
- **Totales** (`offer_summary`, solo con `quote_quantities`), sobre las líneas de la oferta con
  cantidad, redondeando al peso (mitad hacia arriba):

  ```
  base de la línea     = redondeo(cantidad × precio de oferta)
  IVA de la línea      = redondeo(base × IVA / 100)         (IVA desconocido = 0 %)
  licores de la línea  = redondeo(cantidad × impuesto de licores por unidad)
  subtotal             = Σ bases;  total = subtotal + Σ IVA + Σ licores
  ```

  Los grupos de IVA (`vat_groups`) van de la tarifa más alta a la más baja.
- **Excel** `Cotizacion <Cliente> <AAAA-MM-DD>.xlsx` (nombre en ASCII), hoja `Cotizacion`, para
  quien pueda leer la cotización y en cualquier estado: título «Cotización»; cliente, NIT, canal,
  fecha de emisión y «Válida hasta»; el asesor (nombre, correo, teléfono) si `include_seller` y
  hay `seller_contact`; los ítems de la oferta (`Referencia` —«Por crear» en un producto solo para
  cotización—, `Descripción`, `Unidad`, `Precio sin IVA`, `IVA %`, `Imp. licores`, `Precio con
  IVA` = precio × (1 + IVA) + licores, redondeado al peso) y, con cantidades, `Cantidad`,
  `Subtotal`, `IVA` y `Total` más los totales por grupo de IVA, subtotal, IVA, impuesto de licores
  y total; al final «Condiciones de la oferta»: primero «Precios válidos hasta el <fecha larga>»
  y luego las del asesor.

## Productos solicitados

Cada producto solo para cotización queda en el registro `quotation_requested_products`
(`solicitado`, `creado` o `descartado`) para que BI lo cree en SIESA. Quitar la línea o borrar la
cotización no borra el registro. Una línea local guardada antes de `0051` recibió su propio
registro.

- `POST /local-lines` crea el registro (precio estimado = el precio de la línea) y lo enlaza. Con
  `requested_product_id` (un registro `solicitado`) cotiza otra vez ese producto: toma su
  descripción, proveedor e IVA, y el precio del cuerpo o, si no viene, el estimado.
- BI (quien valida) lo marca con `PATCH /requested-products/{id}`: `creado` exige una referencia
  SIESA que exista y esté activa; volver a `solicitado` (o pasar a `descartado`) la borra. `note`
  cambia solo si se envía.
- Con el registro `creado`, `POST /{id}/lines/{sku}/promote` cambia la línea local por la
  referencia SIESA, calculada como una agregada (`agregado`) en su lugar, con su precio nuevo y su
  cantidad. Si la cotización ya tiene esa referencia, la línea local se quita y su cantidad pasa a
  la referencia cuando esta no tiene.

## Fórmulas

```
costo                 = techo(máx(cotización, inventario)) × factor de la familia
apoyo por unidad      = participación del apoyo sobre el costo × costo
margen sin apoyo      = (precio − costo) / precio
margen con apoyo      = (precio − (costo − apoyo)) / precio
variación             = precio nuevo / último precio − 1
margen mínimo         = margen objetivo − 1 punto
precio mínimo         = techo((costo − apoyo) / (1 − margen mínimo))           en pesos
precio óptimo         = redondeo((costo − apoyo) / (1 − margen objetivo))      en pesos
precio a un margen m  = redondeo((costo − apoyo) / (1 − m)), nunca bajo el mínimo si m cumple
```

El redondeo es al peso: normal en Generales y Mayoristas, hacia arriba en Salsamentarias y Market
(como los archivos del área). Los precios nuevos van en **pesos enteros**.

## Regla general

Un precio nuevo puede dejar el margen (con apoyo) **como máximo 1 punto por debajo del margen
objetivo**: objetivo 20 % → mínimo 19 %. Exactamente 1 punto todavía cumple.
`QUOTATIONS_MAX_POINTS_BELOW_TARGET` (en puntos, por defecto `1`).

Cada línea con precio nuevo dice su `compliance`:

| Valor | Cuándo |
| --- | --- |
| `cumple` | el margen nuevo alcanza el mínimo |
| `bajo_minimo` | el margen nuevo queda más de 1 punto bajo el objetivo |
| `sin_objetivo` | el producto no tiene margen para el canal y ningún apoyo trae objetivo |
| `sin_costo` | no hay costo: no se puede calcular el margen |

**Alerta bajo objetivo** (`below_target`): el último precio deja el margen, contando el apoyo,
por debajo del mínimo. La línea se resalta para revisarla y actualizarla; si un apoyo la cubre
(el margen con apoyo alcanza el mínimo), no se resalta. La cabecera cuenta cuántas hay
(`below_target_count`).

Guardar precios nunca se bloquea: la línea queda marcada. **Confirmar** sí exige que ninguna
referencia activa con precio nuevo esté en `bajo_minimo`, `sin_objetivo` o `sin_costo`. Un
producto solo para cotización no tiene `compliance` y no entra en esta cuenta.

## Estados

| Estado | Quién | Qué pasa |
| --- | --- | --- |
| `borrador` | asesor | edita precios, márgenes, referencias; cualquier cambio vuelve aquí |
| `pendiente_validacion` | asesor confirma (`/confirm`) | queda bloqueada para editar; elige actualización o creación, la lista y la fecha de activación |
| `actualizada` / `creada` | BI valida (`/validate`) | se liberan el Excel y el plano; `actualizada` si actualizó la lista del cliente, `creada` si creó una nueva |

Quien valida también puede **devolverla** a borrador con una nota (`/return`, `review_note`).
El asesor puede retirarla de la validación por el mismo camino. Validan los miembros del área
`QUOTATIONS_APPROVER_AREA_SLUG` (por defecto `bi`, que lleva las listas de precios) y los
administradores; ellos leen todas las cotizaciones aunque no sean del área comercial.

## Actualización o creación de lista

- **Actualización** (`actualizacion`): los precios se escriben en la **lista que el cliente tiene
  asignada** en SIESA; no se puede escoger otra. Si la lista es compartida, el cambio llega a todos
  sus clientes (`price_list_customers` y el aviso `lista_compartida`).
- **Creación** (`creacion`): se crea una lista nueva con el código que da el asesor (1 a 3 letras o
  números, libre en SIESA: se consulta `consumo.listas_precios`) y su descripción (por defecto
  `COTIZADOR <cliente>`, en ASCII y mayúsculas, máximo 40).
- **Lista 1**: un cliente en la lista `1` (GENERAL, unos 28.000 clientes) **siempre va por
  creación**: la lista 1 no se actualiza por este medio (aviso `lista_general`).
- Asignar la lista nueva al cliente se hace **a mano en SIESA**: el registro `0201` de clientes
  reescribe la sucursal completa (vendedor, cupo, dirección…) y no se automatiza.

## Plantilla para cambios masivos

`GET /quotations/{id}/template.xlsx` descarga la cotización con todos sus datos (hoja
`Cotizacion`: referencia, descripción, unidad, estado, proveedor, último precio, costo, apoyo,
margen actual, objetivo, precio mínimo, precio óptimo, **Precio nuevo**, **Margen nuevo** y la
alerta) y una hoja `Control` con el id de la cotización. El asesor cambia el precio o el margen de
las referencias que quiera y la sube completa (`POST /quotations/{id}/template`):

- por fila, **manda la celda que cambió**: un precio distinto al guardado; si no, un margen
  distinto (fracción `0,30` o `30 %`), que se convierte en precio con la fórmula de arriba;
- las dos celdas vacías **quitan** el precio nuevo;
- una referencia que la cotización no tiene se **agrega** (si existe y está activa);
- las filas ilegibles o que no se pueden calcular se informan con su número de fila
  (`template_result.skipped`); el resto se guarda;
- los productos solo para cotización no vienen en la plantilla; una fila con una referencia
  `LOC-<n>` se informa en `skipped` y no se toca;
- un archivo de otra cotización se rechaza.

## Formato de actualización

Solo las referencias **activas** de SIESA con precio nuevo mayor que cero (nunca un producto
solo para cotización). Precio por **unidad de
inventario**, antes de IVA, en pesos enteros. La unidad es el código SIESA del DWH
(`unidad_inventario_id`, `UNID`).

**Excel** `ACTUALIZACION LISTAS PRECIOS_<correo>.xlsx`, dos hojas, con el formato del Plano del
área: lista como texto (`001`), referencia como número, fecha `AAAAMMDD` como número entero con
formato `0`, precio como número, Calibri Light 10.

| Hoja | Columnas |
| --- | --- |
| `Precios` | `LISTA DE PRECIOS`, `REFERENCIA`, `AAAAMMDD`, `UNIDAD_MEDIDA`, `PRECIO DE LISTA` |
| `Impuestos en valor` | `LISTA DE PRECIOS`, `REFERENCIA`, `AAAAMMDD`, `UNIDAD_MEDIDA`, `LLAVE IMPUESTO`, `VALOR IMPUESTO` (solo licores) |

**Plano SIESA** (Impodatos): ASCII de ancho fijo, cada línea termina en CRLF. Diccionario
oficial en la base de SIESA (`t661_imp_tablas`, `t662_imp_campos`). Numéricos con ceros a la
izquierda; dinero `000000000000000.0000`; alfanuméricos alineados a la izquierda con espacios;
fechas `AAAAMMDD`.

| Registro | Largo | Campos (posición, largo) |
| --- | --- | --- |
| `0000` inicial | 18 | consecutivo (1,7), `0000` (8,4), subtipo `00`, versión `01`, compañía `001` |
| `0112` lista de precios, versión 01 (solo creación) | 101 | consecutivo, `0112`, `00`, `01`, `001`, actualiza `0` (19,1: crea, no toca una existente), código (20,3), descripción (23,40), moneda `COP` (63,3), tipo `1` (66), impuesto `0` y precio sugerido `0` (67–68), lista base en blanco (69,3), factor base en ceros (72,20), precisión base `0000.0000` (92,9), aplica ítem base `0` (101) |
| `0126` precio, versión 03 | 254 | consecutivo, `0126`, `00`, `03`, `001`, actualiza `1` (19,1), lista (20,3), ítem `0000000` (23,7), referencia (30,50), barras (80,20), extensión 1 (100,20), extensión 2 (120,20), activación (140,8), inactivación en blanco (148,8), promoción `00000000` (156,8), unidad (164,4), precio (168,20), mínimo (188,20), máximo (208,20), sugerido (228,20), puntos `0000000` (248,7) |
| `0127` impuesto en valor, versión 03 | 175 | igual hasta extensión 2, activación (140,8), unidad (148,4), llave de impuesto (152,4: `0011`/`0024`), valor (156,20) |
| `9999` final | 18 | consecutivo, `9999`, `00`, `01`, `001` |

Una lista nueva lleva su `0112` justo después del `0000`; después van sus `0126` y `0127`. El
archivo se descarga como `precios-lista<lista>-<NIT>-<AAAAMMDD>.txt`. El archivo de ejemplo
`193257H10M2requerimientos@jdh.com.co.txt` es la prueba dorada del `0126`/`0127`; el `0112` se
probó contra el ejemplo que armó el agente del DWH desde el diccionario. Falta cargar uno de cada
uno en el ambiente de pruebas de SIESA.

## API (`/api/v1/quotations`)

Leer exige ser administrador, miembro del área comercial (`QUOTATIONS_MANAGER_AREA_SLUG`) o del
área que valida (`QUOTATIONS_APPROVER_AREA_SLUG`). Cotizan las dos áreas: quien gestiona el
cotizador en Comercial o en BI. BI y los administradores tienen el módulo completo: editan
(refrescar, precios, agregar referencias, plantilla masiva, confirmar) y borran cualquier
cotización. Comercial, solo las que creó. Validar, solo quien valida.

Qué cotizaciones ve cada uno: quien valida (BI y administradores) y los moderadores ven todas; el
resto, también supervisores y líderes de Comercial, solo las que creó. `GET /quotations` les
ignora `mine=false` y `GET /quotations/{id}` responde 403 con una ajena. `created_by` (correo)
dice quién la creó. `/permissions` y `/rules` están abiertas. Las operaciones que leen el DWH (crear,
refrescar, agregar, confirmar una creación, «mis clientes») responden 503 si no está disponible.
Decimales en texto; márgenes y variación como fracción (`0.18` = 18 %).

| Método y ruta | Uso |
| --- | --- |
| `GET /quotations/permissions` | `{can_manage, can_validate, seller_linked, seller}` |
| `GET /quotations/rules` | Reglas vigentes por secciones (`precio`, `margen`, `regla`, `formato`) |
| `GET /quotations/customers` | Clientes para cotizar: `scope=mine` (defecto) o `all`, `q`, `page`, `size` (≤ 100) |
| `POST /quotations` | `{customer_nit, window_months: 6 \| 12, validity_days?: 1..365 (30)}` → 201 con la cotización (borrador) |
| `GET /quotations/requested-products` | Registro de productos solicitados: `status`, `q` (descripción o proveedor), `page`, `size`. Primero los `solicitado`, luego los más nuevos. `{items, total, page, size}`; cada ítem `{id, description, supplier_name, estimated_price, vat_pct, status, status_label, siesa_sku, note, created_by, created_at, updated_by, updated_at, quotations: [{id, customer_name, customer_nit, status, status_label, created_by}]}` con solo las cotizaciones que quien consulta puede abrir. Para quien cotiza o valida |
| `PATCH /quotations/requested-products/{id}` | `{status, siesa_sku?, note?}` → el ítem. Solo quien valida (403). `creado` exige una referencia SIESA existente y activa (422) |
| `GET /quotations` | `mine` (defecto `true`; quien valida usa `mine=false`), `status`, `customer_nit`, `q`, `page`, `size` |
| `GET /quotations/{id}` | Cotización completa |
| `PUT /quotations/{id}/lines` | `{version, lines: [{sku, new_price?, margin_pct?, quantity?}]}`. `quantity` (texto o número; `null` la quita) va sola o con `new_price`, nunca con `margin_pct`. Un lote con **solo** cantidades se acepta en cualquier estado, no cambia el estado y sube la versión; uno que toca precios sigue las reglas de siempre. `new_price: null` quita el precio; `margin_pct` (fracción) le pone a esa referencia el precio de ese margen: así se lleva una **selección** a un margen estándar. Las que no se pueden calcular (sin costo, o un producto solo para cotización) vuelven en `skipped: [{sku, reason}]`; 422 si ninguna. 409 por versión o si está pendiente de validación |
| `POST /quotations/{id}/lines` | Pegar una lista o enviar una selección múltiple: `{version, text}` y/o `{skus: [...]}`; hasta 500. Devuelve `import_result: {added, revalidated, not_found, inactive, repeated}`. Una referencia `LOC-<n>` no se busca en SIESA: va en `not_found` |
| `POST /quotations/{id}/local-lines` | Producto solo para cotización: `{version, description, supplier_name?, price, vat_pct}` o `{version, requested_product_id, price?}` (un registro `solicitado`; 422 si no existe o no está solicitado). Crea o enlaza su registro de productos solicitados. `description` de 1 a 120 caracteres (se recortan los espacios), `supplier_name` opcional hasta 120, `price` (texto o número) en pesos enteros mayor que cero antes de IVA, `vat_pct` 19, 5 o 0. Devuelve la cotización con la línea `LOC-<n>` al final. Mismos permisos y estados que agregar referencias: 409 por versión o si está pendiente de validación; 422 con `detail` en español si un campo no sirve |
| `DELETE /quotations/{id}/lines/{sku}?version=N` | Quita una referencia **agregada**, **inactiva** o **solo para cotización** (su registro de productos solicitados queda) |
| `POST /quotations/{id}/lines/{sku}/promote` | `{version}`: cambia el producto `LOC-<n>` por su referencia SIESA cuando su registro está `creado` (si no, 422). Mismos permisos y estados que agregar referencias |
| `PATCH /quotations/{id}/offer` | `{version, validity_days?, conditions?, include_seller?, quote_quantities?, offer_scope?: changed \| all}`: solo cambian las llaves enviadas (`null` deja el valor). En cualquier estado y sin cambiarlo; sube la versión; 409 por versión. Una vigencia nueva empieza hoy |
| `GET /quotations/{id}/offer.xlsx` | La oferta para el cliente, en cualquier estado, para quien puede leer la cotización |
| `POST /quotations/{id}/refresh` | `{version}`: reconstruye desde el DWH conservando los precios nuevos; los productos solo para cotización quedan iguales, al final |
| `GET /quotations/{id}/template.xlsx` | La plantilla con los datos de la cotización |
| `POST /quotations/{id}/template` | `multipart/form-data`: `file` y `version`. Devuelve `template_result: {updated, cleared, added, unchanged, not_found, inactive, skipped: [{sku, reason, row_number}]}`: listas de SKU, salvo `unchanged`, que es el **número** de filas sin cambio |
| `POST /quotations/{id}/confirm` | `{version, list_action?, price_list_id?, new_list_name?, activation_date?}` → `pendiente_validacion`. 422 si alguna no cumple, si la lista 1 se quiere actualizar, si la creación no trae código o el código existe |
| `POST /quotations/{id}/return` | `{version, note}` → `borrador`. Quien valida debe escribir la nota |
| `POST /quotations/{id}/validate` | `{version}` → `actualizada` o `creada`. Solo quien valida |
| `GET /quotations/{id}/export.xlsx` | El Excel (solo `actualizada`/`creada`; si no, 409) |
| `GET /quotations/{id}/export.txt` | El plano SIESA (solo `actualizada`/`creada`; si no, 409) |
| `DELETE /quotations/{id}` | 204. Quien la creó o un administrador |

**Resumen** (lista): `id, customer_nit, customer_name, channel, price_list_id, window_months,
status, status_label, list_action, list_action_label, export_price_list_id, line_count,
changed_count, below_rule_count, below_target_count, created_by, created_at, updated_at,
confirmed_at, validated_at, version, validity_days, valid_from, valid_until` (fechas
`AAAA-MM-DD`).

**Cotización completa**: el resumen más `channel_code, price_list_name, price_list_customers,
billing_center_code, regional, regional_label, price_family, price_family_label, stock_source,
salesperson_code, salesperson_name, built_at, cost_cut_date, activation_date, new_list_name,
max_points_below_target, warnings: [{code, label}], confirmed_by, validated_by, review_note,
updated_by, conditions, include_seller, quote_quantities, offer_scope, seller_contact: {code,
name, email, phone} | null, offer_summary: {item_count, subtotal, vat_groups: [{vat_pct, base,
vat}], liquor_tax, total} | null` y `lines`.

**Línea**:

| Campo | |
| --- | --- |
| `sku`, `description`, `unit`, `pack_factor` | referencia, descripción, unidad de inventario y empaque |
| `category` | categoría del producto en SIESA (`consumo.productos.categoria`); `null` si no se conoce y en un producto solo para cotización. Las cotizaciones guardadas antes de `0050` la traen al refrescar |
| `vat_pct` | IVA de venta en puntos (`19` = 19 %); en un producto solo para cotización, el que escribió el asesor |
| `is_active` | `false` si está inactiva en SIESA: se muestra para reemplazarla y no se exporta |
| `supplier_code`, `supplier_name` | proveedor comercial |
| `last_price`, `last_gross_price`, `last_discount_pct`, `last_sale_date`, `last_document`, `last_sale_unit`, `last_quantity`, `sales_in_window` | último precio y su venta |
| `list_price` | precio vigente de la lista del cliente, por unidad de inventario |
| `value_tax_key`, `value_tax_name`, `value_tax_amount` | impuesto de licores por unidad, según la lista o la regional |
| `cost`, `cost_source`, `cost_source_label` | costo de la regla y de dónde salió |
| `quote_cost`, `inventory_cost`, `discarded_quote_cost` | la cotización usada, el inventario de la bodega y la cotización descartada por fuera de rango |
| `target_margin_pct`, `margin_basis`, `min_margin_pct` | objetivo y mínimo permitido |
| `min_price`, `optimal_price` | precio mínimo que cumple la regla y precio al margen objetivo |
| `below_target` | el último precio deja el margen bajo el mínimo (alerta) |
| `support`, `support_candidates` | apoyo aplicado y los que no se aplicaron |
| `current_margin_pct`, `current_margin_with_support_pct` | margen del último precio sin y con apoyo |
| `new_price`, `new_margin_pct`, `new_margin_with_support_pct`, `variation_pct`, `compliance` | el ajuste del asesor; en un producto solo para cotización solo `new_price` (el resto `null`) |
| `price_with_taxes` | precio nuevo (o último) más el impuesto de licores, sin IVA |
| `problems`, `problem_labels` | `sin_costo`, `sin_margen_objetivo`, `producto_no_sincronizado`, `apoyo_condicionado`, `apoyo_en_revision`, `apoyo_fuera_bodega`, `referencia_inactiva`, `cotizacion_descartada`, `producto_local` |
| `origin`, `origin_label` | `historico` (Histórico), `agregado` (Agregado) o `local` (Solo cotización) |
| `quantity` | unidades de inventario que llevaría el cliente, o `null` |
| `offer_price`, `in_offer` | precio de oferta y si la línea va en la oferta (según `offer_scope`) |
| `requested_product` | en un producto solo para cotización, `{id, status, siesa_sku}` de su registro; si no, `null` |

Un producto solo para cotización trae `sku` `LOC-<n>`, `origin: local`, `description`,
`supplier_name` y `vat_pct` como se enviaron, `new_price`, `is_active: true`,
`problems: ["producto_local"]`, `support_candidates: []`, `sales_in_window: 0`,
`below_target: false` y `null` en `supplier_code`, `unit`, `pack_factor`, `category`, los `last_*`,
`list_price`, el impuesto, el costo, el objetivo, los márgenes, los precios mínimo y óptimo, el
apoyo, `variation_pct` y `compliance`.

Avisos de la cotización (`warnings`): `sin_canal`, `cliente_inactivo`, `listas_distintas`,
`co_distintos`, `lista_compartida`, `sin_lista`, `sin_ventas`, `lista_general`.

## Configuración

| Variable | Defecto | Uso |
| --- | --- | --- |
| `QUOTATIONS_MANAGER_AREA_SLUG` | `comercial` | quién cotiza (además de BI) |
| `QUOTATIONS_APPROVER_AREA_SLUG` | `bi` | quién valida; también cotiza |
| `QUOTATIONS_MAX_POINTS_BELOW_TARGET` | `1` | puntos bajo el objetivo que se toleran |
| `QUOTATIONS_MAX_LINES` | `3000` | referencias máximas por cotización |
| `QUOTATIONS_MAX_UPLOAD_MB` | `10` | tamaño máximo de la plantilla que se sube |
| `QUOTATIONS_SIESA_COMPANY` | `001` | compañía del plano |
| `QUOTATIONS_DWH_TIMEOUT_SECONDS` | `30` | segundos por consulta al DWH del cotizador |

El usuario del DWH debe leer en `consumo`: `ventas`, `clientes`, `productos`,
`producto_impuestos`, `inventario_fecha`, `cotizaciones_proveedores`, `precios_lista_vigentes`,
`listas_precios` y `vendedores` (`svc_bi` sí; el rol `dir_comercial` no alcanza).

El histórico de un cliente con cientos de sucursales tarda de 1 a 6 segundos y, con el DWH
ocupado, hasta 27. Por eso el cotizador tiene su propio tiempo límite; `DataProviderConnection.fetch`
acepta `timeout` por consulta. La consulta de costo (cotización e inventario de N referencias en
una sola ida) tarda unos 100 ms para 8 referencias y 1,4 s para una lista completa de 3.000.

## Validación (2026-11-03, segunda entrega)

Contra el DWH real y una copia de la base local migrada a `0038_quotation_review`:

| Cliente | Lista / CO | Resultado |
| --- | --- | --- |
| Distribuidora, 119 referencias | `10U` / 003 | Bodega BTA9/BTA8 por la lista, familia Mayoristas (sin factor). Costo de cotización en 111 líneas e inventario en 8; 57 con alerta bajo objetivo. Se armó en 1,2 s. Al agregar 1081 (Lactalis) la cotización de la sucursal 003 da 13.104, como verificó el agente del DWH |
| Colegio, 678 referencias | `87Q` / 002 (mayoría de sucursales) | Bodega OCC1/OCC16 por el CO; 12779 Kit Kat con la cotización de 80.552 descartada y el costo del inventario (2.780) |
| Restaurante en la Costa, 244 referencias | `27X` / 004 | Regional Costa: 18973 con 4.144 y 21700 con 49.728 (Nacional: 4.036 y 48.432) |
| Mayorista en la lista 1, 104 referencias | `1` / 009 | Creación obligatoria (actualizar la lista 1 da 422; el código `1M` existe y da 422). Margen 25 % a toda la selección en 135 ms; confirmada como `C97`, validada → `creada`; plano 18/101/254… con el `0112` primero |
| Plantilla (lista 10U) | | 5 precios óptimos y 5 márgenes de 30 % aplicados, 1081 agregada a 20 %, una referencia inexistente y una celda `abc` informadas con su fila; 225 ms |

## Pendiente de definir con el negocio

1. Si el objetivo de un apoyo debe mandar sobre el margen del producto (hoy sí, como en el
   validador).
2. Qué hacer con referencias sin margen para el canal (hoy no se pueden confirmar con precio nuevo).
3. Apoyos con cantidad mínima: la cotización no tiene cantidades; hoy se aplican y se advierte.
4. Vínculo asesor ↔ vendedor: solo por correo. La cartera es una sugerencia, no un bloqueo.
5. **CO sin bodega definida** (006 Yumbo, 007 Urabá, 008 Valledupar, 010 Quindío, 100, 200, 201):
   hoy toman el mayor de las bodegas de la lista 1. ¿Valledupar (008, 201) paga el impuesto de
   licores de la Costa? Hoy solo el CO 004.
6. **Código de las listas nuevas**: hoy lo escribe el asesor. El agente del DWH propone reservar
   `C01`–`C99` (sin uso hoy) para las que crea el cotizador.
7. **Estado Bloqueado**: SIESA tiene Activo/Inactivo/Bloqueado, pero `consumo.productos.estado`
   reduce el bloqueado a `I` (hoy 11 ítems). Si se quiere distinguir, pedir `estado_siesa` en el DWH.
8. **Alzas de precio** (Acumulado de alzas en Drive, el «Costo Final» de la skill): no está en el
   DWH, así que el costo del cotizador no las incluye.
9. Confirmar con el área que **BI** es quien valida y que el factor **1,004** aplica a todas las
   listas que no son mayoristas.
10. Carga directa a SIESA por webservice (la conexión del DWH es de solo lectura) y asignación
    automática de la lista nueva al cliente (`0201`).
