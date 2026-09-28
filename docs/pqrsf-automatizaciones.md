# Automatizaciones propuestas para PQRSF

Hoy el seguimiento vive en correo: el ejecutivo comercial pide el documento
por email, Calidad lo busca, lo reenvía, y el Excel se actualiza a mano. El
módulo de seguimiento ([`docs/pqrsf-seguimiento.md`](pqrsf-seguimiento.md))
ya registra cada cambio como evento (`pqrsf_case_events`), así que cada
automatización de este documento se dispara desde esos eventos, no desde
lógica nueva en los endpoints.

Todo lo que sigue está diseñado pero **no implementado**. El orden es el de
implementación recomendado.

## Base común: bandeja de salida y envío por Microsoft Graph

La plataforma ya usa Entra ID, así que el correo sale por Microsoft Graph
desde un buzón compartido (por ejemplo `pqrsf@jdh.com.co`), no por SMTP.

1. **Outbox transaccional** `pqrsf_notifications`: `id`, `case_id`,
   `event_id`, `channel` (`email` | `teams`), `template`, `recipients`
   (JSONB), `payload` (JSONB), `status` (`pending` | `sent` | `failed`),
   `attempts`, `next_attempt_at`, `sent_at`, `error`. Se escribe **en la misma
   transacción** que el evento del caso: si el caso se guarda, la notificación
   existe; si no, tampoco. Nunca se envía correo desde el request HTTP.
2. **Worker**: proceso aparte (servicio `pqrsf-worker` en Compose, mismo
   código) que toma lotes con `SELECT … FOR UPDATE SKIP LOCKED`, envía y
   reintenta con espera exponencial (1, 5, 30 min, 2 h; luego `failed` visible
   en la UI de gestores).
3. **Graph**: permiso de aplicación `Mail.Send` restringido al buzón PQRSF con
   una *Application Access Policy* de Exchange (así la app no puede enviar como
   cualquier usuario). Mismo tenant y app registration de Entra, secreto aparte.
4. **Plantillas** en el repositorio (Jinja2, HTML + texto plano), en español,
   con el consecutivo en el asunto: `[P-2026-00012] …`. Ese marcador es el que
   permite enlazar respuestas (ver A5).
5. **Preferencias**: tabla `pqrsf_notification_settings` por evento
   (activo/inactivo, destinatarios extra) editable por gestores. Arranca con
   todo activo excepto el resumen semanal.

Costo estimado de la base: 3–4 días. Cada automatización posterior: 0,5–2 días.

## A1. Acuse de recibo al cliente con consecutivo

- **Disparador**: evento `created` con `origin = public_form`.
- **Destinatario**: `requester_email` de la solicitud.
- **Contenido**: consecutivo, tipo, fecha de recepción, fecha límite de
  respuesta (`due_on`) y canal para responder (responder el correo).
- **Resuelve**: hoy el cliente no recibe constancia; el consecutivo es lo que
  citará en adelante y reduce llamadas de «¿recibieron mi solicitud?».
- **Nota**: felicitaciones reciben un agradecimiento sin fecha límite.

## A2. Aviso de asignación al responsable

- **Disparador**: evento `assigned` con `to_user_id`.
- **Destinatario**: el nuevo responsable (correo y, opcional, chat de Teams).
- **Contenido**: consecutivo, cliente, documentos pedidos con su fecha límite,
  botón al detalle del caso en la plataforma.
- **Efecto encadenado**: al abrir el enlace, la UI llama `/read` y el caso
  pasa a «Leída». Ese dato ya mide el tiempo de reacción del responsable.

## A3. Recordatorios y escalamiento por vencimiento

Tarea programada diaria (7:00 a. m. hora Bogotá, solo días hábiles con el
mismo calendario del SLA):

| Momento | Destinatario | Mensaje |
|---|---|---|
| 1 día hábil antes de `due_on` | Responsable | «Vence mañana» con documentos pendientes |
| Día de `due_on` | Responsable | «Vence hoy» |
| 1 día hábil después | Responsable + gestores (área Calidad) | «Vencido», con días de retraso |
| Caso sin asignar > 4 horas hábiles | Gestores | «Casos sin responsable» |

- Se calcula sobre documentos (`pqrsf_case_items.due_on` sin
  `responded_on`), no solo sobre el caso, porque el Excel mide por documento.
- Idempotencia: clave única (`case_id`, `item_id`, `tipo_recordatorio`) en el
  outbox para no repetir avisos si la tarea corre dos veces.

## A4. Resumen diario y semanal

- **Diario** (responsable): sus casos abiertos ordenados por vencimiento, en
  un solo correo, en lugar de un correo por caso.
- **Semanal** (gestores, lunes): indicadores de `/cases/summary` más
  cumplimiento de SLA de la semana (documentos a tiempo / total), top de
  causales de retraso y top de tipos de documento. Es el reporte que hoy se
  arma a mano desde el Excel.

## A5. Buzón de entrada: correo → caso y respuestas → línea de tiempo

La automatización de mayor impacto, porque elimina la transcripción.

- **Suscripción Graph** (*change notifications*) sobre el buzón PQRSF; si la
  red no permite webhooks entrantes, *polling* delta cada 2 minutos.
- **Correo nuevo sin consecutivo en el asunto** → caso `origin = email` en
  «Recibida», sin asignar, con remitente como `requester_email`, asunto como
  `subject` y cuerpo como `description`. Los adjuntos pasan por el mismo
  saneamiento y ClamAV del formulario público.
- **Correo con `[P-2026-00012]` en el asunto** → comentario en la línea de
  tiempo de ese caso (y sus adjuntos). El responsable ve la respuesta del
  cliente o del proveedor sin salir de la plataforma.
- **Clasificación asistida** (opcional, fase 2): proponer tipo de solicitud,
  tipos de documento y cliente leyendo el correo con un modelo de lenguaje; la
  persona confirma. Nunca se asigna ni se cierra solo con esa sugerencia.
- Requiere agregar `email` a `ck_pqrsf_cases_origin` y un campo
  `source_message_id` único para no crear dos casos del mismo correo.

## A6. Responder al cliente desde la plataforma con el documento adjunto

- En el detalle del caso, acción «Responder»: plantilla con el consecutivo,
  texto editable y adjuntos elegidos **del gestor documental** (módulo
  `documents`, donde Calidad ya guarda certificados, fichas técnicas y
  conceptos sanitarios) o subidos en el momento.
- Al enviar: se registra el evento, se marca `responded_on = hoy` en los
  documentos adjuntados y, si ya no quedan pendientes, se propone cerrar.
- **Resuelve**: hoy se busca el archivo, se adjunta en Outlook y luego se
  actualiza el Excel; aquí es un solo paso y la fecha de respuesta es exacta.

## A7. Enrutamiento automático de asignación

- Reglas en `pqrsf_assignment_rules`: condición (tipo de solicitud, tipo de
  documento, dependencia, sede) → responsable o grupo. Primera regla que
  aplica gana; sin regla, queda para gestores.
- Modo grupo con **balanceo por carga**: asigna a quien del grupo tenga menos
  casos abiertos. Evita que un responsable concentre el 40 % (hoy Cristian
  Camilo Garzón tiene 944 de 2.720 casos del histórico).
- Registra el evento `assigned` con `actor = null` y nota «Asignación
  automática: regla X», de modo que A2 se dispara igual.

## A8. Solicitudes a proveedores con seguimiento

Buena parte de los retrasos del histórico tienen como causal al proveedor
(«El proveedor nunca dio una respuesta formal», fichas técnicas pendientes).

- Acción «Pedir al proveedor» en un documento: correo al contacto del
  proveedor (catálogo `pqrsf_supplier_contacts`) con el consecutivo en el
  asunto; su respuesta entra por A5 a la línea de tiempo.
- Recordatorio automático al proveedor a los 2 días hábiles sin respuesta.
- Si el documento se responde tarde, la causal se precarga como «Pendiente de
  proveedor (solicitado el dd/mm)», editable.

## A9. Documentos generados desde el DWH

- **Carta de garantía (relación de lotes despachados)**: 1.176 de los 3.901
  documentos del histórico. Se arma con datos de despachos (cliente, factura,
  lote, fecha); se puede generar el PDF desde el DWH con la plantilla oficial y
  dejarlo listo para A6.
- **Certificado de calidad por lote** (1.678 documentos): si el certificado
  del fabricante está en el gestor documental indexado por producto/lote, se
  propone automáticamente.
- Con estos dos, casi el 73 % de los documentos pedidos podría responderse el
  mismo día.

## A10. Encuesta de satisfacción al cierre

- **Disparador**: estado final en casos con `requester_email` (formulario o
  correo).
- Enlace público de una sola pregunta (1–5) y comentario opcional, firmado con
  token de un solo uso; el resultado se guarda como evento del caso.
- Alimenta el indicador de satisfacción del sistema de gestión (ISO 9001).

## A11. Consulta pública de estado

- Página pública `/pqrsf/estado` donde el cliente ingresa consecutivo + NIT y
  ve estado y fecha límite (sin comentarios internos). Mismo rate limiting
  estricto del formulario.
- Reduce los correos de «¿cómo va mi solicitud?».

## Priorización sugerida

| Fase | Automatizaciones | Por qué primero |
|---|---|---|
| 1 | Base + A1 + A2 + A3 | Visibilidad inmediata del SLA; poco riesgo |
| 2 | A5 + A6 | Elimina transcripción y reenvío manual |
| 3 | A7 + A4 | Reparte carga y reemplaza el reporte manual |
| 4 | A8 + A9 + A10 + A11 | Ataca las causales de retraso y cierra el ciclo con el cliente |

## Métricas para medir el impacto

- Tiempo hasta «Leída» (asignación → `first_read_at`).
- % de documentos respondidos dentro del tiempo establecido.
- Casos creados por correo vs. digitados.
- Días de retraso promedio por causal y por proveedor.
- Satisfacción promedio (A10).
