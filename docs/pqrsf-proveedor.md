# PQRSF: mensajes al proveedor desde el caso

Quien trabaja un caso (su responsable o un gestor) le escribe al proveedor sin salir de la
plataforma: una solicitud de información, una respuesta, una oportunidad de mejora o un mensaje
informativo. Todo queda en la línea de tiempo y la respuesta del proveedor vuelve al caso.

## `POST /pqrsf/cases/{case_id}/supplier-messages` (`201`)

`multipart/form-data`:

| Campo | Contenido |
|---|---|
| `kind` | `request` (Solicitud de información), `response` (Respuesta), `improvement` (Oportunidad de mejora) o `notice` (Mensaje informativo) |
| `to` | Correo del proveedor, repetible: de 1 a 5 |
| `body` | El mensaje, hasta 5000 caracteres |
| `supplier_code`, `supplier_name` | Opcionales: el proveedor (del producto del caso). El código sirve para sugerir estos correos la próxima vez |
| `include_case_data` | Por defecto `true`: agrega cliente, sede, fecha y productos (SKU, lote, vencimiento, cantidad y factura) |
| `include_evidence` | `true` adjunta las fotos y el video que envió el cliente |
| `files` | Archivos nuevos, con las mismas reglas que los comentarios. Quedan guardados en el caso |

- Solo con el caso abierto (`409` si está finalizado).
- Los adjuntos, incluida la evidencia, no pueden pasar de 25 MB en total (`422`).
- Cada correo sale desde `pqrsf@` como un correo nuevo, uno por destinatario. El asunto empieza
  con el consecutivo entre corchetes (`[Q26AC0001] Solicitud de información: …`), así que la
  respuesta del proveedor vuelve sola al caso por el buzón (`docs/pqrsf-correo.md`).
- Pasa por la cola de correos, con los reintentos de siempre. Con `PQRSF_NOTIFY_REDIRECT` (pruebas)
  todo llega a esa dirección.
- La respuesta trae el evento de la línea de tiempo (`event_type: "supplier_message"`, con su
  `title`, por ejemplo «Envió una solicitud de información a TEAM FOODS»), los `files`
  guardados, los `rejected_files` y un `emails[]` con el estado de cada correo.
- El cliente no ve estos mensajes.

## Otros endpoints

| Endpoint | Para qué |
|---|---|
| `GET /pqrsf/cases/{case_id}/supplier-messages` | Correos enviados a proveedores desde el caso: `to`, `subject`, `status` (`pending`, `sent`, `failed`), `error`, `sent_at` |
| `POST /pqrsf/cases/{case_id}/supplier-messages/{notification_id}/retry` | Reintentar un correo que falló |
| `GET /pqrsf/supplier-contacts?supplier_code=&q=&limit=` | Correos ya usados para un proveedor, el más reciente primero. Se llena sola al enviar (`pqrsf_supplier_contacts`); el catálogo de Abastecimiento se puede cargar ahí después |

## En la línea de tiempo

- El mensaje enviado aparece como el autor que lo envió: «Juan Gómez · Envió una solicitud de
  información a TEAM FOODS».
- Cuando el proveedor contesta (por el buzón, conservando el consecutivo en el asunto), el
  evento `email_received` llega con `actor_kind: "supplier"` y el título «Respondió por correo»,
  porque su dirección es una de las que recibieron un mensaje del caso.

Migración `0026_pqrsf_supplier_contacts`.
