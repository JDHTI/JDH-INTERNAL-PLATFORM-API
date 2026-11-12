# Actualización de listas

Módulo de BI para actualizar una **lista de precios vigente de SIESA**. Usa el motor del
[cotizador](cotizador.md) (costo, margen objetivo, regla general, estados, Excel y plano), con
tres diferencias:

- **Origen de las líneas.** Las líneas salen de la lista, no de las ventas de un cliente.
- **Sin oferta.** No hay oferta al cliente, cotización con cantidades ni productos solo para
  cotización.
- **Siempre actualización.** Los archivos actualizan siempre esa misma lista.

## Datos generales

- **Módulo del catálogo:** `actualizacion-listas`. Por defecto está activo en `bi`; un
  administrador lo activa en otras áreas desde el panel.
- **Migración:** `0058_list_updates`, después de `0057_dwh_outbox_support_loads`.
- **Datos del DWH:** se validaron contra el DWH el 2026-11-12. Ver
  [cotizador-dwh.md](cotizador-dwh.md) §4 para las vistas `listas_precios` y
  `precios_lista_vigentes`.

## Dónde se guarda

Una actualización es una fila de `quotations` con `kind = 'lista'`, y comparte endpoints con el
cotizador (`/api/v1/quotations`). El servicio revisa cada cotización contra el módulo de su tipo,
así que el permiso de un módulo no abre las del otro.

| Campo | Valor en una actualización |
| --- | --- |
| `kind` | `lista` (`cotizacion` en el cotizador) |
| `source` | `cliente` (se eligió un cliente y se tomó su lista) o `lista` (por código o nombre) |
| `price_list_id`, `price_list_name` | la lista que se actualiza |
| `customer_nit`, `customer_name` | si se creó por cliente, el cliente; si no, `""` y el nombre de la lista |
| `list_action` | siempre `actualizacion` |
| `window_months` | 12, la ventana de la última venta |
| línea `origin` | `lista` (tiene precio vigente en la lista) o `agregado` |
| línea `last_price` | el **precio vigente de la lista** por unidad de inventario: contra él se calculan el margen actual, la variación y la alerta bajo objetivo |
| línea `last_sale_price`, `last_sale_cost` | precio neto y costo de ventas unitario de la última venta, con su `last_sale_date` y `last_document` |
| línea `support_options` | todos los apoyos de los clientes de la lista, cada uno con su precio mínimo y óptimo |

## Cómo se arma

1. **Lista** (`POST /quotations/list-updates`), por una de estas dos vías:
   - `{price_list_id}`: el código de la lista, que el buscador de la UI encuentra por código o
     por nombre. El código se compara en mayúsculas contra el maestro y desde ahí se usa el
     código guardado, porque hay listas reales con código en minúscula.
   - `{customer_nit}`: la lista que SIESA le tiene asignada al cliente, por mayoría entre sus
     sucursales activas, como en el cotizador.
   - Un cliente sin lista responde 422. Una lista que no existe responde 404.
2. **Líneas:** cada referencia con precio vigente en la lista
   (`consumo.precios_lista_vigentes`, una fila por SKU, la de la unidad de inventario).
   - Se lee por el código exacto. Con `trim` se pierde el índice: 316 ms en vez de 35 ms.
   - Un precio de $1 o menos es un marcador de SIESA, como las 101 referencias de la lista 1 y
     la lista `PRM`. Queda sin precio actual y con el problema `precio_marcador`.
   - Máximo `LIST_MAX_LINES` = 10.000 referencias. La lista 1 tiene unas 7.600.
3. **Contexto:** se toman todas las sucursales asignadas a la lista (`consumo.clientes`) y se
   combinan igual que las de un cliente.
   - El canal de la mayoría decide el margen objetivo.
   - Los CO de facturación deciden el costo: gana un CO de la Costa, y con varios CO, si la lista
     no tiene bodegas propias, el costo es Nacional.
   - No hay vendedor.
   - Avisos:
     - `lista_compartida`: más de un NIT activo en la lista.
     - `lista_sin_clientes`: sin clientes activos; el margen es Lista 1 y el costo Nacional.
     - `lista_sin_precios`: la lista no tiene precios vigentes.
4. **Costo, impuesto y regla:** los mismos del cotizador (`pricing.py`): el mayor entre la
   cotización del proveedor, el inventario de la bodega de la lista y el alza pendiente, con el
   factor de la familia.
5. **Última venta:** sale de `consumo.ventas` en los últimos 12 meses (solo ventas, con cantidad
   y subtotal mayores que cero).
   - Precio = `subtotal / cantidad`. Costo = `costo / cantidad`, porque `ventas.costo` es el
     costo total de la línea en unidad de inventario. Un costo de 0 o menos no se muestra.
   - De quién se toma:
     - Creada por cliente: las ventas a ese cliente, en sus sucursales de la lista.
     - Creada por lista: las ventas a las sucursales activas de la lista.
     - Lista con más de `LIST_SALES_BRANCH_LIMIT` = 1.000 sucursales activas: toda la compañía,
       en dos pasos (el último mes y después el resto de la ventana). Leer las 28 mil
       sucursales de la lista 1 tardaba más de 20 s.
   - Si el DWH no responde a tiempo, la actualización se crea igual con el aviso
     `ultima_venta_no_leida`, y un refresco vuelve a intentar sin perder la última venta que ya
     tenía cada línea.
   - Tiempos medidos el 2026-11-12 en la 10U: 0,7 s por sus clientes y 2,4 s de toda la
     compañía.
6. **Apoyos:** cuentan los de **todos los clientes activos de la lista**.
   - Un apoyo cubre la lista si cubre al menos a uno de sus clientes. Un apoyo de un cliente se
     revisa contra ese cliente; los demás, una vez por cada combinación distinta de canal,
     unidad de negocio y regional entre los clientes.
   - Cada apoyo trae su precio mínimo y su precio óptimo con ese apoyo (`support_options`). Se
     aplica el que deja el **precio mínimo más bajo**; si empatan, el de óptimo más bajo y luego
     el que dura más.
   - Los apoyos en revisión, de una bodega, los rebates y los que no se pueden calcular se
     listan con su motivo y no se aplican.
   - El cotizador sigue aplicando el apoyo que trae más dinero.

## Quién hace qué

| Acción | Quién |
| --- | --- |
| Ver | `actualizacion-listas: read` (solo las propias), y quien valida (todas) |
| Crear, editar, confirmar y retirar de validación | `actualizacion-listas: manage` en cualquier área donde esté activo |
| Validar, devolver, marcar cargada y descargar la plantilla | `actualizacion-listas: manage` desde el área de `QUOTATIONS_APPROVER_AREA_SLUG` (BI) y los administradores |
| Crear o editar una **lista restringida** | solo quien valida (analistas y líder de BI). Son las de `LIST_UPDATES_RESTRICTED_LISTS`, por defecto `001,002,003,004,005,009` |

La lista 1 (GENERAL) no está restringida y se actualiza como cualquier otra. En el cotizador
sigue yendo por creación.

## API

- **Endpoints propios:**
  - `GET /quotations/price-lists?q=`: listas por código exacto (sin importar mayúsculas) o por
    parte del nombre (sin tildes), activas y más usadas primero.
    - Máximo 50, en una página con la forma de los demás buscadores (`items`, `total`, `page`,
      `size`).
    - Cada una trae `restricted`.
    - Solo lee las columnas del maestro: con las columnas de conteo, la búsqueda por nombre
      tardaba 24 s.
  - `POST /quotations/list-updates` con `{customer_nit}` o `{price_list_id}`: crea la
    actualización.
- **`kind=lista` en los endpoints comunes:** `GET /quotations`, `GET /quotations/permissions` y
  `GET /quotations/customers`. Para una actualización, este último busca entre todos los
  clientes, sin cartera.
- **Endpoints del cotizador:** el resto funciona igual por `id`.
  - Responden 422 en una actualización: `PATCH /offer`, `GET /offer.xlsx`,
    `POST /local-lines` y `PUT /lines` con cantidades.
  - `confirm` solo acepta `actualizacion`.
- **Archivos:** el plano se llama `precios-lista<lista>-<NIT o "actualizacion">-<AAAAMMDD>.txt`.

## UI

Ruta `/<área>/actualizacion-listas` (`src/app/(app)/(workspace)/[area]/actualizacion-listas`).
Usa los componentes del cotizador con `QuotationKindProvider kind="lista"`
(`src/features/quotations/lib/kind.tsx`).

- **Lo que no aparece:** oferta, «Cotizar con cantidades» y «Crear producto».
- **Confirmación:** es de un solo paso, la lista y la fecha de activación.
- **Columna de precio:** dice «Precio actual». Debajo va la última venta, y su costo solo para
  quien valida.
- **Detalle de la línea:** muestra todos los apoyos de la lista con su mínimo y su óptimo.
