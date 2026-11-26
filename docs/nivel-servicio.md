# Nivel de servicio (Abastecimiento)

El tablero de nivel de servicio de Power BI pasa a la plataforma. La base son las líneas de los
pedidos de venta que SIESA cerró como **Cumplido**. Una línea queda **pendiente** cuando se pidió
algo que no se despachó.

El módulo tiene dos vistas en el área `abastecimiento`:

| Clave | Vista | `read` | `manage` |
| --- | --- | --- | --- |
| `pendientes-pedidos` | Pendientes de pedidos | detalle de líneas con KPIs, opciones, tablero, desgloses, Pareto, ABCD y motivos | asignar motivos (uno o en bloque), CRUD de motivos, recalcular la ABCD |
| `nivel-servicio` | Nivel de servicio | tablero, desgloses, Pareto, ABCD y motivos; no ve el detalle de líneas | — |

- Las dos claves abren también los buscadores de `/commercial/*`: clientes y proveedores para los
  filtros.
- `GET /service-level/permissions` es abierto. Responde `modules[clave] = {can_read, can_manage}`
  y `can_manage`, que indica si el usuario gestiona los pendientes.
- Después de desplegar, un administrador concede `read` o `manage` rol por rol en
  `/ti/administracion/permisos`. El líder del área ya lo tiene por `*`.

## De dónde salen los datos

- **Origen:** `consumo.pedidos_venta_lineas` (DWH PR #164). Trae una fila por línea t431 de pedidos
  desde 2024, construida por la secuencia `cartera_pedidos` (lun-sáb 11:50). La API lee solo
  `estado_codigo = 4` (Cumplido).
  - Las cantidades van en unidad base.
  - `cant_pendiente` = pedida − remisionada.
  - `valor_subtotal` es bruto − descuentos, sin impuestos.
  - `valor_pendiente` es la parte del subtotal que no se despachó.
  - Un pedido cumplido conserva las líneas que cerró sin despachar (estado de línea 0 a 3, el 2,8 %
    de noviembre de 2026). Cuentan, porque son lo que no se sirvió.
- **Job `service-level-sync`:** corre después de `cartera_pedidos`.
  - La primera vez copia desde `SERVICE_LEVEL_START_DATE` (2026-09-01), mes por mes.
  - Después relee `SERVICE_LEVEL_SYNC_DAYS` (45) días, la misma ventana que relee el DWH.
  - **Lo que no vuelve a llegar:**
    - una línea que ya no llega se borra;
    - si tiene motivo, se marca anulada y sale de todas las cifras;
    - una lectura vacía no toca nada.
  - Mientras la vista no exista, el job no hace nada y lo informa en `skipped`.
- **Tablas de la plataforma:**
  - `service_level_lines`: la copia de las líneas.
  - `service_level_line_reasons`: el motivo de cada línea, aparte para que el sync no lo pise.
  - `service_level_reasons`: el catálogo, sembrado con los 46 motivos de `Motivos.xlsx`.
  - `service_level_classes` y `service_level_class_runs`: la ABCD y cada cálculo.

## KPIs

Todos los KPIs se calculan sobre las líneas del filtro, por fecha del pedido. Se devuelven como
fracción: `0.995` es 99,5 %, y `null` cuando no se pidió nada.

| KPI | Cálculo |
| --- | --- |
| NS Pedidos | 1 − pedidos con alguna línea pendiente / pedidos |
| NS Líneas | 1 − líneas pendientes / líneas |
| NS Cantidad | 1 − cantidad pendiente / cantidad pedida |
| NS Valor | 1 − valor pendiente / valor pedido |
| NS Total | (NS Valor + NS Cantidad + NS Líneas) × 30 % + NS Pedidos × 10 % |

`value_served` (valor facturado) es el valor pedido menos el pendiente. El cálculo cuadra con las
dos capturas del tablero (99,7 % y 93,2 %), como se ve en `tests/modules/service_level/test_rules.py`.

**Colores:** verde por encima de 96 % y ámbar desde 90 %; por debajo, rojo
(`SERVICE_LEVEL_BANDS`). Las respuestas traen `bands`.

## Clasificación ABCD

- **Qué se ordena:** el valor solicitado de los `SERVICE_LEVEL_ABCD_MONTHS` (2) meses cerrados
  anteriores. Los productos se ordenan dentro de cada C.O. y los clientes por NIT.
- **Cómo se asigna la clase:** de mayor a menor, con el acumulado. A ≤ 80 %, B ≤ 95 %, C ≤ 99 %
  y D por encima (`SERVICE_LEVEL_ABCD_CUTS`).
- **Qué se guarda:** solo el resultado; cada cálculo reemplaza la tabla completa.
- **Cuándo se recalcula:** el job lo hace al cambiar de mes. `POST /classes/recompute` lo hace a
  demanda.
- Un ítem que nunca se pidió en la ventana queda sin clase (filtro `none`).

## Endpoints (`/api/v1/service-level`)

### Filtros

Todos los endpoints de lectura toman los mismos filtros.

| Parámetro | Qué filtra |
| --- | --- |
| `since`, `until` | Fecha del pedido. Por defecto el mes actual; máximo `SERVICE_LEVEL_MAX_RANGE_DAYS`. |
| `co`, `warehouse`, `supplier` | Código del C.O., de la bodega o del proveedor comercial. |
| `seller` | `vendedor_key` (de `/options`). |
| `customer` | NIT. |
| `q` | Referencia, descripción o documento. |
| `reason` | Id del motivo, o `none` para las líneas sin motivo. |
| `responsible` | Responsable del motivo. |
| `product_class`, `customer_class` | `A`, `B`, `C`, `D` o `none`. |
| `pending` | `true`: solo las líneas con pendiente. `false`: solo las servidas. |

### Lista

| Método y ruta | Clave | Qué hace |
| --- | --- | --- |
| `GET /permissions` | abierta | Qué puede hacer el usuario en cada vista. |
| `GET /options` | cualquiera | C.O., bodegas, proveedores y vendedores del periodo, más los responsables. |
| `GET /lines` | `pendientes-pedidos` | Detalle de líneas con los KPIs de todo el filtro. |
| `PUT /lines/{line_key}/reason` | manage | Asigna o quita el motivo de una línea. Cuerpo `{reason_id \| null, note}`. |
| `PUT /lines/reason` | manage | Asignación en bloque. Cuerpo `{line_keys[≤500], reason_id, note}`. |
| `GET /dashboard` | cualquiera | KPIs, KPIs por C.O., serie diaria, pendiente por responsable y última ABCD. |
| `GET /dashboard/breakdown` | cualquiera | Desglose por la dimensión `dimension`. |
| `GET /pareto` | cualquiera | Curva de Pareto: `kind=producto\|cliente`, `co` opcional. |
| `GET /classes` | cualquiera | Ventana de la próxima ABCD, cortes y último cálculo. |
| `POST /classes/recompute` | manage | Recalcula la ABCD ahora. |
| `GET /reasons` | cualquiera | Catálogo de motivos (`include_inactive`). |
| `GET /reasons/responsibles` | cualquiera | Responsables de los motivos activos. |
| `POST /reasons`, `PATCH /reasons/{id}` | manage | Crea, renombra, cambia el responsable o desactiva un motivo. Los motivos no se borran. |

### Detalle

**`GET /lines`**
- Paginación y orden: `sort` (`date`, `document`, `co`, `sku`, `supplier`, `customer`,
  `seller`, `qty_pending`, `value_pending` o `value`), `order`, `page` y `size` (hasta 200).
- Cada línea trae:
  - C.O., fecha del pedido y `updated_at` (la última modificación en SIESA);
  - documento, bodega, proveedor, referencia y descripción;
  - cantidades pedida, remisionada y pendiente, subtotal y valor pendiente;
  - NIT, razón social y vendedor;
  - clase del cliente y de la referencia;
  - motivo con su responsable y la nota.
- Respuesta: `{items, total, page, size, since, until, kpis, bands, as_of}`. `as_of` es la última
  copia del job.

**Asignar motivos**
- Solo las líneas con pendiente llevan motivo. Una línea servida responde 422.
- Un motivo inactivo no se puede asignar.

**`GET /dashboard`**
- Responde `kpis`, `by_co[{co, name, kpis}]`, `daily[{day, kpis}]` (la línea del NS día a día),
  `by_responsible` (la dona del valor pendiente), `as_of` y `classes`.

**`GET /dashboard/breakdown`**
- `dimension` puede ser `co`, `warehouse`, `supplier`, `seller`, `customer`, `responsible`,
  `reason`, `product`, `reason_item`, `product_class` o `customer_class`.
  - `responsible`, `reason`, `product` y `reason_item` solo toman líneas con pendiente.
  - `reason_item` trae el producto en `detail_key` y `detail_name`.
- Cada fila trae valor solicitado, facturado y pendiente, `ns_value`, `pending_pct`, cantidades,
  líneas y pedidos.
- Orden: `sort` (`value`, `value_pending`, `qty_pending`, `ns_value`, `name` o `key`), con
  paginación.

**`GET /pareto`**
- Devuelve unos 100 puntos `{items, value}`: % de ítems contra % del valor, desde (0, 0) hasta
  (1, 1).

## Puntos por confirmar con el área

- **Conteo de pedidos.** El DWH tiene 30.984 pedidos cumplidos al 24 de noviembre y el tablero
  32.496. Las líneas y el valor cuadran a menos de 0,1 %. No se sabe qué cuenta el tablero
  (¿pedidos sin líneas?).
- **Bandas de color.** Se dedujeron de las capturas y se pueden configurar.
- **Pendiente frente a lo remisionado.** El pendiente se mide contra lo remisionado (lo facturado
  nunca lo supera). `cant_facturada` existe en la vista por si el área lo pide contra factura.
- **Obsequios.** Las líneas de obsequio (`es_obsequio` en la vista) entran en líneas y cantidad,
  pero tienen valor 0.
