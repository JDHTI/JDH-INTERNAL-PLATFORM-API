# PQRSF

El módulo PQRSF expone un formulario público aislado de la sesión de Entra y
un conjunto de operaciones administrativas protegidas por cookie de auth y el
rol local `admin`.

## Contrato HTTP

La base local es `/api/v1`.

| Método | Ruta | Acceso | Nivel de límite |
| --- | --- | --- | --- |
| `GET` | `/public/pqrsf/catalogs` | Público | `read` |
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

El catálogo devuelve `request_types`, `sites` y `document_categories`. Cada tipo
contiene `id`, `code`, `name`, `allows_document_request` y `allows_attachments`;
la interfaz debe usar esas dos banderas para mostrar u ocultar campos y no
hardcodear la lógica condicional. Cada categoría trae `id`, `code`, `name` y sus
`documents` (`id`, `name`): sólo los tipos de documento con categoría se ofrecen.

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
- `description`: obligatoria para todos los tipos.
- `subject`: asunto, obligatorio para todos los tipos (máximo 200 caracteres).
  Es el asunto del caso.
- `document_request`: `true` cuando la petición es una solicitud de documentos
  (sólo tipos con `allows_document_request`). Entonces se exige
  `document_category_id` y al menos un `document_type_ids` de esa categoría o un
  `document_other` (texto libre, máximo 300). El caso nace con esos documentos
  como ítems, con su plazo, y con la dependencia de la categoría. Una solicitud
  de documentos no admite `images`. Sin `document_request`, ninguno de esos
  campos debe enviarse.
- `images`: cero o más archivos sólo para petición, queja y reclamo, y nunca en
  una solicitud de documentos.

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
