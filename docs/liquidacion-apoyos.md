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

## Reglas

Validadas contra las liquidaciones de BI de septiembre 2026 de Familia Sancela y Team Foods.

| Tema | Regla |
| --- | --- |
| Ventas | `consumo.ventas` del mes. Las devoluciones restan (vienen negativas). Sin `NC530535`. Las líneas sin venta (obsequios, consumo interno, servicios) no se liquidan, pero cuentan en las ventas de la referencia |
| Apoyos | Táctico cliente y actividad temporal, financiados por el proveedor, no anulados, activados y vigentes el día de la venta. La excepción de margen JDH no se cobra |
| Alcance | La sucursal vendida: cliente, grupo, sucursal, regional, canal, unidad de negocio, bodega y bodegas promocionales (`scope_of`). No la unión de las sucursales del cliente |
| Costo base | La cotización SIESA del proveedor vigente el día de la venta (costo bruto por unidad o kilo; primero la del C.O. de la venta, luego la sin C.O., luego la mayor). Sin cotización, el costo de la factura, y la línea queda marcada |
| $ por unidad | % sobre costo; % sobre precio lista (`costo / (1 − margen óptimo de la lista del cliente)`); $ por unidad, caja (÷ factor) o kilo (× kilos vendidos); $ costo apoyado (`costo − costo apoyado`, nunca negativo); % margen garantizado |
| Descuento real | Solo en los tipos en %: si el cliente quedó facturado por encima del precio negociado, el % del proveedor baja en los puntos de descuento que no recibió. `% real = % − max(0, dcto negociado − dcto otorgado)`, con `dcto otorgado = 1 − precio facturado / precio lista` |
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

## Tablas (migración `0085_support_settlements`)

- `support_settlements`: un mes, su estado y totales.
- `support_settlement_refs`: cada referencia apoyada, con todas sus ventas del mes y lo que pagan
  sus apoyos.
- `support_settlement_sales`: las líneas que cubrió un apoyo, con regional, unidad de negocio,
  canal, ciudad, vendedor y cliente (el «reintegro» por dimensión).
- `support_settlement_items`: cada apoyo aplicado a una línea.

## Endpoints

| Método y ruta | Uso |
| --- | --- |
| `GET /permissions` | `can_read`, `can_manage`, `can_reopen`. Abierto: responde `false`, no 403 |
| `GET /periods` | Meses guardados, más el actual y el vencido aunque no estén calculados |
| `GET /{periodo}` | Totales del mes y una fila por proveedor (venta, costo, margen, apoyo, margen recompuesto, no liquidado) |
| `GET /{periodo}/references?supplier_code=` | Referencias apoyadas (de un proveedor) |
| `GET /{periodo}/lines?supplier_code=&sku=&nit=&status=&page=&size=` | Líneas con cada apoyo aplicado; `status` filtra por estado del apoyo |
| `GET /{periodo}/breakdown?by=regional\|business_unit\|salesperson\|customer&supplier_code=` | Dónde vuelve el dinero del proveedor |
| `GET /{periodo}/export?supplier_code=` | Excel: Resumen (por referencia), Detalle ventas y No liquidados |
| `POST /{periodo}/recalculate` | Borrador nuevo. 409 si está cerrado |
| `supplier_code=__none__` | En `references`, `lines`, `breakdown` y `export`: las filas sin código de proveedor |
| `POST /{periodo}/close` | Solo un mes terminado |
| `POST /{periodo}/reopen` | Líder de BI o administrador |

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
