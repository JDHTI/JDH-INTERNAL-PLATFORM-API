# Notificaciones de la vista general

El módulo `notifications` lleva a cada empleado lo que se publica en Strapi: la campana, la página
`/notificaciones` y Web Push. Reemplaza al de INTRANET-JDH-API con el mismo contrato: la misma
carga de la ingesta y los mismos nombres de campo en la campana.

## Cómo llega una notificación

1. **Ingesta.** Al publicar una noticia o un evento, Strapi hace `POST /api/v1/notifications/ingest`
   con la cabecera `X-Jdh-Ingest-Secret`. El secreto es `NOTIFICATIONS_INGEST_SECRET`, el mismo que
   ya tiene el CMS; si está vacío, la ingesta queda apagada y responde 503.
2. **Repetición segura.** La clave `(fuente, fuenteId, idempotencyKey)` hace inofensivo un
   reintento: responde `duplicada: true`. Republicar con un `publishedAt` nuevo cambia la clave y
   vuelve a notificar.
3. **Inmediata o programada.**
   - Sin `programadaPara`, la notificación sale de una vez. Los destinatarios son la audiencia en
     ese momento y el push se envía enseguida.
   - Con `programadaPara`, el despacho la envía cuando llega su hora.
   - Si el despacho llega con más de 24 h de retraso, la cancela y nunca la envía tarde.
4. **Recordatorio de eventos.** Un evento con `recordatorioDiasAntes` y `fechaEvento` programa además
   un recordatorio a las 08:00 (`NOTIFICATIONS_TIMEZONE`), esa cantidad de días antes, salvo que esa
   hora ya haya pasado.
5. **Audiencias.** Se arman con el directorio:
   - `TODOS` llega a los empleados activos.
   - `SEDE` toma ids de sede del directorio.
   - `AREA` toma nombres de departamento, sin distinguir mayúsculas.
   - `EMPLEADOS` toma ids de empleado.
   - `GET /notifications/ingest/audiences` lista las sedes y las áreas con su tamaño, para el editor de
     Strapi.
6. **Personas sin empleado.** Un invitado o el usuario de desarrollo tienen la campana vacía.

## Endpoints (`/api/v1/notifications`)

| Método y ruta | Quién | Respuesta |
| --- | --- | --- |
| `GET` (`page`, `pageSize` 1–50, `soloNoLeidas`) | autenticado | `{items: [{id, titulo, resumen, href, fuente, leida, creada_en}], total, page, pageSize, no_leidas}` |
| `GET /count` | autenticado | `{no_leidas}` |
| `PATCH /{id}/read` | autenticado | 204 |
| `PATCH /read` | autenticado | 204; marca todas |
| `GET /push/public-key` | autenticado | `{clave}`; `null` si push está apagado |
| `POST /push/subscriptions` | autenticado | 204. Recibe `{endpoint, p256dh, auth, expirationTime?}` |
| `DELETE /push/subscriptions?endpoint=` | autenticado | 204 |
| `POST /ingest` | Strapi (secreto) | `{id, estado, duplicada, destinatarios, recordatorioId}` |
| `GET /ingest/audiences` | Strapi (secreto) | `{sedes: [{id, nombre, empleados}], areas: [{nombre, empleados}]}` |

`href` lleva a la página de la vista general: `/noticias/{slug}`, `/eventos/{slug}` o `/perfil`.

## Push

Web Push con VAPID (`pywebpush`):
- **Encendido.** Push funciona cuando están las tres variables `PUSH_VAPID_PUBLIC_KEY`,
  `PUSH_VAPID_PRIVATE_KEY` y `PUSH_VAPID_SUBJECT` (`mailto:...`).
- **Envío.** Va en lotes de 25 a los navegadores de los destinatarios.
- **Navegadores caducados.** Si el servicio de push responde 404 o 410, el navegador se olvida.
- **Fallos.** Un fallo de push nunca quita la notificación de la campana.

## Configuración

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `NOTIFICATIONS_INGEST_SECRET` | vacío | Secreto compartido con Strapi. Vacío apaga la ingesta |
| `NOTIFICATIONS_DISPATCH_ENABLED` | `false` | Enciende el despacho de las programadas |
| `NOTIFICATIONS_DISPATCH_INTERVAL_SECONDS` | `60` | Cada cuánto revisa el despacho |
| `NOTIFICATIONS_TIMEZONE` | `America/Bogota` | Zona de las 08:00 de los recordatorios |
| `PUSH_VAPID_PUBLIC_KEY`, `PUSH_VAPID_PRIVATE_KEY`, `PUSH_VAPID_SUBJECT` | vacíos | Web Push |

## Strapi

El CMS ya manda la misma carga y la misma cabecera. Solo cambia la URL:
`http://platform-api:8000/api/v1/notifications/ingest`, desde la red `intranet-jdh_default`.
`NOTIFICATIONS_API_URL` del stack de borde debe apuntar ahí.
