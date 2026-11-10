# Productos solicitados

Cuando un asesor cotiza un producto que no existe en SIESA (una línea `LOC-<n>` del
[cotizador](cotizador.md)), el producto queda en este registro para que BI lo cree en SIESA o lo
descarte. Es un módulo propio, `productos-solicitados`, que un administrador activa por área como
cualquier otro (por defecto en BI y Comercial; ver [permissions.md](permissions.md)).

El código vive en el módulo `quotations` (la tabla `quotation_requested_products`, migración
`0051_quotation_offer`), porque las líneas de las cotizaciones lo alimentan; las rutas y el permiso
son propios.

## Cómo se llena

- Cada `POST /quotations/{id}/local-lines` con descripción crea una entrada `solicitado` (precio
  estimado = el precio de la línea) y la enlaza a la línea. Con `requested_product_id` se cotiza
  otra vez una entrada `solicitado` sin crear otra.
- Quitar la línea o borrar la cotización **no** borra la entrada.
- Cuando BI la marca `creado` con su referencia SIESA, el asesor cambia la línea local por esa
  referencia (`POST /quotations/{id}/lines/{sku}/promote`).

## Estados

| Estado | Etiqueta | Qué significa |
| --- | --- | --- |
| `solicitado` | Solicitado | falta crearlo en SIESA |
| `creado` | Creado | ya existe en SIESA (`siesa_sku`, referencia activa) |
| `descartado` | Descartado | no se va a crear |

Solo `creado` conserva `siesa_sku`; volver a `solicitado` o pasar a `descartado` lo borra.

## Permisos

- **Leer**: el módulo `productos-solicitados` (`read` o `manage`) en algún área, **o** cotizar o
  validar en el cotizador. Cada entrada lista solo las cotizaciones que quien consulta puede abrir
  (quien valida y los moderadores, todas; quien cotiza, las suyas; quien solo tiene este módulo,
  ninguna).
- **Resolver** (`PATCH`): quien valida en el cotizador (BI) o un administrador de la plataforma.

## API (`/api/v1/requested-products`)

| Método y ruta | Uso |
| --- | --- |
| `GET /requested-products/permissions` | `{can_read, can_resolve}`. Abierta a todo usuario autenticado |
| `GET /requested-products` | `status` (`solicitado`, `creado`, `descartado`), `q` (descripción o proveedor), `page`, `size` (≤ 100). Primero los `solicitado`, luego los más nuevos. `{items, total, page, size}` |
| `PATCH /requested-products/{id}` | `{status, siesa_sku?, note?}` → el ítem. 403 si no resuelve; 404 si no existe; 422 si `creado` no trae una referencia SIESA existente y activa. `note` cambia solo si se envía (vacía la borra) |

Ítem: `{id, description, supplier_name, estimated_price, vat_pct, status, status_label, siesa_sku,
note, created_by, created_at, updated_by, updated_at, quotations: [{id, customer_name,
customer_nit, status, status_label, created_by}]}`.

Las rutas viejas `/quotations/requested-products` se retiraron.
