# PQRSF: sugerencias y felicitaciones, y protección del formulario público

Solo Petición, Queja y Reclamo requieren intervención y abren un caso. **Sugerencia** y
**Felicitación** se guardan en una sección aparte, visible solo para los gestores de PQRSF, y
se responden al instante con un mensaje genérico firmado «PQRSF JDH». No tienen consecutivo,
estado, responsable ni fecha límite, y por eso no cuentan en vencidos, resúmenes ni recordatorios.

## Cómo se decide

`pqrsf_request_types.handling` define qué ocurre con una solicitud de cada tipo:

| Valor | Efecto |
|---|---|
| `case` | Abre un caso de seguimiento (Petición, Queja, Reclamo) |
| `acknowledge` | Se guarda en `pqrsf_feedback` y se agradece (Sugerencia, Felicitación) |

La migración `0019_pqrsf_feedback` pone `acknowledge` en esos dos tipos. Se cambia con
`PATCH /pqrsf/request-types/{id}` (`{"handling": "case" | "acknowledge"}`, solo gestores) y
viaja en `GET /pqrsf/tracking/catalogs` y en el catálogo público. Los casos que ya existían de
esos tipos se quedan como están.

## Por dónde llegan

- **Formulario público.** `POST /public/pqrsf/requests` responde `201` con `handled_as`:
  `case` (con `tracking_code`) o `feedback` (sin consecutivo; la interfaz solo agradece).
- **Correo a `pqrsf@`.** Con `Tipo: Sugerencia` o `Tipo: Felicitación` en la plantilla se guarda
  como mensaje y se responde **dentro del mismo hilo** del cliente. No se envía el acuse con
  consecutivo de los casos. Los adjuntos pasan por las mismas reglas de siempre (extensiones,
  tamaños, antivirus, recodificado de imágenes) y quedan en `pqrsf/feedback/<mensaje>/<archivo>`.
  Si el cliente contesta el agradecimiento sin plantilla («gracias»), su respuesta se queda con
  el mismo tipo en vez de abrir una petición.

## El agradecimiento

Sale por la misma cola de correos que el resto (`pqrsf_notifications`, tipo `feedback_ack`),
con reintentos a 1 min, 5 min, 30 min y 2 h. Al encolarlo se despierta al trabajador, así que
sale en segundos y no a la siguiente pasada de 30 s. Los textos viven en
`notification_templates.feedback_ack`.

No se envía (y el mensaje se guarda igual, con `reply.skipped_reason`) cuando:

| Motivo | Cuándo |
|---|---|
| `recipient_limit` | Esa dirección ya recibió `PQRSF_FEEDBACK_REPLIES_PER_EMAIL_PER_DAY` agradecimientos (3) en 24 h. Evita usar el formulario para llenar el buzón de un tercero con nuestro correo |
| `same_conversation` | Ya se agradeció en esa conversación de correo |
| `disabled` | `PQRSF_NOTIFICATIONS_ENABLED=false` en este entorno |

## Pantalla de gestión (solo gestores: administradores y Calidad)

| Endpoint | Para qué |
|---|---|
| `GET /pqrsf/feedback/summary` | `total`, `unread` y `by_kind` (insignia del menú) |
| `GET /pqrsf/feedback?kind=&q=&unread=&from=&to=&page=&size=` | Lista, lo más nuevo primero |
| `GET /pqrsf/feedback/{id}` | Detalle completo con archivos; la primera apertura lo marca leído |
| `GET /pqrsf/feedback/{id}/files/{file_id}/download` | Descarga (siempre como adjunto) |

Cada elemento trae `kind`, `origin` (`public_form`/`email`), remitente, `subject`, `body_excerpt`,
`site`, `files_count`, `read_at` y `reply: {status, sent_at, skipped_reason}` con `status` en
`pending`, `sent`, `failed` o `skipped`.

## Protección del formulario público

No existía un límite global: cada ruta declaraba el suyo, y el 429 se evaluaba **después** de que
FastAPI leía el cuerpo completo (hasta decenas de MB de imágenes). Ahora todo `POST` bajo
`/api/v1/public/*` pasa antes por `PublicIntakeGuard` (`app/core/public_guard.py`), que decide
**sin leer el cuerpo**, de lo más barato a lo más caro:

1. **Tamaño.** Exige `Content-Length` (`411` si falta) y rechaza con `413` lo que supere
   `PUBLIC_MAX_BODY_MB` (28).
2. **Por dirección.** Perfil `strict`: 5 envíos por IP cada 10 min.
3. **Global.** Perfil `public_global`: `RATE_LIMIT_PUBLIC_GLOBAL_REQUESTS` (100) por hora entre
   todos los visitantes, para un ataque repartido en muchas IPs.
4. **CAPTCHA (Cloudflare Turnstile).** Apagado hasta definir `PUBLIC_CAPTCHA_SECRET`. Con él,
   cada envío debe traer un token válido en la cabecera `X-Captcha-Token`. Si el servicio de
   verificación no responde, no se admite el envío (`503`).

Si Redis no responde, se responde `503` y nada se admite. Las respuestas `413`/`429`/`403` llevan
las cabeceras CORS para que el navegador pueda leerlas. El resto de la API tiene sus propios
niveles (`read`, `relaxed`, `strict`), ver [`rate-limiting.md`](rate-limiting.md).

### Para el despliegue

- **`RATE_LIMIT_TRUSTED_PROXY_IPS`** debe listar los proxies delante de la API. Si no, todos los
  visitantes comparten la dirección del proxy, caen en un solo balde y el límite por dirección
  bloquea a todos con el sexto envío.
- Los límites son de ventana deslizante en Redis; no hay estado en el proceso.
- Para activar el CAPTCHA: crear el sitio en Turnstile, poner el *secret* en
  `PUBLIC_CAPTCHA_SECRET` y que el formulario envíe el token en `X-Captcha-Token`.
