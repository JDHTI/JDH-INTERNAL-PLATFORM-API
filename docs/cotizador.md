# Cotizador

Cada asesor comercial revisa, por cliente, la lista de precios que ese cliente viene pagando
y la ajusta. Parte del **último precio de venta** de cada referencia que se le vendió en una
ventana de 6 o 12 meses, lo cruza con el **costo** (el mayor entre la cotización del proveedor
y el inventario), el **margen objetivo** del [maestro de márgenes](margenes-productos.md) y los
**apoyos vigentes** del [maestro de apoyos](apoyos-proveedores.md) (igual que el
[validador de pedidos](validador-pedidos.md)). El asesor confirma la lista, BI la valida y
entonces se generan el Excel `ACTUALIZACION LISTAS PRECIOS` y el plano de importación de SIESA
(registros `0112`/`0126`/`0127`) para actualizar la lista del cliente o crear una nueva; cuando BI
los carga en SIESA, la marca como cargada. También se cotiza a un **cliente temporal** (que aún no
está en SIESA).

Módulo `quotations`. Migraciones `0037_quotations`, `0038_quotation_review`,
`0050_quotation_local_lines` (categoría de la línea y productos solo para cotización),
`0051_quotation_offer` (oferta al cliente y cantidades) y
`0052_quotation_upload_flow` (estados de carga, CO elegido, cliente temporal, variación en la
oferta). Área: Comercial (`/comercial/cotizador`). Datos del DWH: [cotizador-dwh.md](cotizador-dwh.md).

`0051` también creó la tabla `quotation_requested_products` y la columna
`quotation_lines.requested_product_id` para un registro de productos solicitados que se retiró
antes de salir a producción. Quedan en la base sin uso: nada las lee ni las escribe, y las líneas
nuevas guardan `NULL` en la columna.

El mismo motor actualiza una lista vigente de SIESA en el módulo de BI
[Actualización de listas](actualizacion-listas.md) (`kind = 'lista'`): sin oferta y comparando
contra el precio de la lista.

**Reglas para BI, manual para Comercial**: las reglas de cálculo (este documento y
`GET /quotations/rules`) son de BI: las ven quienes validan el cotizador, las listas principales
y los administradores (`can_see_rules` en `/permissions`); para el resto `/rules` responde 403.
Comercial lee el **manual operativo** (`GET /quotations/manual`, `domain/manual.py`): crear,
qué significa cada columna, apoyos y validación, sin fórmulas, costos ni bodegas. El buscador de
clientes encuentra también por grupo empresarial y lo devuelve (`business_group`).

Las reglas de costo, bodega, redondeo e impuesto son las del área de listas de precios, escritas
en la skill del proyecto [`lista-precios`](../.claude/skills/lista-precios/SKILL.md). El cotizador
las centraliza en `app/modules/quotations/domain/pricing.py`; si la skill cambia, se cambia ahí.

## Cómo se arma una cotización

1. **Cliente**: un NIT, con todas sus sucursales. Del DWH (`consumo.clientes`) se toman, por
   mayoría entre las sucursales activas, el canal (`cod_canal`), la lista de precios SIESA
   (`lista_precio_id`) y el vendedor. Si las sucursales no coinciden, la cotización lo advierte
   (`listas_distintas`, `co_distintos`). Las referencias del histórico quedan atadas a la lista que
   el cliente tiene asignada.
   - **CO que factura** (`billing_center_code`, de `cod_co_factura`): si alguna sucursal activa
     factura desde un CO de la Costa (`004`), se toma el CO de la Costa más frecuente entre ellas
     (paga el impuesto de licores de la Costa); si no, el más frecuente. `co_distintos` sigue
     avisando que facturan desde varios.
   - **CO del costo** (`cost_centers`): de qué bodegas sale el costo de cada línea.
     - **Nacional** (`cost_centers` vacío): el **mayor costo de todas las bodegas**, referencia por
       referencia (las 17 bodegas de la lista 1, tabla del paso 4). Es el valor inicial de un cliente
       **nacional**: el que factura desde varios CO (`co_distintos`), si su lista no tiene bodegas
       propias. Nacional va sola: no se combina con otros CO.
     - **Un CO**: sus bodegas. Es el valor inicial de un cliente que factura desde un solo CO.
     - **Varios CO**: cada referencia toma el **más costoso** de ellos, y la línea dice cuál fue
       (`cost_center`). Un empate va al primero por código.
   - **CO elegido**: el asesor puede cambiar el CO del costo (al crear, un CO con
     `billing_center_code`; después, Nacional o uno o varios CO con `PATCH /billing-center`).
     `billing_center_source` dice `auto` o `manual`; un refresco conserva la elección. Cambiarla
     recalcula cada línea con la regional (impuesto de licores) y las bodegas, y vuelve a borrador.
     Una lista con bodegas fijas (tabla del paso 4, y la lista 1) las conserva aunque cambie el CO
     (`centers_set_cost: false`): el CO decide las bodegas de las listas fuera de la tabla y de un
     cliente temporal.
   - **Comparar antes de elegir**: con dos o más CO marcados, `GET
     /cost-centers/compare?codes=…` dice cuál cuesta más para las referencias de la cotización: el
     de mayor suma de costos unitarios sobre las referencias con costo en todos. Solo el código, sin
     montos; `null` si empatan o si la lista tiene bodegas propias.
   - **Empezar vacía** (`with_history: false` al crear): un cliente de SIESA sin el histórico de
     ventas. El asesor agrega las referencias; un refresco las revalida y nunca trae el histórico.
2. **Último precio** (`consumo.ventas`): por referencia se toma la **última venta de cada CO que
   facturó** (`ventas.co`) en la ventana y, entre ellas, la de **mayor precio neto**; su fecha,
   documento y unidad son los `last_*`. Dentro de un CO, un empate de fecha va al documento mayor,
   luego al precio mayor y a la llave de línea; entre CO, un empate de precio va a la más reciente.
   `sales_in_window` cuenta todas las ventas de la referencia. Solo ventas (no devoluciones) con
   cantidad y subtotal mayores que cero. El precio es `subtotal / cantidad`: neto de descuentos,
   **antes de IVA** y **por unidad de inventario** (una caja C45 vendida a 149.551 es 3.323,36 por
   unidad).
   - **Lectura**: primero las llaves del cliente (`consumo.clientes`) y luego sus ventas por esas
     llaves (`cliente_key = ANY(...)`, que usa el índice). Con el NIT en una subconsulta, el
     planificador recorría por fecha todas las ventas de la compañía del último año (2,3 millones
     de líneas): con el caché frío pasaba de los 30 s del límite y la cotización respondía «No se
     pudo consultar el datawarehouse» (medido el 2026-11-11 con Sodexo: 33,6 s en frío; por
     llaves, 1,4 s). Las lecturas del cotizador corren sin JIT (`jit = off`, unos 2 s menos cada
     una), y si una se pasa del tiempo el mensaje lo dice: «El datawarehouse tardó más de 30 s en
     responder».
3. **Costo**: el **mayor** entre:
   - la **cotización vigente del proveedor** (`consumo.cotizaciones_proveedores`: vigente, ítem
     activo, unidad `UNID` o `KL`): `redondear(costo_bruto) × (1 + ICUI/100) + IBUA`, la mayor por
     referencia. **Lactalis** (NIT 800245795) cotiza por sucursal: la `003` para las listas de
     Centro (003, 10U, 10S, o un cliente que factura desde el CO 003) y la `001` para el resto;
   - el **costo promedio de inventario** (`consumo.inventario_fecha`, la foto de SIESA de la
     mañana) de la bodega de la lista, redondeado al peso. La **bodega 2** (cuarto frío) aplica
     solo a las referencias de línea `CONGELADOS` y `REFRIGERADOS` (`consumo.productos.linea`);
     el resto usa la bodega 1. Oriente (BGA1) y Market (URA6) no tienen cuarto frío aparte: la
     bodega 2 es la misma bodega 1;
   - el **costo nuevo de un alza pendiente**: si la alza más reciente de la referencia en el
     histórico del maestro de productos no tiene fecha de actualización en SIESA, su costo nuevo
     (con impuesto saludable si lo trae). Solo gana si es estrictamente mayor
     ([alzas-precios.md](alzas-precios.md)).

   El mayor se redondea hacia arriba y se multiplica por el factor de la familia de la lista:
   **1,004** en Generales, Salsamentarias y Market; **1** en Mayoristas. La cotización indica de
   dónde salió (`cost_source`: `cotizacion`, `inventario` o `alza`) y trae los valores
   (`quote_cost`, `inventory_cost`, `increase_cost`).
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

   Una lista que no está en la tabla (31A, 62Y…) usa las bodegas del **CO del costo** (paso 1):
   001 → MDE1/MDE3, 002 → OCC1/OCC16, 003 → BTA9/BTA8, 004 → CTG1/CTG15, 005 → BGA1,
   009 → BAQ1/BAQ2. Nacional, o un CO fuera de esa lista (006, 007, 008, 010, 100, 200, 201), toma
   el mayor de las bodegas de la lista 1. Con varios CO se lee el costo de cada uno (a la vez) y
   cada referencia se queda con el mayor. La cabecera dice cuál se usó (`stock_source`: las bodegas
   de un CO, «Nacional: mayor costo entre todas las bodegas» o «Mayor costo entre los C.O. 001,
   003»).
5. **Impuesto**: solo el **impuesto al consumo de licores** (`consumo.producto_impuestos`, llave
   `0011` importados o `0024` nacionales). El valor es el que SIESA tiene en la lista del cliente
   (`precios_lista_vigentes.impoconsumo_valor`); si la lista no lo trae, el del maestro de
   márgenes según la **regional**: **Costa** si alguno de los CO del costo es el `004`; con
   Nacional, la del CO que factura; Nacional para el resto (`regional` en la cabecera). El IVA no
   se muestra ni se suma.
6. **Margen objetivo Listas de Unidad Negocio** (reglas v2, 2026-11-17): el margen óptimo del
   producto en la lista del cliente, igual en el validador y el maestro de apoyos
   (`customer_margin_field`), en este orden:
   1. **Market Apartadó**: factura desde el C.O. `200` (el 201 Market Valledupar no).
   2. **Masivo food**: su vendedor es de un equipo con clase `MASIVO FOOD`
      (`consumo.vendedores.clase_equipo`, copiado al maestro de clientes como
      `sales_team_class`).
   3. **Mayoristas**: canal `MAYORISTAS Y DISTRIBUIDORES`, `SUBDISTRIBUIDOR` o `SALSAMENTARIAS`.
      El margen Salsamentarias del maestro ya no se aplica.
   4. **Food service** (`margin_list`): cualquier otro canal, o sin cliente.

   Con varias sucursales decide la mayoría de las activas. Si el producto no tiene margen Masivo
   food o Market, usa el de Food service. Si un apoyo aplica y trae su propio objetivo
   (`target_margin_pct`, `min_margin_pct` o `list_margin_pct`), manda el del apoyo.
7. **Apoyo**: los apoyos activos (no anulados ni desactivados, sin el origen `Condiciones
   Especiales`) que cubren al cliente y la referencia hoy, con el mismo alcance que en el
   validador. Se aplica el de mayor importe entre los que traen dinero. Un apoyo en revisión o
   restringido a una bodega no se aplica: se lista como candidato con el motivo.
   - **Importe** (reglas v2): el dinero del proveedor por unidad de inventario con la función
     compartida del maestro de apoyos (`unit_amount`): % sobre el costo de la línea, % sobre el
     precio lista (su valor absoluto: costo / (1 − objetivo) × %), $ en costo o en precio lista,
     costo apoyado o margen garantizado. Un valor por caja se divide por la unidad de empaque del
     maestro de productos (vacía = 1).
   - **Precio óptimo con apoyo** = **precio apoyado sugerido** = precio lista − apoyo (antes era el
     objetivo sobre el costo neto).
   - **Precio mínimo**: si el apoyo trae el **margen mínimo autorizado JDH**
     (`supported_margin_pct`), el mínimo es (costo − apoyo) / (1 − ese margen)
     (`support.authorized_margin_pct`); si no, la regla general.
   - Rebates y presupuestos quedan aparte: nunca se aplican.
   - **Inventario**: cada línea trae `stock_on_hand` (existencia) y `stock_available`
     (`cant_disponible`) de las mismas bodegas del costo.
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
    `below_target_count`. Su precio sí basta para confirmar. **No sale en el Excel ni en el
    plano.** Avisa `producto_local`. Se le cambia o quita el precio con `PUT /lines` (con
    `margin_pct` se omite: no tiene costo), se conserva igual al refrescar (después de las líneas
    reconstruidas) y se puede quitar. Vive solo dentro de su cotización: no pasa a otra cotización
    ni se convierte en una referencia SIESA. Si el producto se crea después en SIESA, se agrega como
    cualquier referencia y se quita el `LOC-<n>`.
11. **Cliente temporal**: un cliente que aún no está en SIESA se cotiza con
    `temporary_customer: {name, legal_name, trade_name?, nit?}` (nombre y razón social
    obligatorios, hasta 120 caracteres) y el CO desde donde se cotiza (`billing_center_code`,
    obligatorio). La cotización arranca **vacía**: sin ventas, sin lista (`price_list_id: null`),
    sin canal y sin vendedor; las referencias que se agregan se cotizan con las bodegas y la
    regional del CO elegido. `customer_name` es el nombre y `customer_nit` el NIT (o `""`). Se
    guarda en la misma cotización. La cartera de ventas no aplica: lo puede crear quien cotiza.
    Solo va por **creación** de lista; un refresco recalcula sus líneas sin leer ventas.

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
  `include_seller` (sí por defecto), `quote_quantities` (no por defecto), `include_variation`
  (no por defecto) y `offer_scope`: `changed` (por defecto: las líneas activas con precio nuevo,
  también los productos solo para cotización) o `all` (toda línea activa con precio de oferta).
  Una línea inactiva nunca va.
- **Asesor** (`seller_contact`): el vendedor asignado al cliente (`salesperson_code`) en
  `consumo.vendedores` (`nombre`, `correo` en minúscula, `celular`). Se guarda al crear y al
  refrescar; `null` sin código o sin fila. Las guardadas antes lo traen al refrescar.
- **Precio de oferta** (`offer_price`): el precio nuevo y, si no hay, el último; un producto solo
  para cotización, su precio nuevo. `in_offer` dice si la línea va en la oferta.
- **Cantidades** (`quantity`): unidades de inventario, mayor que cero, hasta dos decimales y
  10.000.000. Se escriben con `PUT /lines`; un lote que solo trae cantidades se acepta en
  cualquier estado (también pendiente de validación), no cambia el estado pero sí sube la versión.
  Se conservan al refrescar, al revalidar y al cambiar de CO.
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
  cotización—, `Descripción`, `Unidad`, `Precio sin IVA`, `IVA %`, `Imp. consumo` —el impuesto
  al consumo de licores por unidad, `value_tax_amount`; vacío si no tiene—, `Precio con IVA` =
  precio × (1 + IVA) + impuesto al consumo, redondeado al peso; con `include_variation`,
  `Variación` = `variation_pct`) y, con cantidades, `Cantidad`, `Subtotal`, `IVA` y `Total` más
  los totales por grupo de IVA, subtotal, IVA, impuesto al consumo y total; al final «Condiciones
  de la oferta»: primero «Precios válidos hasta el <fecha larga>» y luego las del asesor.

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

Guardar precios nunca se bloquea: la línea queda marcada. **Confirmar tampoco**: una referencia
en `bajo_minimo`, `sin_objetivo` o `sin_costo` se confirma y llega a quien valida contada en
`below_rule_count`. Confirmar solo exige al menos una línea activa con precio nuevo, de SIESA o
solo para cotización (un cliente temporal puede tener solo productos locales).

## Estados

**Flujo sin bloqueos hasta el cliente** (BI, 2026-11-18): comercial y BI arman la cotización,
ven y descargan la oferta en borrador y la envían al cliente (`POST /quotations/{id}/send`) sin
pasar por validación; la regla de margen nunca bloquea. Solo **actualizar o crear una lista de
precios** (`/confirm`) pasa por la validación de BI. `/send` necesita al menos una referencia en
la oferta; 409 si la cotización ya está en validación o cargada.

| Estado | Etiqueta | Quién | Qué pasa |
| --- | --- | --- | --- |
| `borrador` | Borrador | asesor | edita precios, márgenes, referencias; cualquier cambio vuelve aquí |
| `enviada` | Enviada al cliente | asesor o BI (`/send`) | la oferta salió al cliente **sin validación**; sigue editable (una edición la vuelve a borrador y guarda `sent_at`/`sent_by` del último envío) |
| `pendiente_validacion` | Pendiente de validación | asesor pide la lista (`/confirm`, desde borrador o enviada) | queda bloqueada para editar precios; elige actualización o creación y la fecha de activación |
| `pendiente_carga` | Pendiente de carga | BI valida (`/validate`) | da el código de una lista nueva; se liberan el Excel y el plano |
| `cargada` | Cargada | BI marca (`/uploaded`) | los archivos se cargaron en SIESA; guarda `uploaded_by` y `uploaded_at` |

`list_action` / `list_action_label` siguen diciendo si es actualización o creación. Editar una
cotización `pendiente_carga` o `cargada` la vuelve a borrador (y borra las marcas de validación y
carga). La migración `0052` pasó las `creada` y `actualizada` a `pendiente_carga`.

Quien valida también puede **devolverla** a borrador con una nota (`/return`, `review_note`).
El asesor puede retirarla de la validación por el mismo camino. Validan los miembros del área
`QUOTATIONS_APPROVER_AREA_SLUG` (por defecto `bi`, que lleva las listas de precios) y los
administradores; ellos leen todas las cotizaciones aunque no sean del área comercial.

## Actualización o creación de lista

- **Actualización** (`actualizacion`): los precios se escriben en la **lista que el cliente tiene
  asignada** en SIESA; no se puede escoger otra. Si la lista es compartida, el cambio llega a todos
  sus clientes (`price_list_customers` y el aviso `lista_compartida`).
- **Creación** (`creacion`): se crea una lista nueva. **El código lo da quien valida**, al validar
  (1 a 3 letras o números, libre en SIESA: se consulta `consumo.listas_precios`); el asesor solo
  puede proponer la descripción al confirmar, y quien valida puede cambiarla (por defecto
  `COTIZADOR <cliente>`, en ASCII y mayúsculas, máximo 40). `export_price_list_id` se fija al
  validar.
- **Lista 1**: un cliente en la lista `1` (GENERAL, unos 28.000 clientes) **siempre va por
  creación**: la lista 1 no se actualiza por este medio (aviso `lista_general`). Igual un
  **cliente temporal**, que no tiene lista en SIESA.
- Asignar la lista nueva al cliente se hace **a mano en SIESA**: el registro `0201` de clientes
  reescribe la sucursal completa (vendedor, cupo, dirección…) y no se automatiza.

La plantilla para cambios masivos (`template.xlsx`, que se subía de vuelta) se retiró: los precios
se gestionan en la plataforma.

**Plantilla de BI** (`lines.xlsx`): solo de consulta, para que quien valida cruce los costos con
SIESA. Trae cada línea con los datos de la tabla y sus costos: el costo más alto, de dónde salió,
el CO del costo, la cotización del proveedor, el costo de inventario y la cotización descartada.
Hoja `Lineas` (una fila por línea) y hoja `Cotizacion` (cliente, lista, CO del costo, bodega,
regional, corte). Solo quien valida la descarga (403 para el resto): lleva costos, que los asesores
no ven. Se escribe con openpyxl en modo de solo escritura (las filas van directo al archivo, sin
fórmulas, validaciones ni formatos condicionales) y en un hilo aparte, para que una cotización
grande no frene la API ni pese al abrirla. La oferta y el Excel de SIESA también se escriben en un
hilo aparte.

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
(refrescar, precios, agregar referencias, CO, confirmar) y borran cualquier cotización.
Comercial, solo las que creó. Validar y marcar como cargada, solo quien valida.

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
| `POST /quotations` | `{customer_nit, window_months: 6 \| 12, validity_days?: 1..365 (30), billing_center_code?, with_history?: true}` (`with_history: false` empieza vacía) o, para un cliente temporal, `{temporary_customer: {name, legal_name, trade_name?, nit?}, billing_center_code, window_months?, validity_days?}` → 201 con la cotización (borrador). 422 si llegan los dos, ninguno, falta el CO del cliente temporal o el CO no existe |
| `GET /quotations/billing-centers` | `[{code, name, regional, regional_label}]`: los CO activos del maestro `centros-operacion` (`consumo.centros_operacion`), por código; `regional` es la del impuesto de licores (`costa` para `004`) |
| `GET /quotations` | `mine` (defecto `true`; quien valida usa `mine=false`), `status` (`borrador`, `enviada`, `pendiente_validacion`, `pendiente_carga`, `cargada`), `customer_nit`, `q`, `page`, `size` |
| `POST /quotations/{id}/send` | `{version}` → `enviada`: la oferta va al cliente sin validación (asesor o BI); sigue editable. 422 sin referencias en la oferta; 409 si está en validación o cargada |
| `GET /quotations/{id}` | Cotización completa |
| `PUT /quotations/{id}/lines` | `{version, lines: [{sku, new_price?, margin_pct?, quantity?}]}`. `quantity` (texto o número; `null` la quita) va sola o con `new_price`, nunca con `margin_pct`. Un lote con **solo** cantidades se acepta en cualquier estado, no cambia el estado y sube la versión; uno que toca precios sigue las reglas de siempre. `new_price: null` quita el precio; `margin_pct` (fracción) le pone a esa referencia el precio de ese margen: así se lleva una **selección** a un margen estándar. Las que no se pueden calcular (sin costo, o un producto solo para cotización) vuelven en `skipped: [{sku, reason}]`; 422 si ninguna. 409 por versión o si está pendiente de validación |
| `POST /quotations/{id}/lines` | Pegar una lista o enviar una selección múltiple: `{version, text}` y/o `{skus: [...]}`; hasta 500. Devuelve `import_result: {added, revalidated, not_found, inactive, repeated}`. Una referencia `LOC-<n>` no se busca en SIESA: va en `not_found` |
| `POST /quotations/{id}/local-lines` | Producto solo para cotización: `{version, description, supplier_name?, price, vat_pct}`. `description` de 1 a 120 caracteres (se recortan los espacios), `supplier_name` opcional hasta 120, `price` (texto o número) en pesos enteros mayor que cero antes de IVA, `vat_pct` 19, 5 o 0. Devuelve la cotización con la línea `LOC-<n>` al final. Mismos permisos y estados que agregar referencias: 409 por versión o si está pendiente de validación; 422 con `detail` en español si un campo no sirve |
| `DELETE /quotations/{id}/lines/{sku}?version=N` | Quita una referencia **agregada**, **inactiva** o **solo para cotización** |
| `PATCH /quotations/{id}/offer` | `{version, validity_days?, conditions?, include_seller?, quote_quantities?, offer_scope?: changed \| all, include_variation?}`: solo cambian las llaves enviadas (`null` deja el valor). En cualquier estado y sin cambiarlo; sube la versión; 409 por versión. Una vigencia nueva empieza hoy |
| `GET /quotations/{id}/offer.xlsx` | La oferta para el cliente, en cualquier estado, para quien puede leer la cotización |
| `PATCH /quotations/{id}/billing-center` | `{version, national: true}` (Nacional) o `{version, cost_centers: [...]}` (uno o varios CO; `billing_center_code` sigue sirviendo para uno): fija el CO del costo (`manual`) y recalcula regional, bodegas, costo, impuesto de licores, objetivo, mínimo, óptimo, márgenes y cumplimiento de cada línea; conserva precios nuevos, cantidades y productos locales. Mismas reglas que `/refresh` (vuelve a borrador; 409 por versión o pendiente de validación). 422 si un CO no existe o está inactivo, o si llega Nacional con CO o ninguno de los dos |
| `POST /quotations/{id}/refresh` | `{version}`: reconstruye desde el DWH conservando los precios nuevos y el CO del costo elegido; los productos solo para cotización quedan iguales, al final. Un cliente temporal no tiene ventas: se recalculan sus líneas |
| `POST /quotations/{id}/confirm` | `{version, list_action?, new_list_name?, activation_date?}` → `pendiente_validacion`. La regla no bloquea; 422 si no hay ninguna línea activa con precio nuevo, si la lista 1 o un cliente temporal se quiere actualizar, o la fecha es anterior a hoy. Ya no recibe `price_list_id` |
| `POST /quotations/{id}/return` | `{version, note}` → `borrador`. Quien valida debe escribir la nota |
| `POST /quotations/{id}/validate` | `{version, price_list_id?, new_list_name?}` → `pendiente_carga`. Solo quien valida. Para `creacion`, `price_list_id` es obligatorio (1 a 3 letras o números, que no exista en SIESA); fija `export_price_list_id` |
| `POST /quotations/{id}/uploaded` | `{version}`: de `pendiente_carga` a `cargada`, con `uploaded_by` y `uploaded_at`. Solo quien valida (403); 409 desde otro estado |
| `GET /quotations/{id}/export.xlsx` | El Excel (solo `pendiente_carga`/`cargada`; si no, 409) |
| `GET /quotations/{id}/export.txt` | El plano SIESA (solo `pendiente_carga`/`cargada`; si no, 409) |
| `GET /quotations/{id}/lines.xlsx` | La plantilla de BI: cada línea con sus costos (también el alza pendiente), en cualquier estado. Solo quien valida (403 para el resto) |
| `GET /quotations/{id}/cost-centers/compare?codes=001&codes=003` | `{highest}`: el CO que más cuesta para las referencias de la cotización (mayor suma de costos unitarios), o `null` si empatan o la lista tiene bodegas propias. Para quien lee la cotización: sin montos. 422 con menos de dos CO o uno inexistente |
| `DELETE /quotations/{id}` | 204. Quien la creó o un administrador |

**Resumen** (lista): `id, customer_nit, customer_name, channel, price_list_id, window_months,
status, status_label, list_action, list_action_label, export_price_list_id, line_count,
changed_count, below_rule_count, below_target_count, created_by, created_at, updated_at,
confirmed_at, validated_at, uploaded_at, uploaded_by, version, validity_days, valid_from,
valid_until, is_temporary_customer, temporary_customer: {name, legal_name, trade_name, nit} | null`
(fechas `AAAA-MM-DD`). `price_list_id` es `null` en un cliente temporal.

**Cotización completa**: el resumen más `channel_code, price_list_name, price_list_customers,
billing_center_code, billing_center_name, cost_centers (vacío = Nacional), centers_set_cost,
with_history,
billing_center_source: auto | manual, regional,
regional_label, price_family, price_family_label, stock_source, salesperson_code,
salesperson_name, built_at, cost_cut_date, activation_date, new_list_name,
max_points_below_target, warnings: [{code, label}], confirmed_by, validated_by, review_note,
updated_by, conditions, include_seller, quote_quantities, offer_scope, include_variation,
seller_contact: {code, name, email, phone} | null, offer_summary: {item_count, subtotal,
vat_groups: [{vat_pct, base, vat}], liquor_tax, total} | null` y `lines`.

**Línea**:

| Campo | |
| --- | --- |
| `sku`, `description`, `unit`, `pack_factor` | referencia, descripción, unidad de inventario y empaque |
| `category` | categoría del producto en SIESA (`consumo.productos.categoria`); `null` si no se conoce y en un producto solo para cotización. Si una línea de SIESA no la tiene (guardada antes de `0050`), se busca al leer la cotización, en una sola consulta a `consumo.productos`, y se guarda en la línea sin subir la versión ni `updated_at`; una referencia sin fila o sin categoría queda marcada como buscada (sale `null`) y no se vuelve a buscar. Si el DWH no responde, la lectura sale igual con `null` |
| `vat_pct` | IVA de venta en puntos (`19` = 19 %); en un producto solo para cotización, el que escribió el asesor |
| `is_active` | `false` si está inactiva en SIESA: se muestra para reemplazarla y no se exporta |
| `supplier_code`, `supplier_name` | proveedor comercial |
| `last_price`, `last_gross_price`, `last_discount_pct`, `last_sale_date`, `last_document`, `last_sale_unit`, `last_quantity`, `sales_in_window` | último precio y su venta |
| `list_price` | precio vigente de la lista del cliente, por unidad de inventario |
| `value_tax_key`, `value_tax_name`, `value_tax_amount` | impuesto de licores por unidad, según la lista o la regional |
| `cost`, `cost_source`, `cost_source_label` | costo de la regla y de dónde salió |
| `quote_cost`, `inventory_cost`, `discarded_quote_cost` | la cotización usada, el inventario de la bodega y la cotización descartada por fuera de rango |
| `cost_center` | el CO cuyas bodegas dieron el costo; `null` con Nacional o con una lista de bodegas fijas |
| `increase_cost` | costo nuevo de un alza que SIESA aún no tiene, el tercer valor que se compara |
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
| `QUOTATIONS_SIESA_COMPANY` | `001` | compañía del plano |
| `QUOTATIONS_DWH_TIMEOUT_SECONDS` | `30` | segundos por consulta al DWH del cotizador |

El usuario del DWH debe leer en `consumo`: `ventas`, `clientes`, `productos`,
`producto_impuestos`, `inventario_fecha`, `cotizaciones_proveedores`, `precios_lista_vigentes`,
`listas_precios`, `vendedores` y `centros_operacion` (`svc_bi` sí; el rol `dir_comercial` no
alcanza).

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

## Pendiente de definir con el negocio

1. Si el objetivo de un apoyo debe mandar sobre el margen del producto (hoy sí, como en el
   validador).
2. Qué hacer con referencias sin margen para el canal (hoy se confirman marcadas y decide quien
   valida).
3. Apoyos con cantidad mínima: la cotización no tiene cantidades; hoy se aplican y se advierte.
4. Vínculo asesor ↔ vendedor: solo por correo. La cartera es una sugerencia, no un bloqueo.
5. **CO sin bodega definida** (006 Yumbo, 007 Urabá, 008 Valledupar, 010 Quindío, 100, 200, 201):
   hoy toman el mayor de las bodegas de la lista 1. ¿Valledupar (008, 201) paga el impuesto de
   licores de la Costa? Hoy solo el CO 004.
6. **Código de las listas nuevas**: hoy lo escribe quien valida. El agente del DWH propone
   reservar `C01`–`C99` (sin uso hoy) para las que crea el cotizador.
7. **Estado Bloqueado**: SIESA tiene Activo/Inactivo/Bloqueado, pero `consumo.productos.estado`
   reduce el bloqueado a `I` (hoy 11 ítems). Si se quiere distinguir, pedir `estado_siesa` en el DWH.
8. **Alzas de precio** (Acumulado de alzas en Drive, el «Costo Final» de la skill): no está en el
   DWH, así que el costo del cotizador no las incluye.
9. Confirmar con el área que **BI** es quien valida y que el factor **1,004** aplica a todas las
   listas que no son mayoristas.
10. Carga directa a SIESA por webservice (la conexión del DWH es de solo lectura) y asignación
    automática de la lista nueva al cliente (`0201`).
