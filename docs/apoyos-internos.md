# Apoyos internos desde la cotización

Quien cotiza le pide a BI un **margen apoyado** para un cliente, sobre una o varias referencias
de un mismo proveedor. BI, o un aprobador designado, lo aprueba de forma persistente o por única
vez, o lo rechaza. Cada referencia aprobada se convierte en un apoyo del maestro que el validador
de pedidos ya aplica, sin cambiar su código.

Módulo `supplier_supports` (con un puerto hacia `quotations`). Migración `0062`.

## Reglas

- **Origen:** solo una cotización de cliente (`kind = cotizacion`). Las actualizaciones de lista y
  las listas principales no lo ofrecen.
- **Alcance:** el NIT de la cotización y una o varias referencias del mismo proveedor que estén en
  ella. Nunca lista de precios, canal, regional ni unidad de negocio. Un cliente temporal (sin NIT
  de SIESA) no puede pedir.
- **Quién pide:** quien gestiona el `cotizador` y no es aprobador (`can_request_support`).
- **Quién decide** (`can_review_internal`):
  - los gestores de `apoyos` en BI;
  - quien tenga `manage` sobre `aprobacion-apoyos-internos` en cualquier área. Es un permiso sin
    pantalla: viene encendido en BI y, para designar aprobadores en otra área, se enciende allí y
    se le da `manage` al rol;
  - los administradores.
- **Una solicitud pendiente por referencia y cotización:** repetirla es 409.
- **Decisión:**
  - `persistente`: vigencia `valid_from` (por defecto la fecha del pedido) a `valid_to` (vacía =
    indefinida).
  - `unica_vez`: vigencia de un solo día, la fecha del pedido (`requested_for`), aunque se apruebe
    después. La nota es obligatoria.
  - Rechazo: con motivo obligatorio.
  - Se puede ajustar el margen de cada referencia y dejar referencias sin aprobar (al menos una
    aprobada).

## Qué crea la aprobación

Por cada referencia aprobada, en la misma transacción, un apoyo con:

| Campo | Valor |
| --- | --- |
| `kind` / `calc_method` | `excepcion_margen` / `margen_minimo` |
| `min_margin_pct` = `supported_margin_pct` | el margen aprobado |
| `customer_nit`, `product_sku`, proveedor | los de la solicitud |
| `valid_from`, `valid_to` | según la decisión |
| `program` | `Solicitud de apoyo interno #N` |
| `funding` | `jdh` |
| `authorized_by` | quien aprobó |
| `internal_request_id`, `last_origin` | la solicitud y `solicitud_interna` (trazabilidad) |

El validador de pedidos lo toma porque compara el margen del pedido contra `min_margin_pct`, con
alcance cliente × SKU × proveedor y vigente en la fecha del pedido. Se publica al DWH como
cualquier cambio del maestro. **Una carga masiva nunca lo empareja, ni lo cuenta como ausente ni lo
vence** (`expire_missing`), porque no viene en el archivo del proveedor.

## API (`/api/v1`)

Márgenes como fracciones (`"0.16"`) y fechas `YYYY-MM-DD`. Gate de lectura: `cotizador`, `apoyos`
o `aprobacion-apoyos-internos`.

| Método y ruta | Uso |
| --- | --- |
| `POST /quotations/{id}/support-requests` | Pedir: `{version, supplier_code, requested_for?, agreed_with, reason, items: [{sku, requested_margin_pct}]}`. `requested_for` vacío = hoy (Bogotá). 409 si cambió la `version` de la cotización o una referencia ya está pendiente |
| `GET /internal-support-requests?mine&status&q&page&size` | `{items, total, page, size}`, las más nuevas primero. `mine=true`: las propias; sin `mine` solo los aprobadores. `q` busca por NIT, cliente, proveedor, solicitante, SKU o `#número` |
| `GET /internal-support-requests/{id}` | Una solicitud: su autor o un aprobador (404 para el resto) |
| `POST /internal-support-requests/{id}/approve` | `{version, decision, valid_from?, valid_to?, note?, items: [{sku, approved_margin_pct}]}`. Las referencias que no vienen quedan sin aprobar |
| `POST /internal-support-requests/{id}/reject` | `{version, reason}` |

La solicitud guarda, por referencia, cómo estaba la línea al pedir: `cost`, `quoted_price` (precio
de la oferta), `margin_pct` (margen que deja, con los apoyos vigentes) y `min_margin_pct`. Además
`requested_margin_pct`, `approved_margin_pct` y `support_id` (el apoyo creado).

Además:

- `GET /quotations/permissions?kind=cotizacion` devuelve `can_request_support`.
- `GET /supplier-supports/permissions` devuelve `can_review_internal` y `can_request_internal`.
- Cada línea de una cotización de cliente trae `support_request: {id, status, decision,
  approved_margin_pct} | null`, la solicitud más reciente de esa referencia en esa cotización.

Las solicitudes de ajuste JDH sobre un apoyo existente (`/supplier-support-requests`) siguen igual.
