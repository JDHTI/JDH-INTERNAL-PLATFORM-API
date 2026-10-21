# PQRSF: comentarios con archivos y respuesta parcial al cliente

Un comentario del caso puede llevar archivos y, si se pide, enviarse por correo al
solicitante. Siempre es una **respuesta parcial**: no cierra el caso ni cambia su estado.

## `POST /pqrsf/cases/{id}/comments`

Acepta dos formatos (el JSON de antes sigue funcionando):

| Formato | Campos |
|---|---|
| `application/json` | `body` (obligatorio, hasta 5000 caracteres), `reply_to_client` (opcional, `false`). Sin archivos |
| `multipart/form-data` | `body`, `reply_to_client` (`true`/`false`) y `files` (cero o más archivos) |

Respuesta `201`: los campos del evento de la línea de tiempo (como antes) más

| Campo | Contenido |
|---|---|
| `files` | Archivos guardados: `id`, `filename`, `content_type`, `size_bytes`, `created_at`, `comment_event_id` |
| `rejected_files` | `[{name, reason}]` de los archivos que no cumplieron las reglas; el comentario se guarda igual |
| `client_reply` | Solo con `reply_to_client`: `id`, `event_id`, `to`, `status` (`pending`/`sent`/`failed`), `attempts`, `error`, `sent_at`, `created_at` |

`data` del evento: `file_ids`, `reply_to_client`, `rejected_files` y, si hubo respuesta,
`client_reply: {to, notification_id}`.

### Archivos
- Mismas reglas que los correos entrantes: extensiones permitidas (`MAIL_ATTACHMENT_EXTENSIONS`),
  tamaños, antivirus, imágenes recodificadas, contenido que corresponde a la extensión.
  Un archivo repetido en el mismo comentario se guarda una vez.
- Se guardan en el bucket bajo el caso y aparecen en `GET /pqrsf/cases/{id}/files`
  con `comment_event_id` (los que llegaron por correo lo traen `null`). Se descargan con
  `GET /pqrsf/cases/{id}/files/{file_id}/download`.

### Respuesta al cliente (`reply_to_client = true`)
- Quién: quien trabaja el caso (su responsable o un gestor). Comentar sin responder lo puede
  hacer cualquiera que vea el caso, como antes.
- **No se permite en casos finalizados** (`409`). Tampoco sin correo del solicitante (`422`).
- Sale desde `pqrsf@` con el texto del comentario, el consecutivo y la firma **PQRSF JDH**,
  y lleva los archivos adjuntos. Si el caso nació por correo, responde **dentro de la
  conversación del cliente**; si no (formulario público), es un correo nuevo con el
  consecutivo en el asunto, para que la contestación del cliente vuelva a su caso.
- Los adjuntos de una respuesta al cliente no pueden sumar más de **3 MB** (límite de Graph
  por solicitud); un comentario interno no tiene ese tope.
- Pasa por la cola de correos: se envía en menos de 30 s y se reintenta a 1 min, 5 min, 30 min y
  2 h. Con `PQRSF_NOTIFY_REDIRECT` (pruebas) se envía a esa dirección y nunca al cliente.

## Estado del envío

- `GET /pqrsf/cases/{id}/client-replies`: respuestas enviadas al cliente y su estado.
- `POST /pqrsf/cases/{id}/client-replies/{notification_id}/retry`: reintenta una respuesta
  que quedó `failed` (responsable o gestor; `409` si no falló).

## Texto

Los comentarios conservan sus saltos de línea (antes se colapsaban en una sola línea), tanto
en la línea de tiempo como en el correo.
