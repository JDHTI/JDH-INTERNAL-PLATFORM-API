# Cotizador

Cada asesor comercial revisa, por cliente, la lista de precios que ese cliente viene pagando
y la ajusta. Parte del **último precio de venta** de cada referencia que se le vendió en una
ventana de 6 o 12 meses, lo cruza con el **costo actual**, el **margen objetivo** del
[maestro de márgenes](margenes-productos.md) y los **apoyos vigentes** del
[maestro de apoyos](apoyos-proveedores.md) (igual que el [validador de pedidos](validador-pedidos.md)),
y genera el formato de actualización de lista de precios para SIESA: el Excel
`ACTUALIZACION LISTAS PRECIOS` y el plano de importación (registros `0126`/`0127`).

Módulo `quotations`. Migración `0037_quotations`. Área: Comercial (`/comercial/cotizador`).
Datos del DWH: [cotizador-dwh.md](cotizador-dwh.md).

## Cómo se arma una cotización

1. **Cliente**: un NIT, con todas sus sucursales. Del DWH (`consumo.clientes`) se toman, por
   mayoría entre las sucursales activas, el canal (`cod_canal`), la lista de precios SIESA
   (`lista_precio_id`), el centro de operación que factura (`cod_co_factura`) y el vendedor.
   Si las sucursales no coinciden, la cotización lo advierte (`listas_distintas`, `co_distintos`).
2. **Último precio** (`consumo.ventas`): por referencia, la última venta de la ventana (fecha,
   número de documento, precio, llave de línea). Solo ventas (no devoluciones) con cantidad y
   subtotal mayores que cero. El precio es `subtotal / cantidad`: neto de descuentos, **antes de
   IVA** y **por unidad de inventario** (una caja C45 vendida a 149.551 es 3.323,36 por unidad).
3. **Costo actual** (`consumo.inventario_corte`, último corte): costo promedio por instalación
   (`costo_prom_unit_ins`) del CO que factura al cliente. Si ese CO no tiene existencia, el
   promedio ponderado de todas las bodegas (`cost_is_global`, sin aviso en la línea).
4. **Impuesto**: solo el **impuesto al consumo de licores** (`consumo.producto_impuestos` con
   `tipo_calculo = 'valor'` y clase de licores: `0011` importados, `0024` nacionales), con el
   valor de la lista del cliente en `consumo.precios_lista_vigentes`; si la lista no lo trae, el
   `IMPTOLIC` del maestro de márgenes. El IVA no se muestra ni se suma, y el impoconsumo no entra
   en la cotización.
5. **Margen objetivo**: el del producto para el canal del cliente, como en el validador:
   `MAYORISTAS Y DISTRIBUIDORES` y `SUBDISTRIBUIDOR` → Margen Mayoristas; `SALSAMENTARIAS` →
   Margen Salsamentarias; cualquier otro canal (o sin canal) → Margen Lista 1. Si un apoyo aplica
   y trae su propio objetivo (`target_margin_pct`, `min_margin_pct` o `list_margin_pct`), manda el
   del apoyo.
6. **Apoyo**: los apoyos activos (no anulados ni desactivados, sin el origen `Condiciones
   Especiales`) que cubren al cliente y la referencia hoy, con el mismo alcance que en el
   validador (proveedor, cliente, referencia, canal, regional, unidad de negocio). Se aplica el
   de mayor importe entre los que traen dinero (`pct_costo`, `pct_precio_lista`, `valor_unidad`,
   `costo_neto`, `precio_neto`). Un apoyo en revisión o restringido a una bodega no se aplica:
   se lista como candidato con el motivo. Un apoyo con cantidad mínima se aplica y se advierte.
7. **Referencias agregadas**: el asesor puede sumar referencias que el cliente no compró en la
   ventana (`origin: agregado`) para cotizarle N productos. Se calculan igual (costo, objetivo,
   apoyos, impuesto de licores) pero no tienen último precio, margen actual ni variación. Se
   conservan al refrescar y se pueden quitar; las del histórico no se quitan (basta con dejar
   vacío su precio nuevo). Un cliente sin ventas en la ventana arranca vacío con el aviso
   `sin_ventas`.

## Fórmulas

```
apoyo por unidad      = participación del apoyo sobre el costo × costo
margen sin apoyo      = (precio − costo) / precio
margen con apoyo      = (precio − (costo − apoyo)) / precio
variación             = precio nuevo / último precio − 1
margen mínimo         = margen objetivo − 1 punto
precio mínimo         = (costo − apoyo) / (1 − margen mínimo)
```

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

Guardar precios nunca se bloquea: la línea queda marcada. **Generar** el formato sí exige que
ninguna línea cambiada esté en `bajo_minimo`, `sin_objetivo` o `sin_costo`.

## Lista de precios destino

Por defecto, la lista SIESA del cliente. Las listas se comparten (la `1` GENERAL tiene unos
28.000 clientes, y 815 mayoristas también están en ella): escribir un precio en una lista
compartida lo cambia a todos sus clientes. La cotización trae `price_list_customers` (clientes
activos con esa lista) para advertirlo, y al generar se puede elegir otra lista
(`price_list_id`, máximo 3 caracteres).

## Formato de actualización

Solo las líneas con precio nuevo. Precio por **unidad de inventario**, antes de IVA. La unidad
es el código SIESA del DWH (`unidad_inventario_id`, `UNID`), no el nombre que guarda el maestro
local (`UNIDAD`), porque el plano admite 4 caracteres.

**Excel** `ACTUALIZACION LISTAS PRECIOS_<correo>.xlsx`, dos hojas:

| Hoja | Columnas |
| --- | --- |
| `Precios` | `LISTA DE PRECIOS`, `REFERENCIA`, `AAAAMMDD`, `UNIDAD_MEDIDA`, `PRECIO DE LISTA` |
| `Impuestos en valor` | `LISTA DE PRECIOS`, `REFERENCIA`, `AAAAMMDD`, `UNIDAD_MEDIDA`, `LLAVE IMPUESTO`, `VALOR IMPUESTO` (solo líneas con impuesto de licores) |

**Plano SIESA** (Impodatos): ASCII de ancho fijo, cada línea termina en CRLF. Diccionario
oficial en la base de SIESA (`t661_imp_tablas`, `t662_imp_campos`). Numéricos con ceros a la
izquierda; dinero `000000000000000.0000` (15 enteros, punto, 4 decimales); alfanuméricos
alineados a la izquierda con espacios; fechas `AAAAMMDD`.

| Registro | Largo | Campos (posición, largo) |
| --- | --- | --- |
| `0000` inicial | 18 | consecutivo (1,7), `0000` (8,4), subtipo `00`, versión `01`, compañía `001` |
| `0126` precio, versión 03 | 254 | consecutivo, `0126`, `00`, `03`, `001`, actualiza `1` (19,1), lista (20,3), ítem `0000000` (23,7), referencia (30,50), barras (80,20), extensión 1 (100,20), extensión 2 (120,20), activación (140,8), inactivación en blanco (148,8), promoción `00000000` (156,8), unidad (164,4), precio (168,20), mínimo (188,20), máximo (208,20), sugerido (228,20), puntos `0000000` (248,7) |
| `0127` impuesto en valor, versión 03 | 175 | igual hasta extensión 2, activación (140,8), unidad (148,4), llave de impuesto (152,4: el cotizador solo escribe `0011`/`0024` licores), valor (156,20) |
| `9999` final | 18 | consecutivo, `9999`, `00`, `01`, `001` |

El archivo se descarga como `precios-lista<lista>-<NIT>-<AAAAMMDD>.txt`; el nombre que pone el
importador de Excel de SIESA (`193257H10M2<correo>.txt`) no es un requisito del plano.
El `1` de la posición 19 reemplaza el precio si ya existe. Mínimo, máximo y sugerido van en
ceros. Cada `0127` va después del `0126` de su referencia, con la misma lista, fecha y unidad.
El archivo de ejemplo `193257H10M2requerimientos@jdh.com.co.txt` es la prueba dorada del
generador. Subirlo a SIESA por webservice no está en esta entrega: el login del DWH en SIESA es
de solo lectura y el importador no valida sin escribir.

## API (`/api/v1/quotations`)

Todo exige ser administrador o miembro del área `QUOTATIONS_MANAGER_AREA_SLUG` (por defecto
`comercial`), salvo `/permissions` y `/rules`. Las operaciones que leen el DWH (crear,
refrescar, «mis clientes») responden 503 si no está disponible; las cotizaciones guardadas se
siguen abriendo. Decimales en texto; márgenes y variación como fracción (`0.18` = 18 %).

| Método y ruta | Uso |
| --- | --- |
| `GET /quotations/permissions` | `{can_manage, seller_linked, seller}`: `seller` es `{code, name, email}` del vendedor del DWH cuyo correo es el del usuario, o `null` |
| `GET /quotations/rules` | Reglas vigentes por secciones (`code`, `title`, `rules: [{code, title, description, effect, parameters}]`), como `/order-validations/rules` |
| `GET /quotations/customers` | Clientes para cotizar. `scope=mine` (defecto: los de los vendedores del usuario) o `all`; `q` (NIT o nombre), `page`, `size` (≤ 100). Respuesta `{items, total, page, size, seller_linked}`; cada cliente `{nit, name, channel_code, channel, salesperson_code, salesperson_name, branch_count, active_branch_count, is_active}`. Sin vendedor vinculado, `mine` devuelve vacío |
| `POST /quotations` | `{customer_nit, window_months: 6 \| 12}` → 201 con la cotización completa (borrador). 404 cliente desconocido. Sin ventas en la ventana, sale vacía con el aviso `sin_ventas` |
| `GET /quotations` | `mine` (defecto `true`), `status`, `customer_nit`, `q` (NIT o nombre), `page`, `size` → `{items: [Resumen], total, page, size}`, más recientes primero |
| `GET /quotations/{id}` | Cotización completa |
| `PUT /quotations/{id}/lines` | `{version, lines: [{sku, new_price}]}` (`new_price: null` quita el precio). Recalcula y devuelve la cotización. 409 si `version` cambió; 422 precio negativo, con más de 4 decimales o referencia que no está. Una cotización `generado` vuelve a `borrador` |
| `POST /quotations/{id}/refresh` | `{version}`: la reconstruye desde el DWH y los maestros (nuevas ventas, costo, apoyos), conservando los precios nuevos por referencia. Vuelve a `borrador` |
| `POST /quotations/{id}/generate` | `{version, activation_date?, price_list_id?}`: valida la regla y deja la cotización `generado` con la fecha de activación (por defecto mañana) y la lista destino (por defecto la del cliente). 422 si no hay líneas con precio nuevo o alguna no cumple |
| `GET /quotations/{id}/export.xlsx` | El Excel (solo `generado`; si no, 409) |
| `GET /quotations/{id}/export.txt` | El plano SIESA (solo `generado`; si no, 409) |
| `POST /quotations/{id}/lines` | Pegar una lista: `{version, text: "26278\n12014\n…"}` (columna de Excel, o separadas por coma, punto y coma, tabulador o espacio; `2537.0` se lee `2537`) y/o `{skus: [...]}`; hasta 500 por vez. Las que **ya están** se revalidan (costo, objetivo, apoyo e impuesto de hoy) conservando su precio nuevo; las **nuevas** se agregan al final con `origin: agregado`; las que no existen o están inactivas se informan y se omiten. Devuelve la cotización con `import_result: {added, revalidated, not_found, inactive, repeated}`. 422 solo si ninguna sirve. 409 por versión. Vuelve a `borrador` |
| `DELETE /quotations/{id}/lines/{sku}?version=N` | Quita una referencia **agregada** y devuelve la cotización; 422 si es del histórico, 404 si no está |
| `DELETE /quotations/{id}` | 204. Quien la creó o un administrador |

**Resumen** (lista): `id, customer_nit, customer_name, channel, price_list_id, window_months,
status, status_label, line_count, changed_count, below_rule_count, created_by, created_at,
updated_at, generated_at, version`.

**Cotización completa**: el resumen más `channel_code, price_list_name, price_list_customers,
billing_center_code, salesperson_code, salesperson_name, built_at, cost_cut_date,
activation_date, export_price_list_id, max_points_below_target, warnings: [{code, label}],
generated_by, updated_by` y `lines`.

**Línea**:

| Campo | |
| --- | --- |
| `sku`, `description`, `unit`, `pack_factor` | referencia, descripción, unidad de inventario y empaque |
| `supplier_code`, `supplier_name` | proveedor comercial |
| `last_price`, `last_gross_price`, `last_discount_pct`, `last_sale_date`, `last_document`, `last_sale_unit`, `last_quantity`, `sales_in_window` | último precio (neto, antes de IVA, por unidad de inventario) y su venta |
| `list_price` | precio vigente de la lista del cliente, por unidad de inventario |
| `value_tax_key`, `value_tax_name`, `value_tax_amount` | impuesto al consumo de licores por unidad; `null` en los demás productos (`vat_pct` sigue en la respuesta, pero no se muestra) |
| `cost`, `cost_is_global` | costo actual por unidad de inventario |
| `target_margin_pct`, `margin_basis` (`lista`, `mayoristas`, `salsamentarias`, `apoyo`), `min_margin_pct` | objetivo y mínimo permitido |
| `support` | `null` o `{support_id, kind, calc_method, program, amount, share_of_cost, required_margin_pct, detail, conditions, needs_review, valid_from, valid_to}` |
| `support_candidates` | `[{support_id, calc_method, program, reason}]`: apoyos que cubrirían la línea y no se aplicaron |
| `current_margin_pct`, `current_margin_with_support_pct` | margen del último precio sin y con apoyo |
| `min_price` | precio mínimo que cumple la regla |
| `new_price`, `new_margin_pct`, `new_margin_with_support_pct`, `variation_pct`, `compliance` | el ajuste del asesor; `null` sin precio nuevo |
| `price_with_taxes` | precio nuevo (o último) más el impuesto de licores, sin IVA; `null` en una agregada sin precio nuevo |
| `problems`, `problem_labels` | `sin_costo`, `sin_margen_objetivo`, `producto_no_sincronizado`, `apoyo_condicionado`, `apoyo_en_revision`, `apoyo_fuera_bodega` |
| `origin`, `origin_label` | `historico` (de la ventana de ventas) o `agregado` (la sumó el asesor; `last_*` en `null`) |

Avisos de la cotización (`warnings`): `sin_canal`, `cliente_inactivo`, `listas_distintas`,
`co_distintos`, `lista_compartida`, `sin_lista`, `sin_ventas`.

## Configuración

| Variable | Defecto | Uso |
| --- | --- | --- |
| `QUOTATIONS_MANAGER_AREA_SLUG` | `comercial` | quién cotiza |
| `QUOTATIONS_MAX_POINTS_BELOW_TARGET` | `1` | puntos bajo el objetivo que se toleran |
| `QUOTATIONS_MAX_LINES` | `3000` | referencias máximas por cotización |
| `QUOTATIONS_SIESA_COMPANY` | `001` | compañía del plano |
| `QUOTATIONS_DWH_TIMEOUT_SECONDS` | `30` | segundos por consulta al DWH del cotizador |

El usuario del DWH debe leer en `consumo`: `ventas`, `clientes`, `productos`,
`producto_impuestos`, `inventario_corte`, `precios_lista_vigentes`, `listas_precios` y
`vendedores` (`svc_bi` sí; el rol `dir_comercial` no alcanza).

El histórico de un cliente con cientos de sucursales tarda de 1 a 6 segundos y, con el DWH
ocupado, hasta 27. Por eso el cotizador tiene su propio tiempo límite y no usa los 10 segundos
de `DWH_COMMAND_TIMEOUT_SECONDS`; `DataProviderConnection.fetch` acepta `timeout` por consulta.

## Rendimiento al agregar

Buscar productos para agregarlos usa el maestro local (`GET /commercial/products`), con índices
trigrama sobre SKU y descripción: 20 a 115 ms. Agregar o revalidar consulta el DWH (ítem,
costo, precio de lista e impuesto) y el maestro de apoyos **en paralelo**. La fecha del último
corte de inventario se recuerda 10 minutos: calcularla recorre toda la vista
`inventario_corte` (~900 mil filas), y con la fecha fija el costo se lee por el índice de la
referencia. Solo se insertan las líneas nuevas y se actualizan, en un solo lote, las
revalidadas; el resto de la cotización no se reescribe.

| Operación (cotización de 120 líneas, DWH real) | Tiempo |
| --- | --- |
| Pegar 10 referencias (5 nuevas, 3 revalidadas, 1 inexistente, 1 repetida) | 447 ms |
| Pegar las mismas 10 otra vez (8 revalidadas) | 153 ms |
| Guardar 5 precios | 73 ms |

Con el DWH en frío (primera consulta del día o tras un rato sin uso) la primera llamada puede
tardar más; la UI debe mostrar las filas pegadas de inmediato como «Agregando…».

## Validación (2026-11-03)

Contra el DWH real (rol `svc_bi`) y una copia de la base local migrada a `0037_quotations`:

| Cliente | Canal | Lista | Referencias | Resultado |
| --- | --- | --- | --- | --- |
| Restaurante, 238 sucursales | RESTAURANTES | `31A` (202 clientes) | 120 | 117 con costo (5 de todas las bodegas), 94 con precio de lista, margen Lista 1 |
| Mayorista | MAYORISTAS Y DISTRIBUIDORES | `1` GENERAL (28.113 clientes) | 41 | margen Mayoristas; precio en el mínimo cumple, 3 % por debajo bloquea la generación; Excel y plano descargados |
| Restaurante con licores | RESTAURANTES | `1` | 196 | `0011` 6.911 y `0024` 42.451, iguales a `precios_lista_vigentes` |
| Mayorista con apoyos | MAYORISTAS Y DISTRIBUIDORES | `62Y` | 104 | 28 apoyos tácticos `costo_neto` aplicados: margen con apoyo 13 % contra objetivo 13 % |

## Pendiente de definir con el negocio

1. Si el objetivo de un apoyo debe mandar sobre el margen del producto (hoy sí, como en el
   validador).
2. Qué hacer con referencias sin margen para el canal (hoy no se pueden generar con precio nuevo).
3. Apoyos con cantidad mínima: la cotización no tiene cantidades; hoy se aplican y se advierte.
4. Vínculo asesor ↔ vendedor: solo por correo (111 de 584 vendedores activos lo tienen). La
   cartera es una sugerencia, no un bloqueo.
5. Carga directa a SIESA por webservice.
