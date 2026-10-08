# PQRSF por correo

Cualquier correo que llegue a `pqrsf@jdh.com.co` se convierte en un caso de
seguimiento (origen `email`). Las respuestas del cliente dentro del mismo hilo se
agregan al caso mientras esté abierto. Funciona con Microsoft Graph y permisos de
aplicación: **no se usan contraseñas de buzón**.

## Plantilla de la solicitud

Texto plano, un `Clave: valor` por línea y la descripción al final:

```
Tipo: Petición | Queja | Reclamo | Sugerencia | Felicitación
Sede: (sede o sucursal)
Nombre: (quien solicita)
Razón social: (empresa)
NIT: (de la empresa)
Motivo: (resumen corto)
Descripción:
(todo lo que sigue es la descripción, en las líneas que se necesiten)
```

- Las claves no distinguen mayúsculas ni tildes y admiten sinónimos
  (`Razón social`/`Empresa`, `Descripción`/`Detalle`, `Motivo`/`Asunto`…).
- Solo la descripción es necesaria. Un correo sin plantilla **igual abre el caso**
  (tipo Petición, descripción = cuerpo, motivo = asunto) y queda marcado
  `needs_review` para que un gestor lo revise; el acuse incluye la plantilla.
- `Tipo` y `Sede` se comparan por nombre, código o abreviatura. Si no coinciden, se
  usan los valores por defecto y el caso se marca para revisión.
- El historial citado de una respuesta (`> …`, `El … escribió:`) no se guarda.

## Qué hace cada correo

| Situación | Resultado |
|---|---|
| Correo nuevo | Caso nuevo, acuse con el consecutivo (respuesta en el mismo hilo) |
| Misma conversación (o consecutivo en el asunto) y caso abierto | Evento `email_received` en la línea de tiempo; no hay acuse |
| Misma conversación y caso **finalizado** | Caso nuevo con `related_case` apuntando al anterior (no se reabre, para no distorsionar los tiempos de respuesta) |
| Enviado por `pqrsf@`/`info@`, `Auto-Submitted`, `Precedence: bulk`, `mailer-daemon`, `no-reply` | Ignorado (evita bucles) |

Cada mensaje queda en `pqrsf_email_messages` con su `Internet-Message-Id` único: un
correo entregado dos veces nunca abre dos casos.

## Adjuntos

- Se aceptan las extensiones de `MAIL_ATTACHMENT_EXTENSIONS`; hay un máximo por
  correo, por archivo y en total.
- Imágenes: se revalidan y recodifican (sin metadatos). Documentos: se comprueba que
  el contenido corresponda a la extensión. Todo pasa por ClamAV.
- Se guardan en el bucket como `pqrsf/cases/<caso>/<adjunto>.<ext>` y su metadato
  en `pqrsf_case_attachments`. Las imágenes incrustadas (logos de firma) se omiten.
- Un archivo rechazado no impide el caso: queda un evento `email_received` con
  `rejected_files` y el motivo. Si ClamAV o el bucket no responden, el mensaje se
  reintenta en la siguiente pasada sin duplicar el caso ni el acuse.
- `GET /pqrsf/cases/{id}/files` y `GET /pqrsf/cases/{id}/files/{file_id}/download`
  (mismas reglas de acceso que el caso; siempre como descarga).

## Sincronización

Un worker del backend (una sola instancia) consulta el buzón cada
`MAIL_POLL_INTERVAL_SECONDS` con *delta query* de Graph y guarda el cursor en
`pqrsf_mail_sync_state`; no hay suscripciones que renovar ni URL pública. Si el
cursor caduca (410) se lista la bandeja de nuevo; la deduplicación evita repetidos.
Un mensaje fallido se reintenta hasta `MAIL_MAX_ATTEMPTS` veces y queda en `failed`.

**Primer arranque:** solo se toman los correos recibidos desde que se enciende
(o desde `MAIL_INTAKE_SINCE`); la bandeja anterior no se convierte en casos.

## Configuración

Ver `.env.example` (`MAIL_*`). Está apagado por defecto (`MAIL_INTAKE_ENABLED=false`);
al encenderlo se exige tenant, `MAIL_GRAPH_CLIENT_ID` y `MAIL_GRAPH_CLIENT_SECRET`
(el **valor** del secreto, no su ID).

Permisos de la app en Entra, tipo **Application** con consentimiento de
administrador: `Mail.Read` y `Mail.ReadWrite` (marcar como leído y categorizar) y
`Mail.Send` (responder). Limitarla a los buzones con una *Application Access Policy*
de Exchange, o la app podría leer y enviar como cualquier usuario del tenant.

## Pendiente (siguientes entregas)

- Responder desde la plataforma dentro del hilo (`/messages/{id}/reply`) y mostrar el
  hilo junto a la línea de tiempo del caso.
- Sincronizar *Elementos enviados* para reflejar respuestas hechas desde Outlook.
- Evento en tiempo real al crearse un caso por correo.
- Aviso de cierre desde `info@`.
