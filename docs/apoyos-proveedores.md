# Maestro de apoyos de proveedores

Reemplaza el libro **«1. MAESTRO APOYOS PROVEEDORES.xlsx»** (22 hojas, 41 tablas,
un formato distinto por proveedor) por un maestro único, enlazado con los
maestros reales del datawarehouse (proveedores, clientes y productos), con
filtrado dinámico y CRUD. Esta primera entrega es solo backend, para validar
resultados.

Módulos: `commercial_masters` (espejo de los maestros del DWH) y
`supplier_supports` (los apoyos). Migraciones `0017` a `0019`; reglas v2 en `0066` a `0069`.

## Reglas v2 (2026-11-17)

Definidas por BI. Lo que sigue manda sobre las secciones anteriores de este documento cuando
se contradicen.

### Los dos frentes

| | BI (gestores de `apoyos`) | Comercial |
| --- | --- | --- |
| Ve | Todo, incluido el costo (referencia, DWH, neto) | Precio lista, precio apoyado sugerido y mínimo, márgenes; **nunca el costo** |
| Crea y carga | Manual, por grupo empresarial, carga masiva, subida por grupo | — |
| Mantiene | Anulación y vencimiento masivo con observación; descarga con datos para validar con el proveedor; alertas de programados | — |
| Pide | Decide las solicitudes | Margen mínimo JDH sobre un apoyo (Solicitudes) y apoyos internos desde la cotización |
| Cotizador | Reglas de cálculo (`/quotations/rules`) | Manual operativo (`/quotations/manual`) |

### Clases y tipos

Solo se crean tres clases (`active_kinds`), cada una con sus tipos (`methods_by_kind`):

| Clase | Tipos |
| --- | --- |
| Apoyo táctico (`tactico_cliente`) | % Apoyo en Costo, % Apoyo en Precio Lista, $ Apoyo en Costo, $ Apoyo en Precio Lista (`valor_precio_lista`, nuevo), $ Costo Apoyado, % Margen Garantizado |
| Actividad descuento temporal (`actividad_temporal`) | los cuatro primeros |
| Excepción margen JDH (`excepcion_margen`) | Margen mínimo autorizado |

Rebate y presupuesto quedan aparte: no se crean, no se aplican y van al DWH con
`habilitado = false`. Lista especial, recuperación y liquidación se **borran**
(`scripts/purge-discontinued-supports.py`, con respaldo CSV y evento de borrado al DWH). Un
apoyo viejo de otra clase se puede seguir editando en lo demás.

### Cálculo (por unidad de inventario)

Un valor por caja se divide por la unidad de empaque del **maestro de productos** (vacía = 1).
Costo = DWH (regla del cotizador). Precio lista (PL) = costo / (1 − margen óptimo de la lista
del cliente: Market Apartadó, Masivo food, Mayoristas o Food service; ver
[cotizador.md](cotizador.md), paso 6; sin cliente ni canal, Food service).

| Tipo | $ Apoyo | Precio apoyado sugerido (PA) |
| --- | --- | --- |
| % Apoyo en Costo | costo × % | PL − $ Apoyo |
| % Apoyo en Precio Lista | PL × % (su valor absoluto se resta al costo) | PL − $ Apoyo |
| $ Apoyo en Costo / en Precio Lista | $ | PL − $ Apoyo |
| $ Costo Apoyado | costo − $ costo apoyado | PL − $ Apoyo |
| % Margen Garantizado (con margen mínimo del proveedor `s`, 2026-11-23) | costo − PA<sub>s</sub> × (1 − g), con PA<sub>s</sub> = costo / (1 − s): fijo | PL − $ Apoyo; precio mínimo PA<sub>s</sub> |
| % Margen Garantizado sin `s` (guardados antes) | máx(0, costo − precio de venta × (1 − g)); sin precio de venta, al PL | PL |

- **% Margen Garantizado con margen mínimo del proveedor** (2026-11-23). Es la regla vigente:
  - El apoyo lleva dos márgenes:
    - el **margen garantizado** `g` (`target_margin_pct`, por ejemplo 18 %);
    - el **margen mínimo garantizado proveedor** `s` (`supplier_min_margin_pct`, por ejemplo
      13 %). Los dos son obligatorios al crear.
  - Con `s` se calcula el **precio apoyado**: PA = costo / (1 − s). El proveedor pone
    $ Apoyo = costo − PA × (1 − g) = costo × (g − s) / (1 − s), un monto fijo que no cambia con
    el precio de venta.
  - A PA, el margen con apoyo es exactamente `g`. Ejemplo: costo 1.000, s = 13 %, g = 18 % →
    PA 1.149,43, $ Apoyo 57,47.
  - **PA es el precio mínimo de la línea** en el cotizador. Vender por debajo deja el margen con
    apoyo por debajo de `g`, porque el proveedor no pone más. Para bajar de `s` en una
    cotización puntual, el vendedor pide desde la línea una **solicitud de margen proveedor**,
    que se decide en el módulo de solicitudes. Al aprobarse crea una copia del apoyo para ese
    cliente con el mínimo aprobado; el apoyo base no cambia (ver
    [solicitudes-apoyos.md](solicitudes-apoyos.md#solicitud-de-margen-proveedor-margen-garantizado)).
  - `s` puede ser 0 % o negativo: es la excepción que asume el proveedor. Es distinto de la
    **Excepción margen mínimo autorizado JDH** (`supported_margin_pct`), que es rentabilidad que
    pierde JDH. Debe ser menor que `g`.
  - El precio apoyado sugerido es PL − $ Apoyo, como en los demás tipos, y nunca baja de PA.
  - En el validador la parte del costo es fija, (g − s) / (1 − s), y la línea debe llegar a `g`.
  - Un apoyo guardado antes sin `s` sigue con la regla anterior, descrita abajo, hasta que se le
    escriba `s`.
- **% Margen Garantizado sin margen mínimo del proveedor** (BI, 2026-11-18; regla anterior): el proveedor garantiza el margen `g` a **cualquier**
  precio de venta y pone lo que falte; no fija precio ni valor (`supplier_value` no se usa, solo
  `target_margin_pct`). El margen con apoyo queda en máx(margen sin apoyo, g). Ejemplo del
  usuario: vendido a 10.000 con margen 10 % (costo 9.000) y g = 14 % → el proveedor pone 400.
  - En el maestro y el `/preview` (no hay precio de venta) se muestra al precio de lista:
    PA = PL, $ Apoyo = máx(0, costo − PL × (1 − g)), margen recompuesto = máx(margen óptimo, g).
    No tiene Precio Apoyado Mínimo.
  - **El margen garantizado es el piso de la línea** (BI, 2026-11-18, «pasa derecho»):
    - se parametriza en el apoyo (`target_margin_pct`, editable en la edición del apoyo);
    - en el cotizador y en el validador la línea debe llegar a g, y con el apoyo siempre llega,
      porque su margen es máx(m, g). Así pasa a cualquier precio;
    - si hay un mínimo autorizado JDH (`supported_margin_pct`), manda ese;
    - cuando no paga nada (la línea ya está en g o por encima), se cumple igual: no es un apoyo
      sin efecto.
  - En el cotizador, el apoyo se calcula al precio de la línea (el nuevo, o el último si no hay
    nuevo): `guaranteed_margin_pct` en el apoyo aplicado; `amount` es lo que pone a ese precio y
    `list_amount` al precio de lista. El precio óptimo es el de lista del producto y no hay precio
    mínimo, porque el proveedor cubre cualquier precio hasta g.
  - En el validador: ver [validador-pedidos.md](validador-pedidos.md#apoyos-reglas-v2).
  - En producción no había ninguno guardado con un PA (18-nov-2026): no hubo migración.

- Margen recompuesto = (precio − costo + $ Apoyo) / precio, donde el precio es el **Precio
  Apoyado Mínimo** si hay margen mínimo JDH y si no el **precio apoyado sugerido**; nunca el
  precio lista (`margin_recomposed`; `recomposed_list_margin` trae el mismo valor por
  compatibilidad). Ejemplo de BI: costo 4.920, PL 6.000, 10 % en precio lista → apoyo 600,
  PA 5.400, margen 20 %. Ejemplo 22506: costo apoyado 118.400, PA 147.757 → 19,9 % (al precio
  lista daría 27,4 %); con margen JDH del 10 %, al mínimo 131.556 → 10 %.
- **Excepción margen mínimo autorizado JDH** (`supported_margin_pct`): Precio Apoyado Mínimo =
  (costo − $ Apoyo) / (1 − margen); para $ Costo Apoyado, costo apoyado / (1 − margen).
  Reemplaza el % JDH (`jdh_pct`, `jdh_net_cost` quedan como historia y ya no mueven las cifras).
- Las mismas fórmulas usan el cotizador y el validador de pedidos (`pricing.unit_amount`).

### Integridad

- **SKU de proveedor**: si solo viene el SKU del proveedor, se busca el SKU JDH
  (`supplier_product_codes`, luego `products.supplier_sku` del proveedor); desconocido o
  ambiguo = error. Ya no cubre todo el proveedor.
- **Sin cruces**: dos apoyos vivos de la misma clase, proveedor, cliente, sucursal, grupo,
  referencia, bodega y listas no pueden tener vigencias que se toquen. Crear, editar o cargar
  uno que cruza responde 409 con `overlaps` (o error de fila), salvo `on_overlap`:
  `vencer_anterior` (termina el día antes; si empezaba después, se anula) o
  `anular_anterior`, en la misma transacción.
- **Programados**: el job `supplier-supports-rollover` corta el apoyo anterior el día antes de que
  empiece el siguiente. Desde el 2026-11-20 corre todos los días a las **00:05** (Bogotá), sin
  esperar la carga del DWH; si el API arranca después, corre al arrancar. `GET
  /supplier-supports/alerts` lista cada programado con el que reemplaza, para dejarlo programado o
  vencer ya el anterior.
- **Estados**: vigente, programado y vencido no se guardan, se calculan al leer con la fecha de
  Bogotá. A las 12 a. m. cambian solos, sin job. Lo único que escribe datos al cambiar el día es
  el job de programados.
- **Grupo empresarial**: `POST /by-group` y la subida `POST /bulk/groups` crean un apoyo por
  cliente activo del grupo (por nombre; el código no se conoce), marcados `group_wide`. Desde el
  2026-11-20 esas copias **valen para todo el grupo**, también para un cliente que entró después o
  estaba inactivo al cargar:
  - el cotizador y el validador las toman como un solo apoyo del grupo, sin NIT, y lo aplican a
    todo cliente cuyo grupo en el maestro sea ese (mayúsculas y espacios no cuentan); si el
    cliente tiene su propia copia, se usa esa;
  - el vendedor ve los apoyos de los grupos de sus clientes, y la búsqueda base por Cliente
    también los trae;
  - un apoyo de un solo NIT nunca vale para el grupo, aunque guarde el grupo de su cliente;
  - una fila con grupo y sin NIT ya no se ve como «general» para todos los vendedores.

  Las copias cargadas antes las marcó la migración `0076` (mismas condiciones, misma
  transacción, dos o más clientes del mismo grupo). Volver a subir el grupo por `bulk/groups`
  también las marca. Uno de sucursal vale solo para el cliente que la tiene. Cada apoyo muestra
  `business_group`, `group_wide` y la lista de margen que usa (`margin_list`).
- **Bodegas promocionales** (grupo 23 de SIESA, 18 bodegas): un apoyo aplica allí solo con
  `promo_warehouses`; si no, el pedido de esa bodega va a su costo, sin apoyo.

### Operación

- `POST /supplier-supports/retire`: anula o vence hasta 2.000 apoyos (elegidos o por filtro) con
  observación obligatoria; todo o nada.
- `POST /supplier-supports/delete` (2026-11-23): **elimina** hasta 2.000 apoyos para siempre.
  - Selección: los elegidos, con `version`, o los de un filtro (anulados incluidos). Pide un
    motivo y es todo o nada.
  - Se borran con sus solicitudes, su historial JDH, costos, escalas y vínculos a documentos. Los
    documentos quedan en el gestor.
  - El DWH no tiene borrado: cada apoyo que no estaba anulado se publica antes como anulado
    («Eliminado: motivo»).
  - **Solo el líder del área de apoyos (BI) o su responsable, y los administradores de la
    plataforma**; los demás reciben 403. `GET /supplier-supports/permissions` lo dice en
    `can_delete`.
  - Los triggers de solo inserción de solicitudes e historial solo dejan borrar dentro de esa
    transacción (`app.allow_support_delete`, migración `0079`).
- `POST /supplier-supports/preview`: las cifras del formulario antes de guardar.
- Un apoyo nuevo o editado a mano trae su costo DWH en segundo plano, sin esperar el job.
- **La carga masiva asigna el costo ella misma** (BI, 2026-11-18). Al aplicar, lee del DWH el
  costo de cada apoyo del archivo (creado, actualizado o sin cambio, con SKU JDH), con la regla
  del cotizador. Lo guarda en la misma transacción que los apoyos.
  - Si el DWH no responde, la carga no se guarda (503) y se puede reintentar.
  - El reporte trae `costs_assigned` y `costs_missing` (apoyos sin costo en el DWH).
  - Volver a subir el mismo archivo recalcula el costo de sus apoyos.
  - El job `supplier-supports-costs` sigue siendo solo la actualización diaria de los costos;
    la carga no depende de él.
- Plantilla (`GET /bulk/template`), solo estas columnas y en este orden (BI, 2026-11-18): Clase
  apoyo, Tipo apoyo, Valor apoyo, Unidad valor, Excepción margen mínimo autorizado JDH, Fecha
  Inicio, Fecha Fin, NIT Cliente, Cliente, Sucursal, Grupo Empresarial, SKU JDH, SKU Proveedor,
  Regional, Unidad Negocio, Canal, Bodega. Clase, Tipo y Unidad con lista desplegable; las
  columnas de fecha ya tienen formato `dd/mm/aaaa` y validación de fecha (también en la descarga
  con datos). `?groups=true` quita las columnas del cliente.
- Llave de la carga: proveedor + NIT + SKU JDH + fecha inicio. El SKU Proveedor se toma del
  maestro de productos (solo sin SKU JDH sirve para encontrarlo).
- **Colores de encabezado** (todas las plantillas y la descarga con datos), calculados con las
  mismas reglas que valida la carga (`fields.REQUIRED_ALWAYS` y `METHOD_RULES`,
  `bulk_file.column_requirements`):
  - **rojo**: obligatoria en toda fila. Son Clase apoyo y Tipo apoyo, más Grupo Empresarial en
    la subida por grupo;
  - **naranja**: obligatoria según el tipo, con una nota en el encabezado y la columna
    Obligatorio de la hoja Instrucciones;
    - Valor apoyo: todos los tipos salvo Margen mínimo autorizado;
    - Excepción margen mínimo autorizado JDH: Margen mínimo autorizado y % Margen Garantizado;
  - **azul**: opcional. La fecha inicio es parte de la llave, pero vacía vale (sin inicio);
  - si el perfil fija el tipo, sus campos pasan a rojo.
- **Bodegas promocionales (grupo 23)** en la carga (`promo_warehouses`):
  - `/bulk` y `/bulk/groups` lo aceptan en `defaults` (`"true"`/`"false"`, también Sí/No) y lo
    aplican a las filas sin valor. El perfil del proveedor lo acepta en sus `defaults` para
    dejarlo fijo;
  - columna opcional «Bodega promocional» (Sí/No, lista desplegable, azul) en la plantilla
    completa y en la descarga con datos, después de Bodega. No va en la plantilla corta, que
    quedó con solo los campos de BI. Vacía = el valor de la carga (No si no se eligió);
  - el alta manual y el PATCH siguen con `promo_warehouses` como antes. `form-schema` no lo
    lista: el formulario tiene su propio control.
- Con la columna Clase apoyo, una fila sin clase es error. Solo un archivo sin esa columna (el
  formato propio de un proveedor) deduce la clase.
- En la plantilla corta, la columna de margen es el margen garantizado de un % Margen
  Garantizado (`target_margin_pct`) y el margen de la Excepción margen JDH.
- **Derivados de los maestros** (carga simulada y aplicada, alta manual y descarga con datos):
  - desde el NIT: nombre del cliente y grupo empresarial (los del maestro, no los escritos).
    La sucursal debe ser una del NIT: `1`, `1.0` y `001` son la misma, y se guarda como `001`.
    El NIT puede venir con dígito de verificación (`800116299-6` o `8001162996`): si no existe
    y su último dígito es el DV DIAN del resto, se lee el resto;
  - desde el SKU JDH: SKU Proveedor, marca y factor de empaque. `01128`, `1128` y `1128.0` son
    el mismo SKU;
  - desde el SKU Proveedor, solo sin SKU JDH: el SKU JDH. Es error si no hay o hay varios. Si
    no hay coincidencia exacta, se ignoran los ceros a la izquierda (Excel convierte `0133` en
    `133`);
  - la lista de margen sale del NIT al calcular (`customer_margin_field`), no se guarda.
- **Descarga con datos** (`GET /bulk/export?supplier_code=`): los apoyos vigentes y programados
  del proveedor con la columna **Validación**; al subirla, Sí actualiza, No vence ayer con la
  observación de la fila, vacía no toca.
- Panel de solicitudes JDH: pide y aprueba el margen mínimo autorizado (`mode = margen_minimo`).

## Maestros comerciales (`commercial_masters`)

Se copian desde las vistas `consumo.*` del DWH (PostgreSQL, solo lectura) a tablas
propias con columnas reales:

| Tabla | Origen | Llave | Notas |
| --- | --- | --- | --- |
| `commercial_suppliers` | `consumo.productos` (`cod_proveedor`, `proveedor`) | `code` | Es el **«Cod. Proveedor»** del Excel (649 códigos). No es una identidad tributaria. El margen ya no vive aquí: es [uno por producto](margenes-productos.md). |
| `commercial_supplier_nits` | `consumo.producto_proveedor` | `(proveedor comercial, nit)` | NITs probables por historial de compras; el de más productos es el principal. Se confirman a mano (`PATCH /commercial/suppliers/{code}`). |
| `suppliers` | `consumo.proveedores` | `proveedor_key` | Proveedor legal por sucursal. Sin datos bancarios ni de contacto. |
| `customers` | `consumo.clientes` | `cliente_key` | Cliente por sucursal. Se omite el registro `cia = -1` («desconocido»). El NIT **no** es único. |
| `products` | `consumo.productos` | `producto_key`, `sku` | `sku` es la **referencia JDH**. |
| `product_suppliers` | `consumo.producto_proveedor` | par de llaves | Relación real producto–proveedor legal. |
| `supplier_product_codes` | hojas `Maestro_Sku_*` | proveedor + SKU proveedor + producto | Código del proveedor y factor de empaque. |

Reglas de importación: el NIT se guarda **sin dígito de verificación** (como el DWH);
`is_active` = `estado = 'A'` y `estado_sucursal = 'A'`; las filas que dejan de venir
del DWH se **desactivan** (nunca se borran), y una lectura vacía jamás desactiva nada.
No hay sincronización incremental: las marcas de tiempo de SIESA no indican
modificaciones reales, así que cada corrida es completa.

## Formato estándar de apoyo

Una fila de `supplier_supports` es **una regla**: alcance + beneficio + vigencia.
Los márgenes y precios apoyados **no se guardan**: se calculan (ver abajo). Lo que sí
se guarda es el costo y precio lista de referencia con su fecha (*snapshot*), para que
un apoyo no cambie en silencio cuando cambia el costo vigente.

| Grupo | Campos |
| --- | --- |
| Identidad | `commercial_supplier_id` (por `supplier_code`), `kind`, `funding`, `program`, `external_ref` |
| Alcance (vacío = todos) | `customer_nit`, `customer_branch_id`, `customer_name`, `customer_group`, `product_sku`/`product_id`, `supplier_sku`, `brand`, `regionals[]`, `business_units[]`, `channels[]`, `warehouse` |
| Beneficio | `calc_method`, `supplier_value`, `jdh_pct`, `value_unit`, `target_margin_pct`, `min_margin_pct`, `list_margin_pct`, `pack_factor` |
| Referencia | `reference_cost`, `reference_list_price`, `reference_date` |
| Condiciones | `min_quantity`, `committed_volume`, `budget_amount`, `budget_units`, `units_used`, `stock_quantity`, `conditions`, escalas en `tiers[]` |
| Vigencia y estado | `valid_from`, `valid_to` (vacío = indefinido / UFN), `end_condition` («HASTA NIVELAR COSTO»), `status` |
| Liquidación | `settlement_method`, `settlement_frequency` |
| Trazabilidad | `authorized_by`, `supplier_contact`, `sales_rep`, `notes`, `source`, `source_key`, `issues[]`, `created_by`, `updated_by`, `version` |

Valores cerrados (`GET /supplier-supports/catalogs`):

- `kind`: `tactico_cliente`, `actividad_temporal`, `lista_especial`, `rebate`,
  `recuperacion_cliente`, `liquidacion_inventario`, `presupuesto`, `excepcion_margen`.
- `funding`: `proveedor`, `jdh`, `mixto`. `settlement_method`: `nota_credito`,
  `pie_factura`, `costo_neto_facturado`, `rebate_periodico`.
- **Estado visible (`state`)**: un solo valor para las personas: `vigente`, `vencido`,
  `programado` (calculados por las fechas a la fecha consultada, zona America/Bogotá),
  `inactivo` (desactivado a mano con el interruptor `is_enabled`, reversible: se ve
  atenuado y queda fuera de lo vigente y del apoyo efectivo) o `anulado` (irreversible,
  gana sobre todo lo demás). La revisión de datos es un indicador aparte, `needs_review`
  (verdadero cuando el apoyo quedó `pendiente_revision` por inconsistencias); nunca es
  un estado. Por eso ya no existe la combinación «activo + vencido».
- `status` (`activo`, `pendiente_revision`, `anulado`) es la columna interna del
  registro y sigue en la respuesta por compatibilidad; las pantallas deben mostrar
  `state` y `needs_review`. Un apoyo se anula con motivo (endpoint `void`): no se borra
  nada, queda quién y por qué, y se oculta de las búsquedas salvo `state: "anulado"`.

### Cómo se aplica `supplier_value` (`calc_method`) — antes de las reglas v2

Histórico: las fórmulas vigentes están en «Reglas v2» arriba.

| `calc_method` | `supplier_value` es… | Dinero del proveedor |
| --- | --- | --- |
| `pct_costo` | fracción del costo (0.048 = 4,8 %) | `costo × valor` |
| `pct_precio_lista` | fracción del precio lista | `lista × valor` |
| `valor_unidad` | monto fijo por unidad (o caja) | `valor` |
| `costo_neto` | costo apoyado pactado | `costo − valor` |
| `precio_neto` | no lleva valor; `target_margin_pct` es el margen garantizado | `máx(0, costo − precio de venta × (1 − margen))` |
| `puntos_margen` | puntos de margen que se restan al margen lista | — (margen mínimo = `list_margin_pct − valor`) |
| `pct_ventas` | rebate sobre ventas | — (se liquida por periodo) |
| `margen_minimo` | no usa `supplier_value`; usa `min_margin_pct` | — |

Derivados que devuelve la API (`derived`): `supplier_amount`, `jdh_amount`, `net_cost`,
`pct_on_cost`, `pct_on_list`, `suggested_price`, `margin_order`, `margin_recomposed`,
`min_margin_pct`. Fórmulas de la hoja `Formulas` del Excel: `margen = 1 − costo/precio`,
`precio = costo/(1 − margen)`, `margen recompuesto = 1 − (costo − apoyo)/precio`.

### Precio por unidad y por caja

La unidad mínima de venta (unitario) es la base; la caja es `pack_factor` unidades. Cada
apoyo dice en qué unidad está su costo (`value_unit`: `caja` o `unidad`) y de dónde lo
sabemos (`unit_basis_source`):

- `hoja`: la hoja lo declara (Familia, Kimberly, Team, Ohra: «COSTO CAJA»; Diversey y
  Aderezos: «COSTO UND»).
- `inferido_dwh`: la hoja no lo dice. Se compara el costo del Excel con el costo
  promedio unitario del DWH (`products.avg_unit_cost`, de `consumo.inventario_fecha`,
  sin IVA): parecido a 1× es `unidad`, parecido a N× (el empaque) es `caja`; si no se
  parece a ninguno queda sin decidir.
- `manual`: lo fijó una persona al crear o editar. `desconocido`: no se pudo decidir.

**Obligatoria al crear** (2026-11-20): un apoyo creado a mano (`POST /supplier-supports`,
el lote `/batch` y el de grupo empresarial) debe traer la **Unidad valor** (`unidad`, `caja`
o `kg`). Si no la trae, responde 422 con `field: "value_unit"`. Se exceptúa la Excepción
margen JDH, que no lleva dinero. La vista previa del formulario y la carga masiva la siguen
infiriendo cuando falta, y editar un apoyo no la exige.

`derived` trae los importes en la unidad original y, cuando se conocen la unidad y el
empaque, también por caja y por unidad: `cost`, `list_price`, `supplier_amount_per`,
`net_cost_per` y `suggested_price_per`, cada uno con `{box, unit}`. Sin empaque no hay
precio unitario (ni de caja si el costo es unitario); el empaque que falta se completa
con el del producto en el DWH. El nombre del producto no forma parte de la estructura
del reporte: se identifica por referencia JDH.

### Porcentaje de apoyo JDH y la regla «Npp»

`jdh_pct` es el descuento que pone JDH, aparte del del proveedor. Es un campo extra de
algunos apoyos (McCain lo muestra con más claridad), editable desde la estructura.

En Diversey el apoyo es el campo `% APOYO DIVERSEY`, un porcentaje sobre el costo
unitario. Un registro que venga como `Npp` (por ejemplo `5pp`) en esa columna, en
`$ APOYO DIVERSEY` o en `SKU JDH` significa N % sobre el costo para **todo el portafolio
del proveedor**: se guarda como `pct_costo` sin producto, sin costo ni unidad y con el
programa `DESCUENTO PORTAFOLIO DIVERSEY`. En el libro actual no hay ninguno.

### Listas de costo apoyado por cliente (Rubbermaid)

En `Lista_Summar_RCP` y `Lista_SodexoCedi_RCP` el apoyo es el **nuevo costo apoyado**
(`costo_neto`: `supplier_value` = «COSTO APOYADO», `reference_cost` = «COSTO REGULAR») y
vale solo para ese cliente. Summar tiene NIT (800125313). Sodexo CEDI no: queda
`pendiente_revision` con la inconsistencia `cliente_por_definir`. (El módulo «Casos» que las seguía se eliminó el 2026-11-19, migración `0073`.)

## API (`/api/v1`)

Lectura: cualquier usuario autenticado. Escritura: administradores y miembros del
área `SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG` (por defecto `bi`).

**Qué apoyos ve cada quien** (2026-11-19). La búsqueda y el detalle siguen la cartera del
cotizador (`salespeople`):
- **Todos los apoyos:** los gestores de `apoyos` (BI), quien ve todos los clientes (BI, el líder
  o responsable de Comercial) y los administradores.
- **Vendedor o supervisor:** los apoyos de los NIT de su cartera (sucursales con su código de
  vendedor, o los de su equipo), los de los grupos empresariales de esos clientes (2026-11-20)
  y los apoyos generales, sin NIT ni grupo.
- **Sin clientes asignados:** ningún apoyo. El detalle de un apoyo fuera de la cartera es 404.

`GET /supplier-supports/permissions` trae `customer_scope` (`all`, `team`, `mine` o `none`).

| Método y ruta | Uso |
| --- | --- |
| `POST /supplier-supports/search` | Filtrado dinámico (abajo) |
| `POST /supplier-supports/summary` | Conteos por estado de la misma búsqueda (los KPI) |
| `GET /supplier-supports/search/fields` | Campos, operadores y valores permitidos |
| `GET /supplier-supports/catalogs` | Vocabularios y regionales, unidades, canales y programas en uso |
| `POST /supplier-supports` | Crear (ligado a proveedor, producto y cliente reales) |
| `POST /supplier-supports/batch` | Hasta 500; se validan todos antes de guardar alguno |
| `GET /supplier-supports/{id}` | Detalle con nombres, vigencia, conflictos y derivados |
| `PATCH /supplier-supports/{id}` | Solo los campos enviados (`null` limpia); exige `version` (409 si cambió). `is_enabled: false` desactiva (estado `inactivo`, reversible); también edita la estructura (`jdh_pct`, `supplier_value`, `value_unit`, `pack_factor`…) |
| `POST /supplier-supports/{id}/void` | Anular con motivo; no se borra nada |
| `GET /supplier-supports/{id}/conflicts` | Apoyos que se solapan con este |
| `GET /commercial/suppliers`, `/suppliers/{code}`, `PATCH /suppliers/{code}` | Proveedores comerciales y NITs confirmados (el margen es [por producto](margenes-productos.md)) |
| `GET /commercial/legal-suppliers`, `/customers`, `/products` | Pickers de los maestros |

### Filtrado dinámico

```json
POST /api/v1/supplier-supports/search
{
  "match": "all",
  "conditions": [
    {"field": "supplier.code", "op": "in", "value": ["233", "399"]},
    {"field": "product.brand", "op": "eq", "value": "FAMILIA"},
    {"field": "customer.channel", "op": "in", "value": ["RESTAURANTES"]},
    {"field": "supplier_value", "op": "between", "value": [0.05, 0.2]},
    {"match": "any", "conditions": [
      {"field": "regionals", "op": "overlaps", "value": ["ANTIOQUIA"]},
      {"field": "regionals", "op": "is_empty"}]}
  ],
  "q": "sodexo",
  "state": "vigente",
  "needs_review": false,
  "active_on": "2026-10-09",
  "has_issues": false,
  "effective_only": true,
  "sort": ["-valid_from", "supplier.name"],
  "page": 1, "size": 50
}
```

- Campos: propios (`kind`, `program`, `customer_nit`, `product_sku`, `regionals`,
  `valid_to`, `supplier_value`…), del proveedor (`supplier.*`), del producto
  (`product.*`: marca, categoría, línea…), del cliente (`customer.*`: canal, unidad de
  negocio, grupo, ciudad, vendedor; se consultan en el maestro por NIT) e inconsistencias
  (`issues.code`).
- Operadores por tipo: texto (`eq`, `neq`, `in`, `nin`, `contains`, `starts_with`,
  `is_null`, `not_null`; sin distinguir mayúsculas), número y fecha (`eq`, `neq`, `gt`,
  `gte`, `lt`, `lte`, `between`, `in`), arreglos (`overlaps`, `contains_all`,
  `is_empty`, `not_empty`).
- Grupos anidados (3 niveles) con `match: all | any`. Un campo u operador desconocido
  es 422; nada se interpola en el SQL.
- **Búsqueda base**: `customer_nits`, `supplier_codes` y `product_skus` (listas) filtran
  por cliente, proveedor y referencia. Son independientes y opcionales (puede enviarse
  uno, dos o los tres) y se combinan con AND entre sí y con `conditions`. Por cliente
  también trae los apoyos de su grupo empresarial (`group_wide`, 2026-11-20). Estado y tipo
  se filtran libremente aparte.
- `state` (`vigente`, `vencido`, `programado`, `inactivo`, `anulado`) y `needs_review`: los
  filtros rápidos (`inactivo` = desactivado a mano, reversible). `active_on` fija la fecha
  de la vigencia (`validity` sigue aceptándose).
- **Por vencer** (2026-11-20): `state` y `validity` aceptan `por_vencer`: vigente y con
  `valid_to` entre `active_on` y un mes calendario después, incluido (del 20 de noviembre, hasta
  el 20 de diciembre; del 31 de enero, hasta el último día de febrero). No es un estado: esas
  filas siguen `vigente` y traen `expiring: true`. Uno sin fecha fin nunca está por vencer.
- **Conteos** (`POST /supplier-supports/summary`, mismo cuerpo que `/search`; la página y el
  orden se ignoran): `vigente`, `por_vencer` (parte de `vigente`), `programado`, `vencido`,
  `inactivo` y `total` (sin anulados), con los mismos filtros y la misma cartera del que
  consulta. `state` y `validity` no cuentan, así que cada número es lo que listaría ese chip.
  `effective_only`: deja el apoyo
  vigente efectivo por proveedor × cliente × SKU (el de inicio más reciente, p. ej. un
  «reemplazo temporal»). `has_conflicts`: apoyos que se solapan en la misma regla y
  periodo (mismo proveedor, tipo, programa, cliente, SKU, SKU del proveedor, bodega y
  regional); **se permiten y se advierten**, no se bloquean. No cuentan como conflicto
  dos filas sin ninguna fecha (son actualizaciones sucesivas de costo) ni filas sin
  cliente, producto ni bodega (programas de todo el proveedor).

## Formulario de creación

`GET /supplier-supports/form-schema` describe el formulario para que la pantalla se arme sola:
cada campo (`name`, `label`, `group`, `type`, `choices`, `help`) y, por método de cálculo
(`methods`), cómo se escribe el valor (`value_kind`: `percent`, `money` o `none`), qué campos
exige (`requires`) y cuáles muestra (`shows`). Siempre se exigen `kind` y `calc_method`. La
creación es `POST /supplier-supports` y las mismas reglas que en el formulario las aplica el
servidor. El catálogo vive en `domain/fields.py` y es el mismo de la plantilla de carga masiva.

## Perfil de apoyos por proveedor

Cada proveedor comercial puede tener un **perfil** (`/supplier-support-profiles`) con lo que lo
distingue, sin programar:

- `defaults`: valores que prellenan el apoyo (`kind`, `calc_method`, `funding`, `value_unit`,
  `settlement_method`, `program`, `regionals`, `business_units`, `channels`, `jdh_pct`,
  márgenes, `authorized_by`…). La pantalla de creación los usa de partida y la carga masiva los
  aplica a toda fila que no traiga su propia columna. Es donde se fija, por ejemplo, la base del
  % de un proveedor (`pct_costo` o `pct_precio_lista`).
- `column_map`: campo → encabezados del archivo del proveedor (`{"supplier_value": ["% APOYO"]}`).
- `sheet`: hoja del libro que se lee.

| Método y ruta | Uso |
| --- | --- |
| `GET /supplier-support-profiles` | Perfiles existentes |
| `GET /supplier-support-profiles/{supplier_code}` | Un perfil (404 si no tiene) |
| `PUT /supplier-support-profiles/{supplier_code}` | Crea (sin `version`) o reemplaza (con `version`; 409 si cambió) |
| `DELETE /supplier-support-profiles/{supplier_code}` | Borra el perfil; los apoyos ya creados no se tocan |

Los porcentajes se guardan como fracción en texto (`"0.05"`). Escritura: gestores.

## Plantilla general (centralización de proveedores)

Un solo formato para todos los proveedores, basado en la propuesta TI v0.3
(`Plantilla_Apoyos_Proveedor_v0_3.xlsx`). **El proveedor solo escribe lo que se negocia**; lo que
ya existe en el DWH no se pide y lo calculado no se diligencia:

La plantilla corta (la que se descarga por defecto) trae solo estas columnas, en este orden y sin
ninguna otra (BI, 2026-11-18). **Obligatorio: el `Tipo apoyo`**; el proveedor se elige en la Suite
al subir.

| Columna | Cuándo |
| --- | --- |
| `Clase apoyo` | Apoyo táctico, Actividad descuento temporal o Excepción margen JDH (lista). Vacía se deduce |
| `Tipo apoyo` | Los seis tipos de las reglas v2 (lista) |
| `Valor apoyo` | El valor según el tipo |
| `Unidad valor` | Unidad o Caja (lista); la caja se divide por el factor de empaque del maestro |
| `Excepción margen mínimo autorizado JDH` | Opcional: da el precio apoyado mínimo |
| `Fecha Inicio`, `Fecha Fin` | Formato de fecha ya aplicado. Fecha Fin vacía o `INDEFINIDO` = sin fin. La fecha inicio es parte de la llave |
| `NIT Cliente`, `Cliente` | Vacío = todos los clientes. El nombre sale del maestro si se deja vacío |
| `Sucursal` | Vacía = todas |
| `Grupo Empresarial` | Sin NIT: un apoyo por cada cliente activo del grupo |
| `SKU JDH` | Vacío = todo el portafolio |
| `SKU Proveedor` | Se toma del maestro de productos; solo sin SKU JDH sirve para encontrarlo |
| `Regional`, `Unidad Negocio`, `Canal` | Varias separadas por coma. Vacío = todas |
| `Bodega` | Vacía = todas |

La descarga con datos suma `Observaciones` (el motivo de una Validación No) y `Validación`.

**Margen apoyado.** Reemplaza al `% Apoyo JDH` en la plantilla. Se calcula así:

1. El precio lista es el costo del DWH al **margen general del producto** (maestro de márgenes,
   `Margen Lista 1`).
2. El dinero del proveedor deja un **margen recompuesto** a ese precio: `1 − costo neto / precio lista`.
3. El margen apoyado dice hasta dónde puede bajar el margen sobre ese costo neto. El **precio
   apoyado** es `costo neto / (1 − margen apoyado)` y el **descuento al cliente** es lo que ese
   precio queda por debajo del precio lista.

En la respuesta van en `derived`: `product_margin_pct`, `recomposed_list_margin`,
`supported_margin_pct`, `supported_price` (y `supported_price_per` por caja y unidad) y
`customer_discount_pct`. Se guarda en `supported_margin_pct` (migración `0061`). El `% Apoyo JDH`
y el panel de solicitudes JDH siguen funcionando como antes.

Todo lo demás es **opcional y no va en la plantilla corta**: `% Apoyo JDH`, `Unidad del valor`
(caja o unidad; vacío se infiere del DWH), `Margen garantizado` (obligatorio con `Precio apoyado
sin IVA`), `Autorizado por` (quién autorizó comercialmente el apoyo, texto libre),
`Volumen Comprometido` y `Referencia externa` (n.º de acuerdo; hoy solo Kimberly), `Forma de
liquidación`, `Cod. Proveedor`. Se leen igual si el archivo las trae y están en la plantilla
completa (`full=true`).

| Del DWH (no se pide) | Calculado (no se diligencia) |
| --- | --- |
| Razón social, descripción, equivalencia de SKU, factor U.E., **costo** (regla del cotizador, ver [apoyos en el DWH](apoyos-dwh.md)), margen lista por canal | % de apoyo sobre costo y sobre lista, $ apoyo por unidad y por caja, costo neto, precio apoyado sugerido, margen real y recompuesto, margen mínimo de pedido, estado de vigencia |

`Tipo de apoyo` es el método de cálculo (`calc_method`); la clase (`kind`, columna opcional
`Clase de apoyo`) se deduce si no viene: `excepcion_margen` con margen mínimo, `tactico_cliente` con
NIT y `actividad_temporal` sin NIT. `GET /supplier-supports/bulk/template` baja la plantilla corta;
`full=true`, todas las columnas. `form-schema` trae `calc_method_labels`.

## Carga masiva por archivo

`POST /supplier-supports/bulk` (`multipart/form-data`, campo `file` `.xlsx`/`.csv`). Cada fila
pasa por las mismas validaciones que un apoyo creado a mano (proveedor, referencia JDH y NIT
deben existir en los maestros) y la carga es **todo o nada**: si una sola fila falla no se guarda
ninguna, aunque se pida `apply=true` (`blocked`). El reporte dice todos los errores de una vez
(fila, campo y motivo) para corregir y volver a subir. Los maestros de todo el archivo se
consultan una sola vez (3 consultas, no 3 por fila).

**Actualiza por llave.** Llave = proveedor + NIT cliente + SKU JDH + fecha inicio (BI,
2026-11-18). La clase, la referencia externa y el SKU proveedor no son parte de ella: una fila que
los cambia actualiza el mismo apoyo. El SKU proveedor solo ocupa el lugar del SKU JDH cuando el
maestro no lo pudo resolver. Cada fila:

- **crea** el apoyo si la llave no existe;
- **actualiza** el existente si algo cambió, **solo con las columnas que trae el archivo**: una
  columna ausente conserva lo guardado (subir sin `% Apoyo JDH` no borra un aporte aprobado);
- lo deja **sin cambio** si dice lo mismo.

Una renovación con otra fecha inicio es un apoyo nuevo. Los apoyos vigentes o programados de los
mismos proveedores y clases que el archivo ya no trae se reportan como **ausentes**
(`missing`, `missing_preview`); con `expire_missing=true` los que ya empezaron se cierran el día
anterior a la carga (`origen = vencimiento`). Nada se borra.

Cada carga aplicada queda en `supplier_support_loads` (archivo, SHA-256, hoja, proveedor, usuario,
fecha y conteos) y cada apoyo que tocó apunta a ella (`last_load_id`, `last_origin = carga`). Todo
se publica en el DWH ([apoyos en el DWH](apoyos-dwh.md)); una carga de más de 5.000 apoyos se
publica en el horario tranquilo del DWH (`publish_not_before`).

| Parámetro | |
| --- | --- |
| `file` | `.xlsx`, `.xlsm` o `.csv` (máximo `SUPPLIER_SUPPORTS_MAX_UPLOAD_MB`, 10 MB, y `SUPPLIER_SUPPORTS_MAX_BULK_ROWS`, 10.000 filas) |
| `apply` | `false` (defecto) simula; `true` guarda si no hay errores |
| `supplier_code` | Proveedor de las filas sin columna propia; además elige su perfil |
| `sheet` | Hoja; si falta, la del perfil y luego la primera con encabezados |
| `expire_missing` | `true` cierra los ausentes ya empezados |
| `defaults`, `column_map` | Objetos JSON (campos del formulario) que prevalecen sobre el perfil en esta carga |

El reporte suma `to_create`, `to_update`, `unchanged`, `missing`, `to_expire`, y por fila de
`preview` su `action` (`crear`, `actualizar`, `sin_cambio`) y `support_id`. Aplicada: `load_id`,
`created`, `updated`, `expired` y `publish_not_before`.

Cómo se lee el archivo:

1. **Encabezados**: se busca entre las primeras 15 filas la que reconozca más campos (mínimo 2).
   Se reconocen los nombres de la plantilla (`GET /supplier-supports/bulk/template`), sus
   sinónimos habituales (`SKU JDH`, `% DCTO`, `FECHA INICIAL`…), el mapeo del perfil y el de la
   petición, sin importar tildes ni mayúsculas. Las columnas que nadie usó se devuelven en
   `ignored_columns` y las usadas en `mapped_columns`, para armar el mapeo desde la pantalla.
2. **Valores**: celda de la fila > `defaults` de la petición > `defaults` del perfil. Las
   columnas cerradas (`Tipo`, `Método de cálculo`…) usan los códigos de `catalogs`.
3. **Porcentajes**: `5 %`, `5` o `0,05` se leen como 5 %; menor que 1 es fracción y de 1 a 100
   son puntos. Los métodos que no son porcentaje (`valor_unidad`, `costo_neto`…) leen dinero.
4. **Fechas**: `dd/mm/aaaa`, `aaaa-mm-dd` o fecha de Excel; vacío, `INDEFINIDO` o `UFN` = sin
   fecha final. Alcance (`regionals`, `business_units`, `channels`) con varios valores
   separados por coma, barra o punto y coma; `TODOS` = sin restricción.
5. Dos filas con la misma llave en el mismo archivo son error (`Fila repetida`). Las escalas
   (`tiers`) no van en el archivo: se agregan luego por `PATCH`.

La respuesta trae `preview` (las 50 primeras filas válidas), `created` (ids, al aplicar) y
`with_conflicts` (cuántos de los creados se solapan con apoyos existentes; se permiten y se
advierten, como siempre). `source` queda como `carga masiva: <archivo>`.

`GET /supplier-supports/bulk/template` baja la plantilla corta (`.xlsx`: hoja `Apoyos` con las
columnas, `Instrucciones` y `Valores`; `full=true` para todas); con `supplier_code` sigue el
formato del perfil: sus encabezados, las columnas que mapea y sin las de valor fijo. `POST /supplier-supports/batch` (JSON) ahora
también guarda en una sola transacción.

## Soportes de los apoyos (documentos)

Cada apoyo puede tener **documentos de soporte** (en su mayoría capturas de pantalla de correos,
porque no todos los apoyos se acuerdan por correo). Se suben desde el maestro y quedan en el
**gestor documental del área de BI** (el maestro de apoyos es de BI), en la carpeta
`soporte_apoyos`, que se crea sola la primera vez (`SUPPLIER_SUPPORTS_DOCUMENTS_AREA_SLUG`, por
defecto `bi`, y `SUPPLIER_SUPPORTS_DOCUMENTS_FOLDER`). Los miembros de BI (y los
administradores) la ven en el gestor como cualquier otra carpeta, con sus documentos
etiquetados, buscables y descargables ahí; los tipos y el tamaño (25 MB) son los del gestor
(`png`, `jpg`, `pdf`, `xlsx`, `zip`…). Borrar un documento en el gestor lo desvincula de sus
apoyos.

Quién hace qué: **subir, vincular y desvincular** es de los gestores de apoyos
(`SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG`, `bi`, y administradores), sin necesidad de ser
miembros de BI. **Ver** los soportes de un apoyo y **abrirlos** desde el maestro lo puede hacer
quien lea el apoyo (todo usuario autenticado), sin ser miembro de BI: el documento solo se abre
a través del apoyo al que está vinculado. En el gestor de BI rigen los permisos normales del
área. Si antes se subieron soportes con otra área configurada,
`scripts/relocate-support-documents.py [--apply]` los mueve a la carpeta de BI (los vínculos
con los apoyos no cambian).

Un documento puede respaldar **varios apoyos** (relación N a N): una captura del correo que
acordó el apoyo de veinte referencias es un solo documento con veinte vínculos. Los apoyos
se eligen **uno a uno** o **dinámicamente** con los mismos filtros de la lista (referencias,
clientes, proveedores, estado, texto, condiciones…):

| Método y ruta | Uso |
| --- | --- |
| `POST /supplier-supports/{id}/attachments` | Sube uno o varios archivos (`files`) a un apoyo |
| `POST /supplier-supports/attachments/preview` | Cuántos apoyos alcanza una selección, sin subir nada |
| `POST /supplier-supports/attachments` | Sube archivos y los vincula a una selección |
| `GET /supplier-supports/{id}/attachments` | Soportes de un apoyo (`support_count`: a cuántos apoyos respalda cada uno) |
| `GET /supplier-supports/{id}/attachments/{document_id}/view` | Enlace temporal firmado (5 min) para **mostrar** el archivo en el navegador (`inline: true`: imágenes, PDF); con `?download=true` lo descarga. 404 si el documento no está vinculado a ese apoyo. Pide uno nuevo en vez de guardarlo |
| `DELETE /supplier-supports/{id}/attachments/{document_id}` | Desvincula; el documento queda en el gestor y en los demás apoyos |

La **selección** es `{"support_ids": [...], "search": {...}}`: ids, una búsqueda o ambas (se
suman sin repetir). `search` es el cuerpo de `POST /supplier-supports/search`
(`product_skus`, `supplier_codes`, `customer_nits`, `state`, `q`, `conditions`…; `page`, `size`
y `sort` se ignoran) y **exige al menos un filtro**, para que una búsqueda vacía no apunte a
todos los apoyos por error. Los anulados no entran. Máximo `SUPPLIER_SUPPORTS_MAX_ATTACH_SUPPORTS`
(1000) apoyos por subida: `preview` informa `capped` y `attach` lo rechaza.

`POST /supplier-supports/attachments` es `multipart/form-data`: `files` (hasta 10), `selection`
(JSON), `document_ids` (JSON: documentos que ya están en `soporte_apoyos`, para vincular a más
apoyos sin volver a subirlos), `name` (solo con un archivo), `description` y `tags` (separadas
por coma). **Todo o nada**: si falla un archivo o el vínculo, los archivos que esa llamada ya
había subido se borran.

Clasificación en el gestor: cada documento lleva la etiqueta `apoyos`, las que se envíen y
`proveedor <código>` por cada proveedor alcanzado (si son más de 10 no se agregan). Sin `name`,
el documento se llama como el archivo más la hora, y si choca (todas las capturas pegadas se
llaman `image.png`) se le agrega un sufijo corto.

En la búsqueda, `has_attachments` (`true`/`false`) lista los apoyos con o sin soporte (para
encontrar los que faltan), y cada apoyo trae `attachment_count`. Migración `0034`.

## Migración del Excel

```bash
poetry run python scripts/sync-dwh-masters.py            # simulación
poetry run python scripts/sync-dwh-masters.py --apply
poetry run python scripts/import-supplier-supports.py "1. MAESTRO APOYOS PROVEEDORES.xlsx" \
    --report inconsistencias.csv                          # simulación
poetry run python scripts/import-supplier-supports.py "1. MAESTRO APOYOS PROVEEDORES.xlsx" \
    --report inconsistencias.csv --apply
```

Necesita `DWH_*` (usuario de solo lectura limitado al esquema `consumo`). Ambos scripts
son simulaciones salvo `--apply` y se pueden repetir: cada apoyo recuerda su hoja y
fila (`source_key`). Cada tabla del libro tiene su adaptador en
`app/modules/supplier_supports/infrastructure/workbook.py`; los apoyos por proveedor
sin código en el Excel (Aderezos, Azteca, Red Bull) se cruzan por nombre y se imprime
el resultado para revisarlo (`--supplier NOMBRE=CODIGO` lo fuerza).

### Recarga desde los archivos por proveedor

Desde noviembre de 2026 los proveedores envían sus tablas por separado
(`Apoyos Proveedor - X.xlsx`, las mismas tablas de Excel del libro). Se recargan **por llave**, sin
duplicar:

```bash
poetry run python scripts/reload-supplier-supports.py "Apoyos proveedores" --report recarga.csv
poetry run python scripts/reload-supplier-supports.py "Apoyos proveedores" --apply
poetry run python scripts/reload-supplier-supports.py "Apoyos proveedores" --expire-missing --apply
```

Lee cada tabla con su adaptador (las tablas se buscan por nombre: algunas hojas cambiaron de
nombre, y Team y Familia cambiaron encabezados) y empareja cada fila con el apoyo guardado por su
llave: crea las nuevas, actualiza lo que cambió y deja igual el resto. Como el importador, no
descarta nada: un NIT o SKU sin cruce queda `pendiente_revision`. Reglas propias de la recarga:

- **Costo y precio lista del archivo** son búsquedas en su propia hoja de productos: solo se usan
  como referencia al crear, nunca actualizan (el costo sale del DWH).
- **El aporte JDH aprobado** en el panel no se reemplaza.
- **Ausentes**: los apoyos de la misma tabla (`source`) que el archivo ya no trae; se cierran el
  día anterior solo con `--expire-missing`.
- Cada archivo es una carga (`supplier_support_loads`) en una sola transacción; lo que toca se
  publica en el DWH. Repetir la recarga sin cambios no escribe nada.

Simulación del 10-nov-2026 contra la importación del 13-oct (base de desarrollo): 14.962 filas,
2.219 nuevas, 3.116 con cambios, 9.571 sin cambio y 146 ausentes. Los cambios son sobre todo
de Familia (la unidad de negocio ahora viene casi en todas las filas) y Team (regional, vendedor,
grupo y vigencias de la hoja que antes estaba «desactualizada»). Kimberly trae 1.091 acuerdos
nuevos.

Nada se descarta por tener datos malos: la fila se importa como `pendiente_revision`
con sus `issues` y el CSV las lista. Pasar un apoyo a `activo` (`PATCH`) limpia sus
inconsistencias.

| `issues.code` | Qué es |
| --- | --- |
| `fecha_invalida` | Fecha imposible (31/09), mal escrita o `1900-…` de una fórmula vacía; los días que no existen se ajustan al fin de mes |
| `fecha_texto` | Fecha leída de texto (`30/10(2026`) |
| `nit_sin_cruce`, `sku_sin_cruce`, `proveedor_sin_cruce` | No existe en el maestro |
| `nit_secuencial_sospechoso` | NIT consecutivo bajo el mismo nombre (error de arrastre) |
| `duplicado` | Renglón repetido en la misma hoja |
| `base_ambigua` | El % se aplica a precio y a costo en la misma hoja (Dodeka/Ohra) |
| `valor_texto`, `valor_faltante` | Porcentaje escrito como texto o no escrito (rebates de Azteca y tercerizados) |

Decisiones tomadas al normalizar (revisables): `TODOS`/`NACIONAL` = sin restricción;
`ANTIQOUIA` → `ANTIOQUIA`; `FOOD` → unidad `FOOD SERVICE`; `MAYORISTAS` → canal
`MAYORISTAS Y DISTRIBUIDORES`; fechas `INDEFINIDO`/`UFN`/vacías = sin fecha final;
`Condiciones Especiales` (márgenes mínimos autorizados, sin dinero del proveedor; el
validador de pedidos no las aplica, ver [condiciones especiales](condiciones-especiales.md))
→ `excepcion_margen`, expandidas por proveedor × referencia × cliente; Unilever se carga con el código 399
(ACO 1126 y Professional 1111 quedan por separar); Kimberly con el de Colpapel (1098)
porque no tiene código propio.

## Regional y unidad de negocio por hoja

Cada apoyo trae `regionals`, `business_units` y `channels`. Solo se llenan donde la hoja
los trae (no se deducen del cliente); donde no, quedan vacíos y las pantallas ocultan la
columna si la página no tiene ningún valor. Cobertura del libro actual (% de apoyos):

| Hoja | Regional | Unidad de negocio | Canal |
| --- | --- | --- | --- |
| Mccain, Ohra, Team Panadería, Griffith, Red Bull | 100 % | — | — |
| Azteca | 97 % | 97 % | — |
| Diversey | 97 % | — | — |
| Aderezos | 88 % | 88 % | — |
| Team (desactualizada) | 69 % | — | 1 % |
| Condiciones especiales | 82 % | 93 % | 4 % |
| Familia | — | 70 % | 30 % |
| Rubbermaid | — | — | 41 % |
| Unilever, Kimberly, Estra, GRB | — | — | — |

## Pendiente de definir con el negocio

1. Base del % por proveedor (costo, lista 1, PVP o mayorista): hoy Ohra y Griffith
   mezclan bases.
2. Margen lista autoritativo (el [margen por producto](margenes-productos.md) puede
   contradecir las hojas de Unilever y Diversey).
3. Si `puntos_margen` (Rubbermaid, Upfield) son realmente puntos o un % sobre precio.
4. Si el costo apoyado se factura neto o se reclama por nota crédito.
5. Qué apoyo gana cuando se solapan, y si los de cliente, grupo, canal y regional se
   acumulan.
6. Porcentajes y escalas de los rebates (Azteca, Juniper, «rebate por escalas»).
7. Qué se hace con lo vencido (Azteca, Sodexo CEDI, Team Panadería, hoja Team
   «desactualizada»): hoy se importa como histórico `vencido`.
8. Semántica de regional (Barranquilla, Urabá) y códigos de Kimberly, Aderezos, Azteca
   y Red Bull.

## Solicitudes comerciales y aporte JDH

Comercial consulta el maestro y solicita cambios; BI y administradores gestionan el maestro y aprueban o rechazan. Ver el [contrato de solicitudes JDH](solicitudes-apoyos.md) para las rutas, permisos, costo neto independiente, auditoría y cancelación exclusiva del aporte JDH.
