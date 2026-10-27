# PQRSF: avisos por correo y temporizadores

Todos los correos salen desde el buzón `pqrsf@jdh.com.co` por Microsoft Graph. Están
apagados por defecto (`PQRSF_NOTIFICATIONS_ENABLED=false`) y requieren las credenciales
`MAIL_GRAPH_*` (ver `docs/pqrsf-correo.md`).

## 1. Aviso de asignación

| Quién asigna | Resultado |
|---|---|
| A sí mismo | Nada: el caso se asigna sin más |
| A otra persona | Correo «Nuevo caso asignado: P26MD0001» al nuevo responsable (consecutivo, tipo, motivo, solicitante, fecha límite, nota y enlace al caso) |
| Deja el caso sin responsable | Nada |

También avisa cuando un gestor **crea** un caso ya asignado a otra persona. Si el correo
no se puede encolar, la asignación igual queda guardada.

## 2. Temporizadores de la organización

Se configuran en el panel de configuración de PQRSF y valen para toda la organización
(se guardan en base de datos, sobreviven a reinicios). Por ahora los cambia cualquier
gestor de PQRSF; los permisos por rol vendrán después.

| Temporizador | Hora por defecto | Destinatarios | Contenido |
|---|---|---|---|
| `daily_summary` | 17:30 | Cada miembro activo del área de Calidad | «Quedaron N casos abiertos sin resolver el día de hoy», cuántos están vencidos y la lista |
| `overdue_reminder` | 07:30 | Cada responsable con casos vencidos | **Un correo por persona** que agrupa todos sus casos vencidos. Los vencidos sin responsable van, en ese mismo correo, a todo Calidad |

- Solo corren en **días hábiles**: lunes a viernes sin festivos. Los festivos salen de la
  API pública [Nager.Date](https://date.nager.at) (`PQRSF_HOLIDAYS_API_URL`), con caché por
  año; si la API falla, responde el calendario local de la librería `holidays` y se
  reintenta la API a los 10 minutos.
- La hora es local (`PQRSF_TIMEZONE`, `America/Bogota`).
- Cada temporizador corre **una vez por día** (`pqrsf_job_runs`). Si la API estuvo caída a
  la hora programada, se ejecuta al volver dentro de las 2 horas siguientes; pasado ese
  margen se omite el día para no enviar un informe viejo.
- Cada correo programado tiene una clave (`daily_summary:<fecha>:<usuario>`), por lo que
  nunca se duplica aunque el trabajo se repita.

### API

- `GET /pqrsf/settings/notifications`
- `PUT /pqrsf/settings/notifications` con
  `{"daily_summary": {"enabled": true, "time": "17:30"}, "overdue_reminder": {"enabled": true, "time": "07:30"}}`.
  `time` es `HH:MM` de 24 horas; otro formato responde 422. La respuesta incluye
  `timezone` y `business_days_only`.

## 3. Entrega

Los correos pasan por una cola (`pqrsf_notifications`) y un worker los envía cada 30
segundos. Si Graph falla se reintenta a 1 min, 5 min, 30 min y 2 h; después queda en
`failed`. Los textos están en `notification_templates.py` (texto plano); pasar a
plantillas HTML solo cambia ese archivo.

## Configuración

| Variable | Uso |
|---|---|
| `PQRSF_NOTIFICATIONS_ENABLED` | Enciende la cola, el worker y los avisos de asignación |
| `PQRSF_UI_BASE_URL` | Raíz de la UI para los enlaces (vacío = sin enlaces) |
| `PQRSF_HOLIDAYS_API_URL` | API de festivos |
| `PQRSF_NOTIFY_REDIRECT` | **Pruebas**: envía todo a esta dirección, con el asunto `[PRUEBA para <destinatario>]` |

Los destinatarios de Calidad son los miembros del área `PQRSF_MANAGER_AREA_SLUG`.
