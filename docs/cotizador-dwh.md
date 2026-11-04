# DWH para el módulo COTIZADOR — respuesta de la sesión DWH

Fecha de verificación: **2026-11-03**, contra el PostgreSQL del DWH (base `dwh`) con el rol `mcp_readonly`, y contra `UnoEE_Real` (SIESA) con el MCP de SQL Server. Todo lo que sigue sale de consultas a la base, no de documentación, salvo donde se indica. Los NIT, nombres y correos de los ejemplos están anonimizados.

> [!important] Resumen en 6 líneas
> - Histórico de venta: **`consumo.ventas`** (una fila por línea de factura o devolución). No trae IVA ni la lista de precios aplicada; el precio unitario se calcula.
> - Clientes: **`consumo.clientes`** (una fila por sucursal). Mayorista = `cod_canal = '0150'` ("MAYORISTAS Y DISTRIBUIDORES"). Para el precio conviene usar la `lista_precio_id` del cliente y no el canal (ver §9.2.3).
> - Vendedor ↔ usuario de la plataforma: `consumo.vendedores.correo` (correo corporativo `@JDH.COM.CO`), pero **solo 111 de 584 vendedores activos lo tienen**.
> - Costo actual: **no hay una vista de "costo del ítem"**. Sale de `consumo.inventario_corte` (costo promedio por instalación y último costo, por bodega). Varía por centro de operación.
> - Listas de precios: `consumo.precios_lista_vigentes` (precio vigente por lista × SKU × UM, con IVA e impoconsumo ya calculados) y `consumo.listas_precios` (maestro).
> - **Margen objetivo y apoyos tácticos no existen en el DWH**: viven solo en la plataforma.
> - Planos 0126/0127: el diccionario oficial de Impodatos está en la propia BD SIESA (`t661_imp_tablas` / `t662_imp_campos`). **`000300111` = subtipo `00` + versión `03` + compañía `001` + "actualiza registro" `1` + primer carácter del código de lista (`1`)**. Los 3 decimales que van después del precio son **precio mínimo, precio máximo y precio sugerido** (§7).

---

## 1. Histórico de ventas

### 1.1 `consumo.ventas`

- **Grano:** una línea de movimiento de venta (`venta_linea_key`, md5). Incluye ventas (`naturaleza_codigo = 2`, concepto "Ventas") y devoluciones (`naturaleza_codigo = 1`, concepto "Devolución de venta"). Volumen: unas 389 mil líneas de venta entre el 2026-09-01 y el 2026-11-03.
- **Origen:** `mart_ventas.fact_venta` ← `core.venta_linea` ← SIESA `t470_cm_movto_invent` (más el encabezado `t350`/`t461`). Se refresca a diario.
- **Cliente:** `cliente_key` se define por **sucursal**. `nit_cliente` es el `f200_nit` del tercero, el mismo valor que `consumo.clientes.nit`.

| Columna | Tipo | Significado para el cotizador |
|---|---|---|
| `venta_linea_key` | text | Llave única de la línea (desempate final). |
| `nro_transaccion` | text | Documento, p. ej. `FEV-1390174`. **Es texto**: para ordenar hay que extraer la parte numérica (ver SQL). |
| `estado` | text | Estado del documento (`Comprometida`, `Aprobada`, …). |
| `fecha` | date | Fecha de la venta. |
| `co` | text | Centro de operación que factura (`001` Medellín, `005`, `009` Barranquilla, …). |
| `nit_cliente`, `cod_sucursal`, `cliente_key` | text | Cliente (NIT), sucursal y llave de sucursal. |
| `sku` | text | **Referencia** del ítem (`t120.referencia`, no `t120.id`). |
| `producto_key` | text | Llave hacia `consumo.productos`. |
| `cantidad` | numeric | Cantidad **en unidad de inventario** (UNID). Ejemplo: una caja C45 sale como `cantidad = 45`. |
| `unidad_medida_id` | text | Unidad de medida **en que se vendió** la línea (UNID, C45, C18, …). |
| `cantidad_empaque` | numeric | Cantidad expresada en empaques. |
| `kg` | numeric | Kilos. |
| `valor_bruto` | numeric | Valor de la línea antes de descuentos y **antes de IVA**. |
| `descuento_linea`, `descuento_global` | numeric | Descuentos en pesos. |
| `subtotal` | numeric | `valor_bruto − descuento_linea − descuento_global`: neto antes de IVA. |
| `dscto` | numeric | % de descuento de la línea **expresado como fracción** (`0.0800` = 8 %). |
| `costo` | numeric | Costo total de la línea **al momento de la venta** (costo histórico, no el actual). |
| `vendedor_key`, `vendedor` | text | Vendedor de la línea (llave hacia `consumo.vendedores` y nombre). |
| `concepto`, `motivo`, `concepto_linea_id`, `motivo_id` | text | Concepto y motivo SIESA. |
| `bodega`, `cod_bodega` | text | Bodega de despacho. |
| `pedido_documento`, `co_pedido`, `fecha_pedido` | | Pedido de origen, si existe. |
| `naturaleza_codigo` | smallint | 2 = venta, 1 = devolución. |
| `identificacion` | text | Identificación del cliente. |

**Lo que NO trae `consumo.ventas`:**

| Pediste | Situación |
|---|---|
| Precio unitario antes de IVA | Se calcula: `valor_bruto / cantidad` (precio de lista o digitado, por unidad de inventario) y `subtotal / cantidad` (neto después de descuentos). |
| Precio después de IVA | No está expuesto. `mart_ventas.fact_venta.valor_neto` sí lo tiene (incluye impuestos: 12.504 → 14.880 con IVA 19 %), pero la vista `consumo` no lo publica. Para el cotizador es mejor aplicar el IVA **actual** del ítem (§3.2) sobre el precio neto. |
| Lista de precios aplicada en la venta | **No llega a `core` ni a `consumo`.** Solo existe en `staging.stg_siesa__t470_cm_movto_invent.id_lista_precio` (y `id_unidad_precio`, `precio_uni`). Si el cotizador la necesita, hay que pedir que se suba a `core.venta_linea` → `consumo.ventas`; no conviene leer staging directamente. |
| Obsequios | `es_obsequio` está en `fact_venta` pero no en la vista. El filtro `subtotal > 0` los excluye en la práctica. |

Ejemplo de filas reales (2026-10-30; NIT anonimizado):

| nro_transaccion | fecha | nit_cliente | sku | cantidad | unidad_medida_id | valor_bruto | descuentos | subtotal | dscto | costo |
|---|---|---|---|---|---|---|---|---|---|---|
| FEV-1390174 | 2026-10-30 | 890*** | 2537 | 3 | UNID | 12.504 | 0 | 12.504 | 0 | 10.210,50 |
| FEV-1390234 | 2026-10-30 | 900*** | 1485 | 1 | UNID | 3.000 | 0 | 3.000 | 0 | 2.297,85 |
| (caja) | 2026-10-30 | — | 12799 | **45** | **C45** | 149.551 | 0 | 149.551 | 0 | — |

En la última fila, `cantidad = 45` está en UNID y la venta fue de 1 caja C45: el precio por unidad de inventario es 149.551 / 45 = 3.323,36.

### 1.2 Otras vistas de venta (no recomendadas para el cotizador)

- `consumo.venta_comercial`: agregada (fecha, mes, co, vendedor del cliente, referencia, cantidad, valor_subtotal). Sin cliente ni documento.
- `consumo.ventas_remisionada`: remisiones todavía sin facturar.
- `consumo.pedidos_venta`: pedidos.

### 1.3 SQL recomendado: último precio por cliente × referencia en N meses

Se probó contra producción (plan por índice `cliente_key` de `fact_venta`; unos 4 ms para un cliente). Filtra por `cliente_key` mediante una subconsulta sobre `consumo.clientes` en lugar de `nit_cliente`, para que el índice se use:

```sql
-- :nit      = NIT del cliente (consumo.clientes.nit)
-- :meses    = 6 o 12
with lineas as (
    select
        v.sku,
        v.producto_key,
        v.fecha,
        v.nro_transaccion,
        v.co,
        v.cod_sucursal,
        v.unidad_medida_id                         as um_venta,
        v.cantidad                                 as cantidad_und_inv,
        v.valor_bruto,
        v.subtotal,
        v.dscto,
        round(v.valor_bruto / v.cantidad, 4)       as precio_bruto_unit,   -- antes de dcto, antes de IVA, por UNID
        round(v.subtotal    / v.cantidad, 4)       as precio_neto_unit,    -- después de dcto, antes de IVA, por UNID
        v.vendedor_key,
        v.vendedor,
        count(*) over (partition by v.sku)         as n_lineas_ventana,
        row_number() over (
            partition by v.sku
            order by v.fecha desc,
                     nullif(regexp_replace(v.nro_transaccion, '\D', '', 'g'), '')::bigint desc nulls last,
                     v.subtotal / v.cantidad desc,      -- misma factura con 2 líneas del SKU: la de mayor precio
                     v.venta_linea_key desc
        ) as rn
    from consumo.ventas v
    where v.cliente_key in (select c.cliente_key from consumo.clientes c where c.nit = :nit)
      and v.naturaleza_codigo = 2          -- solo ventas, no devoluciones
      and v.cantidad > 0
      and v.subtotal > 0                   -- excluye obsequios / líneas en cero
      and v.fecha >= (current_date - make_interval(months => :meses))
)
select *
from lineas
where rn = 1
order by fecha desc, sku;
```

Notas:
- **El cliente se agrupa por NIT, es decir, todas sus sucursales.** Si el cotizador trabaja por sucursal, filtra por `cliente_key` directamente.
- `nro_transaccion` tiene prefijo por tipo de documento y CO (`FEV-…`). La parte numérica solo desempata bien dentro del mismo prefijo. Como el primer criterio es la fecha, en la práctica no hay ambigüedad.
- El precio queda **por unidad de inventario**, la misma base que `precios_lista_vigentes.precio_unidad_inventario` y que el costo de inventario. Así se compara todo en la misma unidad.

Resultado real (un cliente del canal mayorista, 12 meses; anonimizado):

| sku | fecha | nro_transaccion | um | cantidad | precio_bruto_unit | precio_neto_unit | dscto | co | n_lineas |
|---|---|---|---|---|---|---|---|---|---|
| 22858 | 2026-10-24 | FEV-105778 | UNID | 8 | 44.500,00 | 44.500,00 | 0 | 005 | 43 |
| 23197 | 2026-10-24 | FEV-105778 | UNID | 8 | 51.093,00 | 47.005,50 | 0,08 | 005 | 14 |
| 1868 | 2026-07-10 | FEV-98622 | UNID | 20 | 2.414,00 | 2.414,00 | 0 | 005 | 1 |

---

## 2. Clientes

### 2.1 `consumo.clientes`

**Grano:** una fila por **sucursal de cliente** (`cliente_key`). El mismo NIT aparece varias veces, una por sucursal. Origen: `mart_maestro.dim_cliente` (SIESA t200/t201) + `dim_vendedor` + `core.lista_precio`.

| Columna | Contenido |
|---|---|
| `cliente_key` | Llave de la sucursal (es la que usa `consumo.ventas.cliente_key`). |
| `cia` | Compañía (1). |
| `nit` | NIT (`f200_nit`). **Es el campo para cruzar con `ventas.nit_cliente`**. No uses `identificacion` para eso. |
| `razon_social` | Razón social del tercero. |
| `id_sucursal`, `sucursal`, `nombre_establecimiento` | Sucursal. |
| **`cod_canal`**, **`canal`** | Canal comercial (criterio de cliente SIESA). |
| `cod_vendedor`, `nombre_vendedor` | Vendedor asignado **a la sucursal**. `cod_vendedor` = `consumo.vendedores.codigo`. |
| `cod_unidad_negocio`, `unidad_negocio` | Unidad de negocio. |
| `cod_co_factura`, `co_factura` | CO que factura al cliente. Sirve para elegir el costo (§3.3). |
| **`lista_precio_id`**, **`lista_precio`** | Lista de precios asignada a la sucursal en SIESA. |
| `estado` | Estado del **tercero** (`A`/`I`). |
| `estado_sucursal` | Estado de la **sucursal** (`A`/`I`). Para cotizar conviene filtrar las dos. |
| `condicion_pago`, `desc_condicion_pago`, `cod_grupo_empresarial`, `grupo_empresarial` | Comerciales. |
| `correo`, `telefono`, `celular`, `direccion`, `ciudad`, `departamento`, zonas… | Contacto y ubicación (PII). |

### 2.2 Valores de `canal` (conteo real de sucursales, 2026-11-03)

| cod_canal | canal | sucursales | activas |
|---|---|---|---|
| 0152 | RESTAURANTES | 24.190 | 19.179 |
| 0151 | EMPRESAS | 9.596 | 7.598 |
| 0147 | PERSONAS NATURALES CON ESTABLECIMIENTO | 5.350 | 4.116 |
| 0154 | HOGAR | 4.829 | 4.478 |
| 0153 | PANADERIAS, REPOSTERIAS Y HELADERIAS | 4.510 | 3.194 |
| 0156 | AUTOSERVICIO | 4.437 | 3.133 |
| **0150** | **MAYORISTAS Y DISTRIBUIDORES** | 3.502 | 2.747 |
| *(NULL)* | *(sin canal)* | 3.462 | 3.432 |
| 0142 | HOTELES Y CLUBES | 2.751 | 2.399 |
| 0145 | OPERADOR CATERING | 2.158 | 2.033 |
| 0144 | OTROS | 2.016 | 1.864 |
| 0146 | VENDING MACHINE DE CAFÉ | 1.459 | 1.210 |
| 0141 | ENTRETENIMIENTO | 1.196 | 972 |
| 0149 | SECTOR SALUD | 1.178 | 994 |
| 0148 | SECTOR EDUCATIVO | 805 | 651 |
| 0143 | CAJAS DE COMPENSACION | 776 | 762 |
| 0159 | SALSAMENTARIAS | 358 | 302 |
| 0158 | E-COMMERCE | 313 | 313 |
| 0157 | SERVICIOS | 192 | 192 |
| 0155 | GOBIERNO | 108 | 107 |

**Mayorista = `cod_canal = '0150'`.** Compara por código, no por el texto. Hay 3.462 sucursales sin canal: el cotizador tiene que decidir qué hacer con ellas (lo razonable es tratarlas como "resto" y mostrar una advertencia).

### 2.3 Advertencia: el canal no determina la lista de precios

En SIESA, **"Mayoristas" no es una sola lista: hay una por regional**. Y **815 sucursales activas del canal mayorista tienen asignada la lista `1` GENERAL**. Listas reales de los clientes activos del canal 0150:

| lista_precio_id | lista_precio | sucursales activas canal 0150 |
|---|---|---|
| 1M | MAYORISTAS MEDELLIN | 925 |
| 1 | GENERAL | 815 |
| 10U | MAYORISTAS CENTRO | 583 |
| 10T | MAYORISTAS OCCIDENTE | 144 |
| 10N | MAYORISTAS ORIENTE | 139 |
| 10V | MAYORISTAS URABA | 35 |
| 87Y | NEGOCIACION MCCAIN - MAYORISTA COSTA | 13 |
| 11N | MAYORISTAS CARTAGENA | 8 |

Para el **margen objetivo** puedes seguir usando la regla canal 0150 → "Mayoristas" y resto → "Lista 1", ya que ese maestro es de la plataforma. Para el **precio de referencia** y el **código de lista del plano 0126** conviene usar `clientes.lista_precio_id`. Si no, se escriben precios en la lista GENERAL para clientes que en SIESA leen otra lista, o al revés.

### 2.4 Vendedor asignado y su vínculo con el usuario de la plataforma

`consumo.vendedores` (grano: un vendedor, `vendedor_key`; origen `mart_maestro.dim_vendedor` = SIESA + maestro comercial manual `externo.vendedor_info`):

`vendedor_key, codigo, nombre, estado (A/I), supervisor, equipo, clase_equipo, regional, unidad_negocio, cod_centro_costo, centro_costo, correo, celular, cod_tercero, nit, cod_portafolio, portafolio, cobrador (Si/No), vendedor (Si/No), cod_supervisor`

- Unión con el cliente: `consumo.clientes.cod_vendedor = consumo.vendedores.codigo`. En ventas: `consumo.ventas.vendedor_key = consumo.vendedores.vendedor_key`.
- **`correo`** viene del maestro comercial (`correo_corporativo`) y está en mayúsculas (`xxx@JDH.COM.CO`). Compáralo con `lower()`.
- **Cobertura: de 658 vendedores, 584 están activos y solo 111 activos tienen correo** (todos `@jdh.com.co`, 104 distintos). El vínculo por correo sirve para los asesores que están en el maestro comercial, pero el cotizador necesita una alternativa, por ejemplo mapear `codigo` de vendedor en la tabla de usuarios de la plataforma.
- Supervisor: `consumo.supervisores` (`supervisor_key, nit, nombre, correo, celular, rol, equipo, regional, unidad_negocio`).

Ejemplo (anonimizado):

| cliente_key | nit | razon_social | id_sucursal | cod_canal | canal | cod_vendedor | lista_precio_id | lista_precio | estado | estado_sucursal | correo vendedor |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0633e683… | 900***** | HAMB... | 003 | 0152 | RESTAURANTES | 0051 | 1 | GENERAL | A | I | xxx@JDH.COM.CO |
| 8a837541… | 900***** | REST... | 001 | 0152 | RESTAURANTES | 0040 | 1 | GENERAL | A | I | *(NULL)* |

`consumo.clientes_segmentacion` (grano: tercero/NIT) agrega segmento ABC, frecuencia, `ultima_compra`, `venta_12m` y el canal de la sucursal principal. Puede servir para enriquecer la cabecera del cotizador.

---

## 3. Ítems, impuestos y costo actual

### 3.1 `consumo.productos` (grano: un ítem, `producto_key`; la referencia es `sku`)

`producto_key, sku, sku_proveedor, descripcion, descripcion_corta, estado (A/I), tipo_item, cod_proveedor, proveedor, fabricante, marca, categoria, subcategoria, linea, sublinea, grupo, origen, peso, volumen, factor_empaque, unidad_inventario_id, unidad_inventario, unidad_empaque_id, peso_empaque, volumen_empaque, codigo_barras_principal, grupo_impositivo_id, grupo_impositivo, iva_venta_pct, iva_compra_pct, fecha_creacion, fecha_actualizacion, fecha_inactivacion, item_id, proveedor_etiqueta, vida_util_dias`

- **Unidad de medida:** `unidad_inventario_id` (UNID, KG…) y `unidad_empaque_id` + `factor_empaque` (C18 = caja de 18).
- **Proveedor:** `cod_proveedor`/`proveedor` es el **proveedor-criterio del ítem** (clasificación). El proveedor real de compra está en `consumo.producto_proveedor` (`proveedor_key, nit, razon_social_proveedor, primera_compra, ultima_compra, lineas_compra`; un ítem puede tener varios).
- **IVA:** `iva_venta_pct` es la tasa de IVA de venta ya resuelta. `grupo_impositivo_id` es el grupo SIESA (p. ej. `1614` = "IVA19%,RTFTCOMP2.5%,RIVA15%,RICA1%").

> [!warning] Estado del ítem
> Hasta el 2026-10-28 `estado` podía estar desfasado (t121 no movía su `ts` al cambiar de estado; 123 activas figuraban inactivas y 34 al revés). Se propuso extraer t121 completo. Si el cotizador filtra por `estado = 'A'`, confirma que ese cambio ya se desplegó.

### 3.2 `consumo.producto_impuestos` (grano: ítem × llave de impuesto × indicador compra/venta)

`producto_key, sku, tipo_inventario_servicio_id, tipo_inventario_servicio, grupo_impositivo_id, grupo_impositivo, indicador ('venta'|'compra'), clase_impuesto_id, clase_impuesto, llave_impuesto_id, llave_impuesto, tipo_calculo ('porcentaje'|'valor'), tasa_pct, porcentaje_base, es_descontable, llave_activa, llave_descuento_id, porc_imp_valor_desc, es_mayor_valor_costo`

- Usa `indicador = 'venta'`.
- **Impuestos en valor (los de la hoja "Impuestos en valor" y del registro 0127)** son los de `tipo_calculo = 'valor'`:

| clase_impuesto | llave_impuesto_id | llave_impuesto | ítems |
|---|---|---|---|
| Impoconsumo | 0005 | IMPOCONSUMO | 150 |
| Impuesto al consumo licores | 0011 | IMPUESTO AL CONSUMO LICORES IMPOR IVA 5% | 15 |
| Impuesto al consumo licores | 0024 | IMPUESTO AL CONSUMO LICORES NAL IVA 5% | 15 |

  El **valor** del impuesto por lista e ítem no está en esta vista: está en `precios_lista_vigentes.impoconsumo_valor` (§4), que sale de SIESA t127.
- `consumo.productos_icui_ibua`: banderas de ICUI/IBUA (impuestos saludables) por ítem.

Ejemplo:

| sku | descripcion | unidad_inventario_id | unidad_empaque_id | factor_empaque | grupo_impositivo_id | iva_venta_pct | llave_impuesto_id (venta) | tasa_pct |
|---|---|---|---|---|---|---|---|---|
| 2537 | Abrasivo en Polvo Pino 1-A x700g | UNID | C18 | 18 | 1614 | 19 | 0009 IVA DE 19% | 19 |
| 1485 | Abrasivo en Polvo Pino 1-A x500g | UNID | C30 | 30 | 1614 | 19 | 0009 IVA DE 19% | 19 |

### 3.3 Costo actual: `consumo.inventario_corte` (reemplazado, ver §9.2)

**No hay una vista de "costo del ítem" a nivel de producto.** El costo actual sale de la foto de inventario:

- **Grano:** `fecha_corte` × bodega × ítem. Tiene un corte por cierre de mes y **un corte "de hoy"** (último `fecha_corte` = 2026-11-03, periodo 202611, unas 5.644 referencias).
- Columnas: `fecha_corte, periodo, co_bodega, bodega, desc_bodega, referencia (= sku), desc_item, existencia, costo_prom_unit_ins, costo_prom_total, entradas_mes, salidas_mes, producto_key, bodega_key, centro_operacion_key, ultimo_costo_unit, fecha_ultimo_costo`.
- **`costo_prom_unit_ins`** es el **costo promedio ponderado por instalación** (SIESA t400) por unidad de inventario. Es el mismo costo que usa SIESA para el costo de venta (`consumo.ventas.costo`) y el que corresponde usar para el margen.
- **`ultimo_costo_unit`** + `fecha_ultimo_costo` es el **último costo de entrada** (`core.existencia_actual`). Puede tener meses de antigüedad en bodegas de poca rotación.
- **El costo varía por instalación o CO:** 2.557 de 5.644 referencias tienen más de un costo promedio entre bodegas con existencia (mediana de dispersión máx/mín de 5 %). Ejemplo, sku 2537: MDE1 Medellín 3.403,65; BAQ1 Barranquilla 3.640,12.

Recomendación para el cotizador: el costo promedio del **CO del cliente** (`clientes.cod_co_factura` = `inventario_corte.co_bodega`) en el último corte. Si ese CO no tiene existencia, usar el promedio ponderado por existencia de todas las bodegas:

```sql
with ult as (select max(fecha_corte) f from consumo.inventario_corte),
inv as (
    select referencia, co_bodega,
           sum(existencia * costo_prom_unit_ins) / nullif(sum(existencia), 0) as costo_co
    from consumo.inventario_corte
    where fecha_corte = (select f from ult) and existencia > 0
    group by referencia, co_bodega
),
glob as (
    select referencia, sum(existencia * costo_prom_unit_ins) / nullif(sum(existencia), 0) as costo_global
    from consumo.inventario_corte
    where fecha_corte = (select f from ult) and existencia > 0
    group by referencia
)
select g.referencia as sku,
       coalesce(i.costo_co, g.costo_global) as costo_actual_unid_inv,
       (i.costo_co is null)                 as costo_es_global
from glob g
left join inv i on i.referencia = g.referencia and i.co_bodega = :co_cliente;
```

---

## 4. Listas de precios

### 4.1 Maestro: `consumo.listas_precios` (grano: lista)

`lista_precio_id, lista_precio, estado_activo, moneda_id, precios_incluyen_impuesto, tipo_lista_codigo, lista_base_id, factor_base, clientes_asignados, clientes_activos_asignados, precios_vigentes, productos_con_precio_vigente, versiones_de_precio, fecha_ultimo_cambio, sin_precios_cargados`

`lista_precio_id` **es el código SIESA de la lista** (`f112_id`, alfanumérico de hasta 3 caracteres). Es lo que va en las posiciones 20–22 del plano 0126/0127. Principales listas activas:

| lista_precio_id | lista_precio | clientes activos | productos con precio vigente | último cambio |
|---|---|---|---|---|
| **1** | **GENERAL** ("Lista 1") | 28.113 | 7.753 | 2026-10-28 |
| 1M | MAYORISTAS MEDELLIN | 856 | 3.938 | 2026-10-30 |
| 69T | MASIVOS CENTRO | 787 | 3.420 | 2026-10-27 |
| 25 | EMPLEADOS JDH (base = lista 1) | 748 | 18.947 | — |
| 5 | SODEXO | 499 | 3.642 | 2026-10-30 |
| 10U | MAYORISTAS CENTRO | 429 | 2.956 | 2026-10-30 |
| 2 | PROVEEMOS MEDELLIN | 333 | 2.385 | 2026-10-30 |
| 71T | MASIVOS COSTA | 298 | 4.509 | 2026-10-26 |
| 10S | SALSAMENTARIAS BOGOTA | 161 | 2.900 | 2026-10-28 |
| 10N | MAYORISTAS ORIENTE | 133 | 1.251 | 2026-10-28 |
| 10T | MAYORISTAS OCCIDENTE | 96 | 1.804 | 2026-10-28 |

Ninguna de las listas activas revisadas tiene `precios_incluyen_impuesto = true`: **los precios de lista son antes de IVA**.

### 4.2 Precio vigente: `consumo.precios_lista_vigentes`

**Grano:** lista × ítem × unidad de medida, solo el precio vigente hoy (unas 619 mil filas). Origen: SIESA t126 (precio) + t127 (impuestos en valor). Al 2026-11-03 está al día (`max(fecha_actualizacion)` = 2026-11-03 07:43).

`lista_precio_id, lista_precio, lista_activa, producto_key, sku, producto, producto_activo, marca, categoria, linea, sublinea, proveedor, unidad_medida_id, es_unidad_inventario, factor_um, precio_sin_iva, precio_unidad_inventario, precio_incluye_impuesto, iva_venta_pct, iva_valor, impoconsumo_valor, precio_con_impuestos, vigente_desde, usuario_actualizacion, fecha_actualizacion, precio_rowid`

- `precio_sin_iva` está en la UM `unidad_medida_id`. `precio_unidad_inventario` = `precio_sin_iva / factor_um`, por UNID. Para comparar con el histórico usa este último.
- `iva_valor` = `precio_sin_iva × iva_venta_pct`. `impoconsumo_valor` = impuesto en valor de t127. `precio_con_impuestos` = suma de los tres.
- `vigente_desde` = `f126_fecha_activacion`. La vista no tiene "vigente hasta" porque solo muestra lo vigente.

Ejemplos:

| lista | sku | um | factor_um | precio_sin_iva | iva % | iva_valor | impoconsumo_valor | precio_con_impuestos | vigente_desde |
|---|---|---|---|---|---|---|---|---|---|
| 1 GENERAL | 2537 | UNID | 1 | 4.568 | 19 | 867,92 | — | 5.435,92 | 2026-08-12 |
| 1M MAYORISTAS MEDELLIN | 2537 | UNID | 1 | 3.913 | 19 | 743,47 | — | 4.656,47 | 2026-09-24 |
| 1 GENERAL | 12014 (vino tetrapack) | UNID | 1 | 12.141 | 5 | 607,05 | **6.911** | 19.659,05 | — |
| 1 GENERAL | 26278 (ginebra 700) | UNID | 1 | 88.209 | 5 | 4.410,45 | **42.451** | 135.070,45 | — |

### 4.3 Histórico: `consumo.precios_lista_historico`

Mismas columnas, más `vigente_hasta`, `fecha_inactivacion` y `es_vigente`. Grano: cada versión de precio (fila de t126). Sirve si el cotizador quiere mostrar "precio de lista que tenía el cliente cuando compró".

> [!warning] Ediciones y borrados en SIESA
> Por cómo se extrae (incremental), las ediciones y borrados de precios en el ERP pueden tardar o no reflejarse (se documentó el 2026-10-28 para t212; t126 tiene una reconciliación completa pendiente de calendarizar). Para el precio que se va a sobrescribir en SIESA, toma este valor como referencia, no como garantía.

---

## 5. Margen objetivo y apoyos tácticos

**No existen en el DWH.** No hay ninguna tabla o vista con `margen`, `apoyo` o `promo` en `core`, `consumo`, `mart_*` ni `monitoreo` (verificado en `information_schema`). Viven solo en la plataforma.

Lo más cercano en SIESA:
- **Promociones o descuentos de SIESA** (t110, `f126_id_promo_dscto`, `staging…t470.rowid_promo_dscto_precio`): están en staging, sin modelar.
- **"APOYO SUR"** aparece como texto en pedidos 0430 que BEX importa por el webservice. Son notas de pedido, no un maestro de apoyos.

Si en el futuro se quiere el margen real en el DWH, `consumo.ventas` ya trae `subtotal` y `costo` por línea.

---

## 6. Permisos del usuario DWH de la plataforma interna

Roles con login que leen `consumo` (verificado con `has_table_privilege` el 2026-11-03):

| Rol | consumo (37 vistas) | core | mart_maestro / mart_ventas | monitoreo | staging / landing | Límites |
|---|---|---|---|---|---|---|
| **`adm_plataforma`** (superadministrador de la plataforma) | ✅ todas | ✅ | ✅ | ✅ | ✅ (migraciones 051/056/061) | sin `statement_timeout` |
| `svc_bi` (miembro de `dwh_lector_consumo`) | ✅ todas | ❌ | ❌ | ✅ | ❌ | — |
| `dir_comercial` | ⚠️ solo 5 de 37: `clientes`, `productos`, `proveedores`, `vendedores`, `ventas`. **Sin** `precios_lista_vigentes`, `listas_precios`, `inventario_corte`, `producto_impuestos` | ❌ | ❌ | ❌ | ❌ | `statement_timeout=10min`, 5 conexiones |
| `dwh_lector_global` (sin login, para heredar) | ✅ | ✅ | ✅ | ✅ | ❌ | — |

- Si la plataforma se conecta como **`adm_plataforma`**, ya lee todo lo de este documento.
- Si se conecta con un rol de lectura de consumo (`svc_bi` o un rol hijo de `dwh_lector_consumo`), también tiene todo lo necesario, porque todo está en `consumo`.
- Con **`dir_comercial` el cotizador no funciona**: le faltan precios, costo e impuestos. Haría falta un `GRANT`.
- Las vistas de `consumo` se recrean en los builds de dbt. Los permisos se vuelven a aplicar en el despliegue, pero si alguna consulta falla con `permission denied` justo después de un build, ese es el primer sospechoso.
- Para el cotizador lee solo `consumo`. `core`/`mart_*` cambian de forma sin aviso de contrato.

---

## 7. Plano de importación SIESA de listas de precios (0126 / 0127)

### 7.1 Fuente

El diccionario oficial del importador (Impodatos) está **dentro de la BD de SIESA**:
- `UnoEE_Real.dbo.t661_imp_tablas`: tipos de registro y versiones.
- `UnoEE_Real.dbo.t662_imp_campos`: campos con `f662_posicion_inicial`, `f662_tamano`, `f662_tipo_dato` (1 = numérico, 2 = alfanumérico, 3 = fecha) y `f662_ind_obligatorio`.

Versiones disponibles (t661):

| tipo | subtipo | versión | descripción |
|---|---|---|---|
| 0000 | 00 | 01 | Registro inicial |
| 0126 | 00 | 01 … 05 | Lista de precios (V.1 a V.5) |
| 0127 | 00 | 01 … 04 | Impuestos en valor en precios |
| 9999 | 00 | 01 | Registro final |

> [!note] Cómo leer t662
> t662 mezcla los campos del plano con **columnas internas de trabajo** del importador (`f121_rowid`, `f120_rowid`, `f120_id_grupo_impositivo`, `f110_rowid_promo_dscto`, `f126_rowid`, `f127_dec_und`, `f112_ind_puntos_fiel`). Esas columnas tienen posiciones que se solapan con otros campos y **no van en el archivo**. Abajo solo se listan los campos del plano.

Reglas de formato (texto de `f662_observacion`):
- Numérico (tipo 1): con ceros a la izquierda. Los **valores monetarios** van como `000000000000000.0000`: 15 enteros + punto + 4 decimales = 20 caracteres.
- Alfanumérico (tipo 2): alineado a la izquierda y relleno con espacios.
- Fecha (tipo 3): `AAAAMMDD`. Si es opcional y no aplica, va en blanco.

En `t668_imp_conector_detalle` (los planos que ya entran por el webservice, sobre todo BEX) **no hay ningún 0126 ni 0127**. Nadie los ha importado por ese canal, así que no hay filas reales de ejemplo. Los ejemplos de §7.6 están construidos según el diccionario.

### 7.2 Registro inicial 0000 y final 9999 (18 caracteres)

| Pos | Long | Campo | Valor |
|---|---|---|---|
| 1 | 7 | f_numero_reg | Consecutivo del archivo (0000001 en el inicial) |
| 8 | 4 | f_tipo_reg | `0000` / `9999` |
| 12 | 2 | f_subtipo_reg | `00` |
| 14 | 2 | f_version_reg | `01` |
| 16 | 3 | f_cia | `001` |

Real (t668): `000000100000001001`. El 9999 lleva el siguiente consecutivo, por ejemplo `000000499990001001`.

### 7.3 Registro 0126 — lista de precios, **versión 03** (254 caracteres)

| Pos | Long | Tipo | Oblig. | Campo | Descripción |
|---|---|---|---|---|---|
| 1 | 7 | N | sí | f_numero_reg | Consecutivo |
| 8 | 4 | N | sí | f_tipo_reg | `0126` |
| 12 | 2 | N | sí | f_subtipo_reg | `00` |
| 14 | 2 | N | sí | f_version_reg | `03` |
| 16 | 3 | N | sí | f_cia | `001` |
| 19 | 1 | N | sí | **f_actualiza_reg** | Si el precio ya existe: 0 = no lo reemplaza, **1 = lo reemplaza** |
| 20 | 3 | A | sí | **f126_id_lista_precio** | Código de lista (`1`, `1M`, `10U`…), alineado a la izquierda |
| 23 | 7 | N | no* | f126_id_item | Código interno del ítem. Obligatorio si no va la referencia; en blanco si va la referencia |
| 30 | 50 | A | no* | **f126_referencia_item** | Referencia (`sku`) |
| 80 | 20 | A | no* | f126_codigo_barras | Obligatorio si no van ni ítem ni referencia |
| 100 | 20 | A | no | f126_id_ext1_detalle | Extensión 1 (si el ítem maneja precios por extensión) |
| 120 | 20 | A | no | f126_id_ext2_detalle | Extensión 2 |
| 140 | 8 | F | sí | f126_fecha_activacion | AAAAMMDD |
| 148 | 8 | F | no | f126_fecha_inactivacion | AAAAMMDD o en blanco |
| 156 | 8 | N | no | f126_id_promo_dscto | Código de promoción |
| 164 | 4 | A | sí | f126_id_unidad_medida | UM del precio (debe ser del ítem: UNID, C18…) |
| 168 | 20 | N | sí | **f126_precio** | Precio (15.4) |
| 188 | 20 | N | no | **f126_precio_minimo** | Precio mínimo (15.4) |
| 208 | 20 | N | no | **f126_precio_maximo** | Precio máximo (15.4) |
| 228 | 20 | N | no | **f126_precio_sugerido** | Precio sugerido (15.4) |
| 248 | 7 | N | no | f126_puntos_fiel | Puntos de fidelización (7 enteros) |

Diferencias entre versiones:
- **v01:** layout corto. Referencia de 20 caracteres en la pos 30, barras en la 50, extensiones de 4, fecha de activación en la 78, UM en la 102 y precio en la 106. Mínimo, máximo y sugerido siguen al precio.
- **v02:** igual a v03 sin `puntos_fiel` (termina en la 247).
- **v04:** v03 + pos 255 `f126_ind_exclusivo_oferta` (0/1).
- **v05:** v04 + pos 256 `f126_notas` (255 caracteres).

### 7.4 Qué es `000300111`

Leído desde la posición 12, que es donde termina el tipo de registro `0126`:

```
pos 12-13  00    f_subtipo_reg
pos 14-15  03    f_version_reg       → versión 03 del 0126
pos 16-18  001   f_cia               → compañía 1
pos 19     1     f_actualiza_reg     → 1 = reemplazar si el precio ya existe
pos 20     1     primer carácter de f126_id_lista_precio (3 posiciones: "1  ")
```

Es decir, `00`+`03`+`001`+`1` es un prefijo fijo, y **el último `1` es el código de lista**. Para la lista "1" (GENERAL) van además 2 espacios (pos 21–22) y 7 espacios del código de ítem (pos 23–29) antes de la referencia, que empieza en la pos 30. **Para una lista mayorista ese segmento cambia**, por ejemplo `00030011` + `1M ` o `10U`. Si el Excel de referencia siempre trae `000300111`, es porque se generó para la lista 1.

### 7.5 Los 3 campos decimales después del precio

Son **`f126_precio_minimo`, `f126_precio_maximo` y `f126_precio_sugerido`** (pos 188, 208 y 228; 20 caracteres cada uno, formato 15.4). Son opcionales; si no se usan, van en ceros (`000000000000000.0000`). Si se llenan, SIESA valida que el precio digitado en el pedido o la factura esté entre el mínimo y el máximo.

### 7.6 Registro 0127 — impuestos en valor, versión 03 (= v04; 175 caracteres)

Se envía uno por cada ítem con impuesto en valor (impoconsumo, licores) **en la misma lista, ítem, UM y fecha de activación que su 0126**.

| Pos | Long | Tipo | Oblig. | Campo | Descripción |
|---|---|---|---|---|---|
| 1 | 7 | N | sí | f_numero_reg | Consecutivo |
| 8 | 4 | N | sí | f_tipo_reg | `0127` |
| 12 | 2 | N | sí | f_subtipo_reg | `00` |
| 14 | 2 | N | sí | f_version_reg | `03` (o `04`, mismo layout) |
| 16 | 3 | N | sí | f_cia | `001` |
| 19 | 1 | N | sí | f_actualiza_reg | 0/1 |
| 20 | 3 | A | sí | f126_id_lista_precio | Código de lista |
| 23 | 7 | N | no* | f126_id_item | Ítem (o referencia o barras) |
| 30 | 50 | A | no* | f126_referencia_item | Referencia |
| 80 | 20 | A | no* | f126_codigo_barras_item | Barras |
| 100 | 20 | A | no | f126_id_ext1_detalle | Extensión 1 |
| 120 | 20 | A | no | f126_id_ext2_detalle | Extensión 2 |
| 140 | 8 | F | sí | f126_fecha_activacion | AAAAMMDD (la del precio 0126) |
| 148 | 4 | A | sí | f126_id_um | UM del precio |
| 152 | 4 | A | sí | **f127_id_llave_impuesto** | Llave **de tipo valor**: `0005` IMPOCONSUMO, `0011`/`0024` licores (§3.2) |
| 156 | 20 | N | sí | **f127_valor_imp** | Valor del impuesto (15.4) |

### 7.7 Ejemplo de archivo completo (generado según el diccionario y verificado por longitud)

Los espacios son significativos. Precio 4.650 para el sku 2537 en la lista 1, activo desde 2026-11-04, más el impoconsumo de 6.911 del sku 12014:

```
000000100000001001
00000020126000300111         2537                                                                                                          20261104                UNID000000000004650.0000000000000000000.0000000000000000000.0000000000000000000.00000000000
00000030127000300111         12014                                                                                                         20261104UNID0005000000000006911.0000
000000499990001001
```

Longitudes: 18 / 254 / 175 / 18.

En el 0127 del ejemplo, el sku 12014 necesita también su propio 0126 con la misma fecha y UM. Se omitió para que el ejemplo sea corto.

### 7.8 Cómo entra a SIESA

- Por pantalla: el importador de SIESA (Impodatos) recibe el archivo plano.
- Por webservice: `ImportarXML(pvstrDatos, printTipoError)`, con las líneas dentro de `<Datos><Linea>…</Linea></Datos>`. `printTipoError = 0` significa sin errores. Con errores, el DataSet devuelve por línea `f_nro_linea, f_tipo_reg, f_subtipo_reg, f_version, f_nivel, f_valor, f_detalle`. **No hay modo de validar sin escribir.**
- **El login del DWH (`ws_pro_DWH`, conexión `DWH_UnoEE_Real`) es solo lectura** (verificado el 2026-10-26). Si el cotizador quiere subir el plano por webservice y no solo descargarlo, necesita una conexión de importación propia pedida a SIESA, y probarla primero en el ambiente de pruebas.

---

## 8. Resumen de vistas para el cotizador

| Necesidad | Vista | Llave de cruce |
|---|---|---|
| Último precio vendido | `consumo.ventas` (SQL §1.3) | `cliente_key` (por NIT vía `consumo.clientes`), `sku` |
| Cliente, canal, lista, CO, vendedor | `consumo.clientes` | `nit` / `cliente_key`; `cod_vendedor` → `vendedores.codigo` |
| Asesor ↔ usuario de la plataforma | `consumo.vendedores.correo` (`lower()`); alternativa `codigo` | — |
| Descripción, UM, empaque, proveedor | `consumo.productos` | `sku` / `producto_key` |
| IVA e impuestos en valor | `productos.iva_venta_pct`, `consumo.producto_impuestos` (`indicador='venta'`) | `producto_key` |
| Costo actual | `consumo.inventario_corte` (último `fecha_corte`, `costo_prom_unit_ins`, por CO) | `referencia` = `sku`, `co_bodega` |
| Precio vigente por lista | `consumo.precios_lista_vigentes` (`precio_unidad_inventario`, `impoconsumo_valor`) | `lista_precio_id`, `sku` |
| Margen objetivo, apoyos | **Solo en la plataforma** | — |

## 9. Segunda ronda: reglas de la skill de listas de precios

El cotizador aplica estas reglas desde `0038_quotation_review` (ver [cotizador.md](cotizador.md)). El costo pasó de `inventario_corte` a `inventario_fecha` y a la cotización del proveedor.

Verificado el **2026-11-03** contra el DWH de producción (rol de solo lectura) y contra `UnoEE_Real` (SIESA). Leí completa `internal-platform-api\SKILL-LISTA-PRECIOS.md` antes de responder. Los tiempos son `Execution Time` de `EXPLAIN ANALYZE` en el servidor; no incluyen red ni driver.

> [!important] Resumen
> 1. **Las cotizaciones del DWH ya están limpias.** Los casos de la skill (15379 con 5.587.154, 21050 con 178.147) se corrigieron el 2026-10-28, cuando t212 pasó a extracción completa diaria. Hoy el DWH trae 2.640 y 139.115, igual que SIESA. Comparación completa: **101.803 filas, misma cantidad y misma suma de precio que SIESA hasta el último rowid extraído**. Lo que sigue saliendo raro (unas 52 filas) son **errores de captura en el propio SIESA**, por ejemplo un precio de caja cargado como UNID. Propongo un filtro contra la mediana del último costo.
> 2. **Para el costo usa `consumo.inventario_fecha`, no `inventario_corte`.** Corrijo lo que dije en la primera ronda: `inventario_corte` reconstruye el costo a partir de movimientos y hoy difiere en 3.681 de 14.457 filas, con casos en 0. `inventario_fecha` es la foto directa de SIESA (t400 + t402) de esta mañana (08:18).
> 3. **El DWH no tiene la relación lista → bodegas.** Sí tiene CO → bodegas, pero no identifica de forma única cuál es la principal y cuál la cava. Hay que fijar el mapeo en una tabla de la plataforma.
> 4. **El estado "Bloqueado" existe en SIESA** (`t121.f121_ind_estado = 2`, 11 ítems), pero `consumo.productos.estado` lo reduce a `I`.
> 5. **`precios_lista_vigentes.impoconsumo_valor` ya trae el valor Costa en las listas 004 y 11N.** La regional sale de `clientes.cod_co_factura`.
> 6. **Crear una lista nueva es el registro 0112 v01** (101 caracteres) y después sus 0126 v03. Asignarla al cliente es el registro 0201 (`f201_id_lista_precio`, pos 140), que **reescribe la sucursal completa**. Ver el riesgo en §9.5.4.

---

### 9.1 Costo de cotización (`consumo.cotizaciones_proveedores`)

#### 9.1.1 Columnas reales

`cotizacion_rowid, proveedor_key, proveedor_nit, proveedor, proveedor_sucursal_id, proveedor_sucursal, proveedor_activo, tipo_proveedor, clase_proveedor, condicion_pago, producto_key, sku, producto, producto_activo, tipo_inventario_servicio_id, tipo_inventario_servicio, grupo_impositivo_id, grupo_impositivo, marca, categoria, linea, sublinea, fecha_activacion, fecha_hasta, unidad_medida_id, moneda_id, precio_unitario, descuento_escala_pct, descuento_valor, costo_bruto, icui_tasa_pct, icui_valor, ibua_valor, ibua_valor_um, costo_impts, tiempo_entrega_dias, centro_operacion_id, convenio, es_ultima_activacion, es_vigente`

- **Grano:** una fila por cotización de SIESA (`t212.f212_rowid`). Origen: t212 (precio), t213 (IBUA), t214 (escala de descuento). Se extrae **completa cada madrugada** (hoy a las 03:11).
- `costo_bruto = precio_unitario − precio_unitario × descuento_escala_pct/100`.
- `es_vigente = es_ultima_activacion AND fecha_activacion <= now() AND coalesce(fecha_hasta,'infinity') >= now()`. `es_ultima_activacion` marca la última fecha de activación de cada proveedor × sucursal × ítem × UM.
- **`icui_tasa_pct` es un porcentaje**, igual que en la fórmula de la skill. **`ibua_valor` es por unidad base.** Para UNID/KL usa `ibua_valor`; `ibua_valor_um` es el mismo valor multiplicado por el factor de la UM, para cajas.
- **La sucursal del proveedor está en `proveedor_sucursal_id`.** No uses `centro_operacion_id`, que viene vacío.

#### 9.1.2 Lactalis

"Proveedor 417" es el **código del proveedor-criterio del ítem** (`consumo.productos.cod_proveedor = '417'`, 234 ítems). En cotizaciones, Lactalis se identifica por **`proveedor_nit = '800245795'`** (LACTALIS COLOMBIA S.A.S). Tiene 244 cotizaciones vigentes en la sucursal `001` y 25 en la `003`.

Regla en SQL: `(proveedor_nit <> '800245795' or proveedor_sucursal_id = :suc_lactalis)`, con `:suc_lactalis = '003'` para las listas de Centro (003, 10U, 10S) y `'001'` para todas las demás. Verificado con 1081 (Crema de Leche Parmalat) en la 10U: **13.104**, el valor de la sucursal 003 que menciona la skill.

#### 9.1.3 ¿El DWH trae valores malos?

**Ya no.** Lo que pasaba: hasta el 2026-10-28, t212 se extraía por secuencia de rowid, que solo ve filas nuevas. Las ediciones en el mismo registro (SIESA cambia el precio sin crear fila nueva) y los borrados no llegaban: había 56 precios viejos y 809 cotizaciones borradas que seguían saliendo. Desde ese día t212 se extrae completa todos los días.

Verificación de hoy:

| Prueba | Resultado |
|---|---|
| 15379, vigente en el DWH | **2.640** (rowid 55999), igual que SIESA |
| 21050, vigente en el DWH | **139.115** (rowid 266508), igual que SIESA |
| t212 completa, DWH vs SIESA hasta el rowid 283.732 | **101.803 = 101.803 filas; suma de `precio` 11.783.482.564,9495 en ambos lados** |
| SIESA después de la extracción | 44 cotizaciones nuevas creadas hoy después de las 03:11; entran mañana |

La instrucción de la skill ("usar el Cotizaciones.xlsx mientras TI revisa el MCP") ya se puede levantar. Que lo confirme quien la puso, con una comparación de un día.

#### 9.1.4 Filtro de cotizaciones absurdas

Lo que queda raro está **mal en SIESA**, no en el DWH. Ejemplo: 12779 Kit Kat x41.5 g tiene en t212 `UNID = 84.792` (activación 2026-04-07), cuando la caja C24 cuesta 2.035.008. Es un precio de caja cargado como unidad. El DWH lo reproduce tal cual.

Universo con el filtro de la skill (`es_vigente and producto_activo and unidad_medida_id in ('UNID','KL')`): **8.830 filas y 8.775 SKU**. Las 27.625 vigentes de la skill son sin filtrar.

Probé dos referencias:

| Referencia | Filas cot > 3× ref | Problema |
|---|---|---|
| Costo promedio de inventario (máximo entre bodegas con existencia) | 911 | **No sirve.** Bodegas como MDE23 "OBSOLETOS Y CORTA FECHA" llevan costo promedio 1 o 0,98, y la referencia se cae. |
| Máximo `ultimo_costo_unit` (> 1) entre instalaciones | 25 | Frágil: el último costo también tiene basura (15379 tiene un último costo de 4.751.173 en una instalación). |
| **Mediana de `ultimo_costo_unit` (> 1) entre instalaciones** | **52 filas / 52 SKU** (29 > 5×, 24 > 10×) | **Recomendada.** Aguanta una instalación con basura. |

Propuesta:
- **Descartar la cotización si `costo > 3 × mediana(último costo)`.** El costo es un máximo, así que una cotización inflada sube el precio; una baja no hace daño. Las 26 filas con `costo < mediana/3` no se descartan.
- **Devolver la cotización descartada en una columna aparte** (`costo_cot_descartado`) para mostrarla como alerta "Revisar cotización en SIESA". No se debe esconder.
- Si el SKU no tiene último costo (677 filas, ítems sin compras registradas), no hay referencia: la cotización pasa sin filtro y se marca "sin referencia".

#### 9.1.5 Consulta del costo para N SKU (cotización + inventario de la bodega de la lista)

```sql
-- :skus          text[]  referencias a cotizar
-- :suc_lactalis  '003' para listas Centro (003, 10U, 10S); '001' para el resto
-- :bod1, :bod2   bodegas de la lista (Bodega 2 = CONGELADOS/REFRIGERADOS); ver §9.2
with params as (
  select :skus::text[] skus, :suc_lactalis::text lactalis_suc, :bod1::text bod1, :bod2::text bod2
),
prod as (
  select p.producto_key, p.sku, p.linea, p.estado
  from consumo.productos p, params where p.sku = any(params.skus)
),
ref as (   -- mediana del último costo por instalación, referencia del filtro
  select producto_key, percentile_cont(0.5) within group (order by ultimo_costo_unit) med
  from (select distinct f.producto_key, f.instalacion, f.ultimo_costo_unit
        from consumo.inventario_fecha f join prod using (producto_key)
        where f.ultimo_costo_unit > 1) s
  group by 1
),
cot as (
  select c.producto_key,
         round(round(c.costo_bruto) * (1 + coalesce(c.icui_tasa_pct,0)/100) + coalesce(c.ibua_valor,0)) costo
  from consumo.cotizaciones_proveedores c join prod using (producto_key), params
  where c.es_vigente and c.producto_activo and c.unidad_medida_id in ('UNID','KL')
    and (c.proveedor_nit <> '800245795' or c.proveedor_sucursal_id = params.lactalis_suc)   -- Lactalis
),
cot_ok as (
  select c.producto_key,
         max(c.costo) filter (where r.med is null or c.costo <= 3 * r.med) costo_cot,
         max(c.costo) filter (where c.costo > 3 * r.med)                   costo_cot_descartado
  from cot c left join ref r using (producto_key) group by 1
),
inv as (
  select f.producto_key, max(round(f.costo_prom_unit_ins)) costo_inv, max(f.existencia) existencia
  from consumo.inventario_fecha f join prod pr using (producto_key), params
  where f.bodega = case when pr.linea in ('CONGELADOS','REFRIGERADOS') then params.bod2 else params.bod1 end
  group by 1
)
select pr.sku, pr.linea, pr.estado,
       c.costo_cot, c.costo_cot_descartado, i.costo_inv, i.existencia, r.med as ultimo_costo_mediana,
       greatest(c.costo_cot, i.costo_inv) as costo_max      -- greatest() ignora NULL
from prod pr
left join cot_ok c using (producto_key)
left join inv    i using (producto_key)
left join ref    r using (producto_key)
order by pr.sku;
```

Tiempos medidos:

| Caso | Tiempo |
|---|---|
| 8 SKU, lista 10U (BTA9/BTA8) | **106 ms** |
| 2.956 SKU (toda la 10U) | **1,41 s**, de los cuales ~1,1 s fueron armar la lista de SKU con `trim(lista_precio_id) = '10U'` |
| Armar los SKU de la 10U **sin `trim()`** | **35 ms** (usa índice) |

> [!tip] Quita el `trim()` de la skill
> `lista_precio_id` no tiene espacios en el DWH: 0 de 3.569 listas tienen relleno. `trim(lista_precio_id) = '10U'` impide usar el índice y obliga a leer las 619 mil filas de precios. Usa `lista_precio_id = '10U'`.

Resultado real (lista 10U):

| sku | costo_cot | costo_cot_descartado | costo_inv (BTA9) | existencia | mediana último costo | costo_max |
|---|---|---|---|---|---|---|
| 1081 (Lactalis, suc 003) | 13.104 | — | 13.104 | 1.068 | — | 13.104 |
| 12779 Kit Kat | — | **80.552** | 2.049 | 0 | 2.780 | 2.049 |
| 15379 | 2.640 | — | **1** | 2.585 | — | 2.640 |
| 21050 | 139.115 | — | 139.115 | 152 | — | 139.115 |
| 22858 | 43.166 | — | 36.949 | 98 | 39.274 | 43.166 |
| 7654 | 25.330 | — | 25.840 | 114 | 26.563 | 25.840 |

**Variante lista 1** (mayor entre las 17 bodegas de la skill): en `inv`, cambia el filtro por `f.bodega = any(array['MDE1','MDE3','MDE4','OCC1','OCC16','OCC14','BTA9','BTA8','BTA20','CTG1','CTG15','CTG14','BGA1','BGA11','BAQ1','BAQ2','BAQ10'])` sin el `case`. El `max(round(...))` ya da el mayor, y `sum(existencia)` da la existencia total.

> [!warning] Costos de inventario en 1 o 0
> Como muestra 15379 (costo 1 en BTA9 con 2.585 unidades), hay bodegas con costo promedio ≤ 1. La regla de la skill "costo ≤ $1 → Revisar" sigue haciendo falta. Además, en el máximo de la lista 1 conviene ignorar `costo_prom_unit_ins <= 1` (`filter (where costo_prom_unit_ins > 1)`) para que un 1 no se coma la referencia cuando es el único valor.

---

### 9.2 Inventario: cuál vista y qué bodega

#### 9.2.1 `inventario_fecha` vs `inventario_corte`

| | `consumo.inventario_fecha` | `consumo.inventario_corte` |
|---|---|---|
| Qué es | Foto **directa** de SIESA: t400 (existencia por bodega) + t402 (costo promedio por instalación) + t132 (último costo) | Saldo **reconstruido** desde movimientos (t470) por cierre de mes; el mes en curso corta hoy |
| Frescura hoy | `max(foto_at)` = **2026-11-03 08:18** | corte 2026-11-03, construido a las 03:00 |
| Costo `(ins)` | El de SIESA (t402) | Último costo promedio visto en movimientos; coincide con SIESA en ~85 % de los pares |
| Hoy, mismo SKU × bodega | — | **3.681 de 14.457 filas con costo distinto**, varias en 0 (3144 en OCC1: 0 vs 54.748) |

**La correcta es `inventario_fecha`**, como dice la skill. Mi recomendación de la primera ronda (usar `inventario_corte`) era un error. Cambien el cotizador a `inventario_fecha`. Antes de calcular, la verificación de la skill (`select max(foto_at) from consumo.inventario_fecha` = hoy) sigue siendo válida.

Columnas útiles de `inventario_fecha`: `referencia, bodega, desc_bodega, co_bodega, instalacion, existencia, cant_disponible, costo_prom_unit_ins, costo_prom_uni_bodega, ultimo_costo_unit, fecha_ultimo_costo, producto_activo, bodega_activa, fecha_ultima_venta, foto_at, producto_key, bodega_key`. Grano: SKU × bodega, todas las filas de t400 (226.852; 14.364 con existencia).

#### 9.2.2 ¿El DWH tiene CO → bodegas o lista → bodegas?

- **Lista → bodegas: no existe** en el DWH ni en SIESA. Es una convención del área (la hoja Faltantes en Lista y la columna Bodega de los archivos de Melis).
- **CO → bodegas: sí** (`consumo.bodegas.co`, `inventario_fecha.co_bodega`), pero un CO tiene muchas bodegas. Por descripción, cada CO tiene varias "PRINCIPAL" y "CAVA":

| CO | Bodegas "PRINCIPAL" / "CAVA" activas |
|---|---|
| 001 | LYSME, MDE1, MED1 (PRINCIPAL MEDELLIN); MDE3 CAVA MEDELLIN, MDE5 CAVA PREMIUM, MDE25, MDE26 |
| 002 | CALI1, OCC1 (PRINCIPAL); OCC16 CAVA OCCIDENTE |
| 003 | BDLYS, BTA9 (PRINCIPAL BOGOTA); BTA8 CAVA BOGOTA |
| 004 | CART4, CTG1 (PRINCIPAL CARTAGENA), VUPx; CTG15 CAVA CARTAGENA, CTG17 |
| 005 | BGA1 |
| 009 | BAQ1; BAQ2 CAVA, BAQ15 |

No se puede derivar la bodega de forma automática y confiable. **Hay que fijar el mapeo en una tabla de la plataforma** (`lista_precio_id → bodega_1, bodega_2`), cargada desde la skill:

| Lista(s) | Bodega 1 | Bodega 2 (CONGELADOS/REFRIGERADOS) |
|---|---|---|
| 001, 1M, 10X | MDE1 | MDE3 |
| 002, 10T, 10R | OCC1 | OCC16 |
| 003, 10U, 10S | BTA9 | BTA8 |
| 004, 11N | CTG1 | CTG15 |
| 005, 10N | BGA1 | — (BGA1) |
| 009, 1DR | BAQ1 | BAQ2 |
| 35P | URA6 | — |
| 1 | las 17 de la skill (máximo) | |

La columna `linea` de `consumo.productos` trae exactamente `CONGELADOS` (339 ítems activos) y `REFRIGERADOS` (191) para la regla de Bodega 2.

#### 9.2.3 Lista fuera de la skill (31A, 62Y, …)

**Recomiendo usar la Bodega 1/2 del CO que factura al cliente** (`clientes.cod_co_factura`), con el mismo mapeo por CO de la tabla anterior (001 → MDE1/MDE3, 002 → OCC1/OCC16, 003 → BTA9/BTA8, 004 → CTG1/CTG15, 005 → BGA1, 009 → BAQ1/BAQ2). Es lo que más se parece a cómo SIESA costea la venta: el costo de la factura es el promedio de la **instalación** desde donde se despacha. Hay dos casos sin regla, y el área tiene que decidirlos:
- **DECISIÓN PENDIENTE:** clientes con CO 006 Yumbo, 007 Urabá, 008 Valledupar, 010 Quindío, 100, 200 y 201. No están en el mapeo, y la skill excluye expresamente APO1/APO8/VUP1/VUP9 de la lista 1.
- 3.817 sucursales activas no tienen `cod_co_factura`: usar el máximo de la lista 1 y marcar la alerta.

---

### 9.3 Estado del ítem

- `consumo.productos.estado` → **`A` / `I`** (9.497 A, 18.346 I). Sale de `t121.f121_ind_estado` (estado de la extensión, no de t120). Es el mismo valor que `producto_activo` en `inventario_fecha` y `cotizaciones_proveedores`.
- **SIESA tiene tres estados: 0 = inactivo, 1 = activo, 2 = bloqueado.** Hoy: 18.330 / 9.504 / 11. **El DWH reduce el bloqueado a `I`.** El dato está en `staging.stg_siesa__t121_mc_items_extensiones.ind_estado`, que la plataforma puede leer si entra como `adm_plataforma`, extraído hoy a las 07:31. Los 11 bloqueados de hoy: 9582, 9959, 1415, 1886, 6129, 12534, 15378, 18037, 22159, 22248, 27568.
- **¿Coincide con la columna Estado del Maestro Margen?** Debería, porque ambos leen el estado del ítem en SIESA, pero no puedo validarlo: el Maestro Margen es un Excel que no está en el DWH. El desfase que se vio hasta el 2026-10-28 (t121 no movía su `ts` al cambiar de estado) se corrigió al extraer t121 completo. Hoy el DWH tiene 9.497 activos y SIESA 9.504; la diferencia corresponde a cambios hechos después de la extracción de las 07:31.
- Si necesitan `Bloqueado` en el cotizador sin leer staging, la propuesta es agregar a `consumo.productos` una columna `estado_siesa` (`Activo`/`Inactivo`/`Bloqueado`). Es un cambio de dbt en `dwh-platform` y hay que pedirlo.

```sql
-- Mientras tanto (solo adm_plataforma): estado de 3 valores
select p.sku,
       case x.ind_estado when 1 then 'Activo' when 2 then 'Bloqueado' else 'Inactivo' end as estado_siesa
from consumo.productos p
join staging.stg_siesa__t120_mc_items i            on i.id = p.item_id and i.id_cia = 1
join staging.stg_siesa__t121_mc_items_extensiones x on x.rowid_item = i.rowid and x.id_cia = i.id_cia
where p.sku = any(:skus);
```

---

### 9.4 Regional e impuesto de licores (Nacional vs Costa)

- **Regional de facturación del cliente:** `consumo.clientes.cod_co_factura` / `co_factura`. Valores activos: 001 ANTIOQUIA (26.526), 003 CENTRO (14.458), 002 OCCIDENTE (5.692), **004 COSTA (3.122)**, 005 ORIENTE (2.778), 009 BARRANQUILLA (2.057), 200 JDH MARKET APARTADO, 006 YUMBO, 008 VALLEDUPAR, 010 QUINDIO, 201 JDH MARKET VALLEDUPAR, 007 URABA, 100 ACO URABA, y 3.817 sin valor.
- **`precios_lista_vigentes.impoconsumo_valor` ya trae el valor Costa en las listas de la Costa.** Ejemplos reales:

| sku | 1 / 001 / 009 / 1M / 1DR / 10X | 004 / 11N |
|---|---|---|
| 21700 Juniper Moscow Mule | 48.432 | **49.728** |
| 21701 Juniper Gin Tonic | 60.828 | **62.256** |
| 26275 Ron Blanco Carbón | 38.555 | **39.832** |
| 18973 Juniper Moscow Mule (unidad) | 4.036 | **4.144** (004) |
| 12013 / 12014 Vino Cavic, 26278 / 26279 Selva Gin | igual en todas | igual |

  Eso confirma la regla de la skill: 004 y 11N llevan Costa, y 009 y 1DR (Barranquilla) llevan Nacional.

**Regla recomendada para el cotizador:**
1. Si la lista del cliente ya tiene precio del SKU, toma su `impoconsumo_valor`. Es lo que SIESA tiene cargado.
2. Para una lista nueva o un SKU sin precio en la lista: Costa si `cod_co_factura = '004'` (copiar el `impoconsumo_valor` de la lista **004**); si no, Nacional (copiar el de la lista **1**).
3. **DECISIÓN PENDIENTE:** 008 Valledupar y 201 Market Valledupar están en la Costa geográficamente, pero la skill no los menciona. Que el área confirme.
4. La llave para el 0127 sale de `consumo.producto_impuestos` (`indicador='venta' and tipo_calculo='valor'`): `0005` IMPOCONSUMO, `0011` licores importados IVA 5 %, `0024` licores nacionales IVA 5 %. Usa la del SKU; no la fijes por tipo de licor.

---

### 9.5 Crear una lista en SIESA y asignarla al cliente

#### 9.5.1 Cabecera de la lista: registro **0112 v01** ("Lista de precio")

Es la única versión que hay en t661. Layout según `t662_imp_campos` (sin las columnas internas de trabajo, por ejemplo `f112_ind_existe`):

| Pos | Long | Tipo | Oblig. | Campo | Valor / nota |
|---|---|---|---|---|---|
| 1 | 7 | N | sí | f_numero_reg | Consecutivo |
| 8 | 4 | N | sí | f_tipo_reg | `0112` |
| 12 | 2 | N | sí | f_subtipo_reg | `00` |
| 14 | 2 | N | sí | f_version_reg | `01` |
| 16 | 3 | N | sí | f_cia | `001` |
| 19 | 1 | N | sí | f_actualiza_reg | `0` para crear (si ya existe, no la toca); `1` reemplaza la cabecera |
| 20 | 3 | A | sí | **f112_id** | Código nuevo, 3 caracteres |
| 23 | 40 | A | sí | **f112_descripcion** | Descripción |
| 63 | 3 | A | sí | f112_id_moneda | `COP` |
| 66 | 1 | N | sí | f112_ind_tipo_lista | `1`, como todas las listas reales (1, 1M, 10X, 31A, 62Y) |
| 67–68 | 1+1 | N | sí | f112_ind_impuesto, f112_ind_precio_sugerido | `0` y `0`, como las reales (precios **sin** impuesto, sin precio sugerido). El diccionario pone los dos en la pos 68 y deja la 67 vacía, con toda probabilidad un error del diccionario. Con los dos en `0` el resultado es el mismo, pero confírmenlo en pruebas |
| 69 | 3 | A | no | f112_id_lista_base | En blanco (lista independiente) |
| 72 | 20 | N | no | f112_factor_base | Ceros |
| 92 | 9 | N | no | f112_precision_base | Ceros |
| 101 | 1 | N | no | f112_ind_aplica_item_base | `0` |

**Después de la cabecera bastan los 0126 v03**, uno por SKU, con la lista nueva en las posiciones 20–22, más los 0127 de los licores. Todo puede ir en el mismo archivo, con la 0112 antes que los 0126 (el importador procesa por `f661_orden_proceso`).

**Código de la lista nueva:** son 3 caracteres alfanuméricos. Hoy hay 3.569 listas en la compañía 1, casi todas de la forma dígitos + letra (`62Y`, `10X`). Verifiquen que el código esté libre en SIESA antes de generarlo; `consumo.listas_precios` refleja SIESA del día anterior. Ejemplo: el patrón `C` + 2 dígitos (`C01`…`C99`) está **sin usar** (0 listas hoy) y serviría para identificar las listas creadas desde el cotizador. **DECISIÓN PENDIENTE** del área.

#### 9.5.2 Ejemplo de archivo (generado según el diccionario y verificado por longitud)

Lista nueva `C01` para un cliente; SKU 22858 a 53.958 desde el 2026-11-04:

```
000000100000001001
0000002011200010010C01COTIZADOR DISTRI CARPO FOODS            COP100   000000000000000.00000000.00000
0000003012600030011C01       22858                                                                                                         20261104                UNID000000000053958.0000000000000000000.0000000000000000000.0000000000000000000.00000000000
000000499990001001
```

Longitudes: 18 / **101** / 254 / 18.

#### 9.5.3 Asignar la lista al cliente: registro **0201** (clientes)

- El campo es **`f201_id_lista_precio`, pos 140, 3 caracteres**. Está en la misma posición desde la v02 hasta la v16. La lista se asigna **por sucursal**, la misma granularidad que `consumo.clientes.lista_precio_id`.
- La versión que ya usa otro integrador (BEX) por el webservice es la **v04 (757 caracteres)**. Campos obligatorios de la v04: `f201_id_tercero` (20, 15), `f201_id_sucursal` (35, 3), `f201_ind_estado_activo` (38), `f201_descripcion_sucursal` (39, 40), `f201_id_moneda` (79, 3), `f201_ind_calificacion` (86), `f201_id_tipo_cli` (132, 4), `f201_fecha_ingreso` (745, 8), más `f_actualiza_reg = 1`.

#### 9.5.4 Riesgos antes de automatizar

> [!danger] El 0201 no es un "update de un campo"
> Con `f_actualiza_reg = 1`, el 0201 **reemplaza la sucursal del cliente con lo que trae la línea**: vendedor (82), condición de pago (87), cupo (93), CO de factura (169), bloqueos, contacto y dirección (427–744). No hay forma de mandar solo la lista. Si los demás campos van en blanco, es probable que borren datos del cliente. Ese comportamiento **no está verificado**, y `ImportarXML` no tiene modo de validar sin escribir.
> Opciones, de más a menos segura:
> 1. **Asignar la lista a mano en SIESA** (un cliente a la vez; el cotizador solo crea la lista y sus precios).
> 2. Armar el 0201 completo **desde los datos actuales de la sucursal** (`consumo.clientes` trae la mayoría: vendedor, condición de pago, CO de factura, contacto, dirección) y probarlo primero en el ambiente de pruebas de SIESA.

> [!warning] Canal de carga
> El login del DWH en el webservice (`ws_pro_DWH`) es **solo lectura**. Si la plataforma va a subir el plano por `ImportarXML` y no solo generar el .txt para que Melis lo importe, necesita una conexión de importación propia pedida a SIESA.

---

### 9.6 Resumen de cambios para el cotizador

| Tema | Antes (ronda 1 / skill) | Ahora |
|---|---|---|
| Inventario | `inventario_corte` | **`inventario_fecha`** (foto directa, 08:18) |
| Cotizaciones | La skill dice "MCP malo, usar el Excel" | DWH = SIESA (verificado completo); filtro `costo > 3 × mediana(último costo)` → `costo_cot_descartado` |
| Lactalis | "Proveedor 417" | `proveedor_nit = '800245795'` y `proveedor_sucursal_id` |
| Filtro de lista | `trim(lista_precio_id) = …` | `lista_precio_id = …` (35 ms en vez de ~1 s) |
| Lista → bodegas | — | Tabla propia en la plataforma (§9.2.2); fuera de la skill, el CO que factura |
| Estado | A/I | A/I en consumo; Bloqueado solo en staging t121 (pedir `estado_siesa`) |
| Impoconsumo | Maestro Margen Nacional/Costa | `precios_lista_vigentes.impoconsumo_valor` de la lista (004/11N ya traen Costa) |
| Lista nueva | — | 0112 v01 + 0126 v03 (+ 0127); asignación por 0201 con riesgo (§9.5.4) |
