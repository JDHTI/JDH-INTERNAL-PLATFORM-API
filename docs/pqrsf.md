# PQRSF

El módulo PQRSF expone un formulario público aislado de la sesión de Entra y
un conjunto de operaciones administrativas protegidas por cookie de auth y el
rol local `admin`.

## Contrato HTTP

La base local es `/api/v1`.

| Método | Ruta | Acceso | Nivel de límite |
| --- | --- | --- | --- |
| `GET` | `/public/pqrsf/catalogs` | Público | `read` |
| `GET` | `/public/pqrsf/products?q=&limit=` | Público | `read` |
| `POST` | `/public/pqrsf/requests` | Público | `strict` |
| `GET` | `/pqrsf/requests` | Admin | `read` |
| `GET` | `/pqrsf/requests/{request_id}` | Admin | `read` |
| `GET` | `/pqrsf/requests/{request_id}/attachments/{attachment_id}/download` | Admin | `read` |
| `POST` | `/pqrsf/requests/{request_id}/case` | Admin | `relaxed` |
| `GET` | `/sites` | Admin | `read` |
| `POST` | `/sites` | Admin | `relaxed` |
| `PATCH` | `/sites/{site_id}` | Admin | `relaxed` |
| `DELETE` | `/sites/{site_id}` | Admin | `relaxed` |

El seguimiento interno de los casos (bandeja, estados, responsables,
documentos y SLA) está documentado en
[`docs/pqrsf-seguimiento.md`](pqrsf-seguimiento.md).

El catálogo devuelve `request_types`, `sites`, `document_categories` y `subjects`.
Cada tipo contiene `id`, `code`, `name`, `description`, `allows_document_request`
y `allows_attachments`; la interfaz debe usar esas dos banderas para mostrar u
ocultar campos y no hardcodear la lógica condicional. `description` es una línea
corta (máximo 120 caracteres) que se muestra **siempre visible** bajo cada opción
de tipo, en texto secundario; no va en un tooltip (en el celular no se ve) ni en
un bloque de ayuda. Calidad la cambia con `PATCH /pqrsf/request-types/{id}`.

Cada categoría trae `id`, `code`, `name` y sus `documents` (`id`, `name`): sólo
los tipos de documento con categoría se ofrecen.

### Asunto y causal

`subjects` es el catálogo de asuntos (las novedades de la tabla de canales de
comunicación). Cada asunto trae `id`, `code`, `name`, `description`,
`request_type_codes` (los tipos que lo ofrecen), `is_other` y `causes`. Cada
causal trae `id`, `code`, `name`, `description`, `is_other`, `allows_product` y
`requires_product`. La dependencia y la ruta de tratamiento de la causal no se
publican: deciden a qué área va el caso, en el servidor.

Reglas para la interfaz:

- Ofrecer los asuntos cuyo `request_type_codes` contiene el `code` del tipo
  elegido. Si el tipo no tiene asuntos (sugerencia, felicitación) o es una
  solicitud de documentos, se sigue pidiendo el asunto escrito (`subject`).
- Con asunto elegido, mostrar sus `causes`; si no tiene, no se pide causal.
- `is_other` en el asunto o en la causal: la descripción pasa a obligatoria y
  conviene pedir también `subject` como asunto corto.
- `allows_product`: mostrar la sección de productos; `requires_product`: exigir
  al menos una línea.

Los asuntos y causales sembrados son **provisionales** (los de la reunión del
22 de octubre) hasta recibir la matriz oficial de Calidad, que los mantiene sin
despliegue (sesión de Entra; crear y editar solo gestores PQRSF):

| Método | Ruta | Para qué |
| --- | --- | --- |
| `GET` | `/pqrsf/subjects?include_inactive=` | Asuntos con sus causales, dependencia (`dependency_id`) y ruta (`route`) |
| `POST` | `/pqrsf/subjects` | Crear asunto: `code`, `name`, `request_type_codes`, `description?`, `dependency_id?`, `is_other?`, `sort_order?` |
| `PATCH` | `/pqrsf/subjects/{id}` | Editar o desactivar (`is_active`); el código no cambia |
| `POST` | `/pqrsf/subjects/{id}/causes` | Crear causal: `code` (único entre todas), `name`, `description?`, `dependency_id?`, `route` (`supplier`, `internal`, `none`), `requires_product?`, `allows_product?`, `is_other?`, `sort_order?` |
| `PATCH` | `/pqrsf/causes/{id}` | Editar o desactivar; el código y el asunto no cambian |

Nada se borra: lo que deja de usarse se desactiva, y las solicitudes que lo
eligieron lo conservan. Las descripciones admiten 120 caracteres, como las de los
tipos. Una causal que exige productos siempre los admite.

### Productos

`GET /public/pqrsf/products?q=` busca por referencia exacta (SKU JDH) o parte
del nombre (mínimo 2 caracteres, `limit` hasta 20) y devuelve `sku` y
`description`. Nunca devuelve proveedor ni costos: el proveedor se resuelve en
el servidor al guardar. Lee el maestro de productos de `commercial_masters`, que
se sincroniza desde el DWH con `scripts/sync-dwh-masters.py`; si el maestro está
vacío, ninguna referencia existe y las quejas con producto se rechazan.

Los cinco tipos iniciales son `peticion`, `queja`, `reclamo`, `sugerencia` y
`felicitacion`. El catálogo de sedes inicial contiene Aco, Apartadó,
Barranquilla, Bogotá, Bucaramanga, Cartagena, JDH Market Apartadó, JDH Market
Valledupar, Medellín, Pereira, Valledupar y Yumbo.

## Envío público

El `POST` usa `multipart/form-data`. Debe incluir un UUID nuevo en el header
`Idempotency-Key`; el servidor lo utiliza para que reintentos del navegador no
dupliquen la solicitud.

Campos de formulario:

- `request_type_id`: UUID del catálogo.
- `site_id`: UUID de la sede.
- `full_name`, `business_name`, `nit`, `address`, `department`, `city` y
  `email`: obligatorios.
- `subject_id` y `cause_id`: asunto y causal del catálogo, obligatorios en los
  tipos que ofrecen asuntos (petición que no es de documentos, queja, reclamo).
  La causal debe ser del asunto. El caso toma como asunto «Asunto · Causal» y
  como dependencia la de la causal (o la del asunto).
- `subject`: asunto escrito (máximo 200 caracteres). Obligatorio en los tipos sin
  asuntos y en las solicitudes de documentos; con «Otro», complementa el asunto.
- `description`: obligatoria en sugerencia y felicitación (es el contenido) y
  cuando el asunto o la causal es «Otro»; opcional en lo demás.
- `products`: arreglo JSON de líneas, sólo si la causal lo admite (máximo 20):
  `[{"invoice_number", "sku", "lot", "expiry_date": "AAAA-MM-DD",
  "affected_quantity"}]`. La referencia debe existir en el maestro; se guarda con
  su descripción y su proveedor de ese momento.
- `pickup_order_requested`: `yes`, `no` o `unknown`, obligatorio cuando hay
  productos. `pickup_order_number` (máximo 40) solo con `yes` y es opcional.
- `document_request`: `true` cuando la petición es una solicitud de documentos
  (sólo tipos con `allows_document_request`). Entonces se exige
  `document_category_id` y al menos un `document_type_ids` de esa categoría o un
  `document_other` (texto libre, máximo 300). El caso nace con esos documentos
  como ítems, con su plazo, y con la dependencia de la categoría. Una solicitud
  de documentos no admite `images`. Sin `document_request`, ninguno de esos
  campos debe enviarse.
- `images`: cero o más archivos en los tipos con `allows_attachments` (petición,
  queja, reclamo y sugerencia), y nunca en una solicitud de documentos.
- `videos`: con las mismas reglas que `images`; por defecto un video MP4, MOV o
  WebM de hasta 20 MB (`PQRSF_MAX_VIDEOS`, `PQRSF_MAX_VIDEO_MB`).

El catálogo trae `limits` (`max_images`, `max_image_mb`, `max_total_image_mb`,
`image_extensions`, `max_videos`, `max_video_mb`, `video_extensions`) para que la
interfaz avise antes de enviar.

El detalle administrativo de la solicitud (y el `request` del detalle del caso)
incluye `selected_subject`, `selected_cause`, `products` y los dos campos de la
orden de recogida.

El servidor devuelve `201` con `{ "id", "reference", "tracking_code",
"created_at" }`. `tracking_code` es el consecutivo del caso de seguimiento
(`Q-2026-00012`) y es el número que la UI debe mostrar al cliente; puede ser
`null` si el caso no se pudo abrir en ese momento (ver
[`docs/pqrsf-seguimiento.md`](pqrsf-seguimiento.md)). Los
errores de validación usan el formato de errores de la API y normalmente
responden `422`; un límite excedido responde `413`, una clave de idempotencia
reutilizada con datos distintos responde `409`, rate limiting responde `429` y
la caída de Redis, S3 o ClamAV responde `503`.

## Seguridad de imágenes

Se aceptan únicamente JPG/JPEG, PNG y WebP. La API comprueba nombre,
extensión, MIME y formato real, limita cada archivo a 5 MiB, permite máximo
cinco archivos y 25 MiB por solicitud, limita la imagen decodificada a 20
millones de píxeles y rechaza imágenes animadas. Pillow decodifica y re-encoda
el contenido sin EXIF antes de que ClamAV lo analice. Sólo después se guarda en
un bucket S3/MinIO privado bajo una clave generada por el servidor.

Los videos no se recodifican: se comprueba que nombre, extensión, MIME y
contenedor real (firma `ftyp` de MP4/MOV, EBML de WebM) coincidan, se limita su
tamaño y pasan por ClamAV antes de guardarse en el mismo bucket privado. Para
darles cabida, el tope del cuerpo de los formularios públicos
(`PUBLIC_MAX_BODY_MB`) sube de 28 a 50 MB.

Los metadatos nunca exponen la clave privada. La descarga sólo está disponible
para un administrador mediante el endpoint protegido de descarga.

## Rate limiting

Los perfiles son configurables por ambiente:

- `read`: 600 solicitudes por minuto (consultas).
- `relaxed`: 120 solicitudes por minuto (cambios).
- `strict`: 5 solicitudes cada 10 minutos (lo costoso o abierto al público).

Todos los endpoints tienen un nivel, aplicado por un middleware global; la tabla completa está en
[`docs/rate-limiting.md`](rate-limiting.md).

El contador usa una ventana deslizante en Redis. Si Redis no responde, el
endpoint falla cerrado con `503`; no se acepta un envío público sin el control
de abuso disponible.

## Desarrollo local

Para usar la API desde el host, levanta la infraestructura y ejecuta la
migración:

```powershell
docker compose up -d
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Para ejecutar también la API y ClamAV en contenedores usa:

```powershell
docker compose --profile app up -d --build
```

El formulario público de la UI debe vivir en `/pqrsf`, fuera del grupo de
layout autenticado. Puede reutilizar los tokens visuales y la fuente de la
plataforma, pero no debe montar sidebar, header, sesión ni navegación privada.
Las llamadas públicas deben omitir credenciales (`credentials: "omit"`); las
operaciones administrativas conservan `credentials: "include"`.

El plan ejecutable de la interfaz está en
[`docs/pqrsf-ui-plan.md`](pqrsf-ui-plan.md).
