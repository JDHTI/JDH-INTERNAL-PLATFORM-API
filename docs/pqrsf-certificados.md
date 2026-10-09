# PQRSF: certificados que se envían automáticamente

Un cliente que pide documentos por el formulario público recibe **al instante, por correo y sin
intervención humana**, los que tengan un certificado configurado. Solo lo que no tiene
certificado, y lo escrito como «Otro», sigue la ruta de siempre: un caso que una persona gestiona.

## El catálogo de certificados

Cada tipo de documento que ofrece el formulario (RUT JDH, Cámara de Comercio JDH, Certificado de
fumigación…) puede apuntar a **un archivo del gestor documental del área de Calidad**
(`PQRSF_CERTIFICATES_AREA_SLUG`, por defecto `calidad`). El archivo no se copia: si Calidad lo
reemplaza o actualiza en el gestor, lo que se envía cambia solo, y se lee en el momento del envío.

Es **opt-in por certificado**: nada se envía automáticamente hasta que un gestor lo configura y lo
deja activo. Se excluye la categoría «Otro». Solo pueden ser certificado los archivos del área de
Calidad (otras áreas no, aunque quien configura tenga acceso a ellas).

| Endpoint (solo gestores PQRSF) | Para qué |
|---|---|
| `GET /pqrsf/certificates` | Todos los tipos de documento del formulario por categoría, con su certificado: `is_active`, `has_file`, `automatic` y `document` con su **ruta** (`path`: «Calidad / Carpeta / Documento») |
| `GET /pqrsf/certificates/documents?q=&folder_id=` | Archivos de Calidad para elegir (máximo 100) |
| `PUT /pqrsf/certificates/{document_type_id}` | `{document_id, is_active}`: elige el archivo y lo activa o desactiva |
| `DELETE /pqrsf/certificates/{document_type_id}` | Quita el certificado; ese documento vuelve a abrir casos |

Si el archivo se borra del gestor, el certificado queda sin archivo (`has_file: false`) y deja de
enviarse: la solicitud abre un caso en vez de fallar.

## Qué pasa con una solicitud de documentos

1. **Se separa** lo que tiene certificado activo con archivo (automático) de lo demás (manual).
2. **Todo automático:** no se abre caso. La respuesta del formulario trae
   `handled_as: "documents"` y `delivered_documents` con los nombres enviados.
3. **Mezclado:** se abre un caso **solo con lo manual** (con su SLA y su consecutivo) y el correo
   de los certificados le cuenta al cliente qué quedó pendiente y con qué consecutivo. La respuesta
   trae `handled_as: "case"`, `tracking_code` y `delivered_documents`.
4. **Nada automático:** igual que antes.

El catálogo público marca con `automatic: true` los documentos que se responden al instante, para
que el formulario pueda decirlo.

El correo sale desde `pqrsf@`, firmado «PQRSF JDH», por la misma cola de correos del resto
(reintentos a 1 min, 5 min, 30 min y 2 h) y se despierta al trabajador al encolarlo. Una solicitud
con muchos o muy pesados archivos se parte en varios correos (`PQRSF_DOCUMENT_EMAIL_MAX_MB`, 15 MB
de adjuntos por correo; un archivo más grande va solo). Graph limita lo que cabe en una sola
llamada, por eso un correo de más de 2 MB se envía como borrador y un archivo de 3 MB o más se
sube por sesión de carga.

### Cuándo NO se envía automáticamente (todo abre un caso)

| Motivo | Cuándo |
|---|---|
| Envíos apagados | `PQRSF_NOTIFICATIONS_ENABLED=false` en ese entorno |
| Tope por dirección | La dirección ya recibió `PQRSF_DOCUMENT_EMAILS_PER_ADDRESS_PER_DAY` correos de documentos (5) en 24 h: el formulario no sirve para llenar el buzón de un tercero con nuestro correo |
| Fallo al registrar | Si no se puede guardar el registro del envío, la solicitud abre su caso completo |

Los topes del formulario público (tamaño, límite por IP y global, CAPTCHA) siguen aplicando
(`docs/pqrsf-sugerencias.md`).

## La sección «Solicitudes de documentos»

Registro de lo que se respondió automáticamente, aparte de la bandeja de casos y sin SLA.
Solo gestores.

| Endpoint | Para qué |
|---|---|
| `GET /pqrsf/document-deliveries/summary` | `total` y `unread` (insignia del menú) |
| `GET /pqrsf/document-deliveries?q=&unread=&from=&to=&page=&size=` | Lista, lo más nuevo primero, de 10 en 10 |
| `GET /pqrsf/document-deliveries/{id}` | Detalle; la primera apertura lo marca leído (`read_by`) |
| `POST /pqrsf/document-deliveries/{id}/retry` | Reenvía un envío que se rindió (`status: "failed"`) |

Cada elemento trae quién pidió (`full_name`, `business_name`, `nit`, `email`, `site`), los
`documents` enviados, los `manual_documents` que fueron a un caso y su `case_code`, y el estado del
envío: `pending`, `sent` (con `sent_at`) o `failed` (con `error`).

## Responder a mano un documento de un caso

Para los documentos de un caso que nadie respondió automáticamente (sin certificado y «Otro»),
quien trabaja el caso (su responsable o un gestor) responde cada documento con un archivo, por
una de dos vías, y el archivo sale al cliente por correo desde `pqrsf@`.

`POST /pqrsf/cases/{case_id}/items/{item_id}/delivery` (`201`)

| Vía | Cuerpo | Qué hace |
|---|---|---|
| Vincular un archivo del gestor | JSON `{"source": "vault", "document_id": "…"}` | Se adjunta el archivo del gestor documental **de Calidad** (de otra área se rechaza). No se copia al caso; se lee al enviar |
| Subirlo «de una sola vez» | `multipart/form-data`: `source=upload`, `file` | Se guarda con el caso (aparece en `GET /cases/{id}/files`) y viaja en el correo; **nunca entra al gestor**. Pasa por las mismas reglas que cualquier archivo (extensiones, tamaño, antivirus) |

Campos comunes: `send_to_client` (por defecto `true`; sin él solo se registra la respuesta),
`delay_cause` y `delay_detail` (obligatorios si la respuesta llega fuera del tiempo acordado).

- **Cuándo aplica la subida única:** a los documentos libres (sin tipo) y a los tipos con
  `allows_direct_upload: true`. La migración lo activa en la categoría «Otro»; un gestor lo cambia
  por tipo con `PATCH /pqrsf/document-types/{id}` (viaja en el catálogo y en el tipo de cada
  documento del caso). Con otro tipo se responde `422`: debe vincularse un archivo del gestor.
- **Lo que hace:** marca el documento como respondido hoy (con las mismas reglas del
  `PATCH` de documentos: el primer documento respondido pasa el caso a «En curso»), guarda el
  archivo, y encola el correo (asunto «Documento de tu solicitud P26…», firmado «PQRSF JDH»).
  Si algo falla antes (archivo inválido, falta la causal del retraso) no se guarda ni se envía nada.
- **Respuesta:** `{case, delivery}`: el caso actualizado y la respuesta con `source`, `filename`,
  `document_path` (vincular), `case_file_id` (subir) y `email.status` (`not_sent`, `pending`,
  `sent` o `failed`, con `sent_at` y `error`).
- **Errores:** `409` si el caso está finalizado o el documento ya tiene respuesta; `422` sin
  correo del solicitante (con `send_to_client`), archivo inválido o tipo que no admite subida.

Otros endpoints (quien trabaja el caso):

| Endpoint | Para qué |
|---|---|
| `GET /pqrsf/cases/{case_id}/documents/search?q=&folder_id=` | Archivos del gestor de Calidad para vincular (máx. 100) |
| `GET /pqrsf/cases/{case_id}/deliveries` | Respuestas dadas en el caso, con el estado de su correo |

Para **subir un archivo nuevo al gestor y vincularlo**, la interfaz usa el endpoint que ya existe
del gestor documental, `POST /areas/calidad/folders/{folder_id}/documents` (multipart `file`,
`name`, `description`, `tags`; requiere ser miembro de Calidad), y luego vincula el `id` que
devuelve con `source: "vault"`.

## Sugerencias y felicitaciones ya no son tipos de caso

Sugerencia y Felicitación tienen su propia sección (`docs/pqrsf-sugerencias.md`). Por eso
`GET /pqrsf/tracking/catalogs` ya no las lista entre los tipos de caso, y crear un caso interno de
esos tipos se rechaza. El formulario público las sigue ofreciendo. Los casos históricos de esos
tipos conservan su tipo.

## Configuración

| Variable | Defecto |
|---|---|
| `PQRSF_CERTIFICATES_AREA_SLUG` | `calidad` |
| `PQRSF_DOCUMENT_EMAILS_PER_ADDRESS_PER_DAY` | `5` |
| `PQRSF_DOCUMENT_EMAIL_MAX_MB` | `15` |

Migración `0020_pqrsf_certificates`: `pqrsf_certificates` y `pqrsf_document_deliveries`.

## Qué cuidar al elegir certificados

El formulario es público: un certificado activo se envía a cualquier correo que escriba quien lo
pide. Conviene activar solo documentos que JDH está dispuesta a entregar a un tercero y revisar
los sensibles del catálogo (copia de la cédula del representante legal, certificación bancaria,
estados financieros) antes de activarlos.
