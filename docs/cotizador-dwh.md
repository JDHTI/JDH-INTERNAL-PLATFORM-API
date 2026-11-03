# DWH para el módulo COTIZADOR — respuesta de la sesión DWH

Fecha de verificación: **2026-11-03**, contra el PostgreSQL del DWH (base `dwh`) con el rol `mcp_readonly`, y contra `UnoEE_Real` (SIESA) con el MCP de SQL Server. Todo lo que sigue sale de consultas a la base, no de documentación, salvo donde se indica. Los NIT, nombres y correos de los ejemplos están anonimizados.

> [!important] Resumen en 6 líneas
> - Histórico de venta: **`consumo.ventas`** (una fila por línea de factura o devolución). No trae IVA ni la lista de precios aplicada; el precio unitario se calcula.
> - Clientes: **`consumo.clientes`** (una fila por sucursal). Mayorista = `cod_canal = '0150'` ("MAYORISTAS Y DISTRIBUIDORES"). Para el precio conviene usar la `lista_precio_id` del cliente y no el canal (ver §2.3).
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

### 3.3 Costo actual: `consumo.inventario_corte`

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
