# Liquidación de apoyos

Cuánto debe reconocer cada proveedor por las ventas de referencias apoyadas en un mes, y el
margen de esas ventas con ese dinero de vuelta (margen recompuesto). Reemplaza la liquidación que
BI hacía a mano en Excel (2026-11-24).

- **Módulo:** `app/modules/support_settlements`. Prefijo `/api/v1/support-settlements`.
- **Permiso:** `liquidacion-apoyos`, bloqueado en BI.
  - `read` consulta y descarga.
  - `manage` recalcula y cierra.
  - Reabrir un mes cerrado solo lo puede el líder o responsable de BI, o un administrador
    (`AreaLeaderPolicy`).
- **Periodo:** mes completo, `AAAA-MM`.
  - Se cierra el mes vencido: en noviembre, octubre.
  - El mes en curso se puede calcular como vista previa.
  - Empieza en `SUPPORT_SETTLEMENTS_START_PERIOD` (`2026-10`). Los apoyos de antes no están
    cargados en la plataforma.
- **Dos costos** (`cost_basis`, 2026-11-24): cada mes se liquida con los dos.
  - `cotizacion`: la cotización del proveedor.
  - `inventario`: el costo de inventario facturado.
  - Una sola lectura del DWH da los dos. Cada uno se guarda, se consulta, se descarga, se cierra
    y se reabre por separado.

## Reglas

Validadas contra las liquidaciones de BI de septiembre 2026 de Familia Sancela y Team Foods.

| Tema | Regla |
| --- | --- |
| Ventas | `consumo.ventas` del mes, línea por línea: cada ítem vendido de una referencia apoyada. Las devoluciones **restan**: entran con cantidad y valores negativos y descuentan apoyo; no basta con excluirlas. Sin `NC530535`. Las líneas sin venta (obsequios, consumo interno, servicios) no se liquidan, pero cuentan en las ventas de la referencia |
| Apoyos | Táctico cliente y actividad temporal, financiados por el proveedor, no anulados, activados y vigentes el día de la venta. La excepción de margen JDH no se cobra |
| Alcance | La sucursal vendida: cliente, grupo, sucursal, regional, canal, unidad de negocio, bodega y bodegas promocionales (`scope_of`). No la unión de las sucursales del cliente |
| Costo base `cotizacion` | La cotización SIESA del proveedor vigente el día de la venta, en costo bruto por unidad o kilo. Se toma la del C.O. de la venta; si no hay, la que no tiene C.O.; si no, la mayor. Sin cotización se usa el costo de la factura y la línea queda marcada |
| Costo base `inventario` | El costo de inventario facturado en la línea: `consumo.ventas.costo ÷ cantidad` |
| Precio lista | El costo elegido al **margen Food Service** de la referencia (`margin_list`): `costo / (1 − margen)`, sea cual sea el canal del cliente |
| $ por unidad | Siempre con el costo elegido. % sobre costo; % sobre precio lista; $ por unidad, caja (÷ factor) o kilo (× kilos vendidos); $ costo apoyado (`costo − costo apoyado`, nunca negativo). % margen garantizado: `costo − costo apoyado`, donde el costo apoyado es el precio vendido al margen garantizado, `precio × (1 − margen)` |
| Descuento real | Solo en los tipos en %: si el cliente quedó facturado por encima del precio negociado, el % del proveedor baja en los puntos de descuento que no recibió. `% real = % − max(0, dcto negociado − dcto otorgado)`, con `dcto otorgado = 1 − precio facturado / precio lista` |
| Jerarquía (2026-11-24) | Entre los apoyos que cubren la venta, el nivel de cliente más específico reemplaza a los otros: el de la sucursal de la venta (NIT-sucursal) deja fuera al del NIT y al del grupo; sin él, el del NIT deja fuera al del grupo. Los apoyos sin cliente no entran en la jerarquía |
| Varios apoyos | Las copias del mismo apoyo (clase, tipo, valor, unidad y referencia: copias de grupo, grupo y NIT) cuentan una vez. Los apoyos distintos se suman y la línea queda marcada. La copia de una solicitud de margen proveedor reemplaza a su apoyo base |
| Cantidad mínima | Se mide con lo que el cliente compró de esa referencia en el mes, devoluciones incluidas |
| No se cobra | Apoyo en revisión, con escalas, ya descontado en la factura del proveedor (`pie_factura`, `costo_neto_facturado`) o sin dato para calcularlo. Se lista aparte (`flagged_amount`, hoja «No liquidados») |
| Margen recompuesto | `(venta − costo + apoyo) / venta`, por línea, referencia y proveedor |

Los montos se guardan en centavos. El % liquidado se guarda con 6 decimales.

## Cálculo y cierre

- **Recalcular:** lee las ventas del mes del DWH en una sola consulta (minutos en frío, segundos
  en caliente) y reemplaza el borrador del mes en una transacción.
  - Si el cliente o el proxy cortan la petición, el cálculo sigue hasta guardarse.
  - Volver a pedir el mes muestra el resultado. Lo mismo vale para cerrar.
- **Tarea diaria:** `support-settlements-refresh`, después de la carga de ventas
  (`reporte_ventas`), recalcula el mes en curso y el vencido si no está cerrado.
- **Cerrar:** recalcula por última vez y congela la foto. Las líneas guardan el apoyo como era en
  ese momento (id, versión, tipo, valor, vigencia). Un mes cerrado no cambia aunque después
  editen los apoyos o lleguen ventas.
- **Reabrir:** devuelve el mes a borrador.

## Tablas (migraciones `0085_support_settlements` y `0086_settlement_cost_basis`)

- `support_settlements`: un mes con una base de costo (`cost_basis`), su estado y sus totales.
  Única por `(period, cost_basis)`.
- `support_settlement_refs`: cada referencia apoyada, con todas sus ventas del mes y lo que pagan
  sus apoyos.
- `support_settlement_sales`: las líneas que cubrió un apoyo, con regional, unidad de negocio,
  canal, ciudad, vendedor y cliente (el «reintegro» por dimensión).
- `support_settlement_items`: cada apoyo aplicado a una línea.

## Endpoints

Todos menos `/permissions` aceptan `cost_basis=cotizacion|inventario`; por defecto
`cotizacion`.

| Método y ruta | Uso |
| --- | --- |
| `GET /permissions` | `can_read`, `can_manage`, `can_reopen`. Abierto: responde `false`, no 403 |
| `GET /periods` | Meses guardados, más el actual y el vencido aunque no estén calculados. `settlement` es la liquidación de la base pedida; `settlements` trae las dos que haya |
| `GET /{periodo}` | Totales del mes y una fila por proveedor (venta, costo, margen, apoyo, margen recompuesto, no liquidado) |
| `GET /{periodo}/references?supplier_code=` | Referencias apoyadas (de un proveedor) |
| `GET /{periodo}/lines?supplier_code=&sku=&nit=&status=&page=&size=` | Líneas con cada apoyo aplicado; `status` filtra por estado del apoyo |
| `GET /{periodo}/breakdown?by=regional\|business_unit\|salesperson\|customer&supplier_code=` | Dónde vuelve el dinero del proveedor |
| `GET /{periodo}/export?supplier_code=` | Excel: Resumen (por referencia), Detalle ventas y No liquidados |
| `POST /{periodo}/recalculate` | Recalcula las dos bases que no estén cerradas, con una sola lectura del DWH, y devuelve la pedida. 409 si la pedida está cerrada |
| `supplier_code=__none__` | En `references`, `lines`, `breakdown` y `export`: las filas sin código de proveedor |
| `POST /{periodo}/close` | Solo un mes terminado. Cierra solo la base pedida |
| `POST /{periodo}/reopen` | Líder de BI o administrador. Reabre solo la base pedida |

## Validación de octubre 2026 (Familia Sancela, contra la liquidación cerrada de BI)

| Base | BI | Módulo | Diferencia |
| --- | --- | --- | --- |
| Cotización | 292.950.233,90 | 292.728.947,89 | 221.286 (0,08 %) |
| Inventario (BI rehecho con su «Costo Unt») | 293.199.165,48 | 292.468.680,86 | 730.485 (0,25 %) |

- **Devoluciones:** restan en los dos. BI −9.498.550; módulo −9.484.059. La diferencia es la
  NDE-53400, que no está en el DWH.
- **Inventario:** 3.741 llaves iguales al peso.
- **Cotización:** 14.812 llaves iguales y 358 con diferencias de pesos (16.217 en total, menos de
  $1.000 por llave). BI usa el costo del maestro de precios de Familia (por ejemplo 53.308 / 24)
  y el módulo la cotización de SIESA (53.304 / 24 = 2.221).
- **Lo que no cuadra es de datos, no de reglas:**
  - **1128, NIT 901969003:** la sábana de BI tiene dos apoyos para la llave (costo apoyado
    54.805 y 60.622) y su búsqueda toma el primero. En la plataforma está el de 60.622
    (290.865).
  - **13614, Milsen:** BI divide la caja por un factor de 36; SIESA tiene 24 (cotización C24 de
    98.544 = 4.106 por unidad).
  - **6283, NIT 900554515:** el apoyo (vigente hasta el 31-oct) no está cargado en la plataforma
    (36.873).
  - **19161, NIT 7533497:** la plataforma tiene un apoyo desde el 1-oct que BI no tiene (40.388).
  - **22940, FEV-36246:** venta a $1 desde una bodega promocional. El apoyo no incluye las
    bodegas promocionales, así que no aplica allí (17.393).
  - **NDE-53400:** devolución que no está en las ventas del DWH de octubre (−13.757).

## Validación (piloto de septiembre 2026)

| Proveedor | BI | Módulo | Iguales o a menos del 2 % | Falta el apoyo en la plataforma |
| --- | --- | --- | --- | --- |
| Familia Sancela | 295.560.808 | 272.923.800 | 15.581 llaves | 113 llaves, 20,2 M (versiones vencidas el 30-sep, no cargadas tras el borrado del 18-nov) |
| Team Foods | 90.363.566 | 77.618.691 | 8.506 llaves | 261 llaves, 12,2 M |

- **Costo de la factura contra la cotización:** la cotización da exactamente el costo de lista que
  usa BI (Familia 18369: 9.142,67).
- **Team Foods:** BI usa a veces un costo distinto por regional de costo (183.524 contra 189.944
  en la 9951). Por eso hay 0,6 M de diferencia en 49,8 M.

## Pendiente

- Conciliación contra la nota crédito recibida.
- Negociaciones especiales tipo «Lima».
- Rebates y presupuestos.
- Publicar la liquidación al DWH.
