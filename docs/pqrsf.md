# PQRSF

Documento único del módulo PQRSF (peticiones, quejas, reclamos, sugerencias y felicitaciones):
qué es, cómo funciona la API, cómo la consume la interfaz (`internal-platform-ui`), qué está
listo, qué falta y qué preguntas siguen abiertas. Reemplaza los documentos `pqrsf-*.md` que
existían por separado.

Contenido:

1. [Estado actual](#1-estado-actual)
2. [Negocio: definiciones y flujo](#2-negocio-definiciones-y-flujo)
3. [Arquitectura y datos](#3-arquitectura-y-datos)
4. [Formulario público](#4-formulario-público)
5. [Casos y seguimiento](#5-casos-y-seguimiento)
6. [Panel interno (UI)](#6-panel-interno-ui)
7. [Correo entrante](#7-correo-entrante)
8. [Correos salientes: avisos, respuestas y temporizadores](#8-correos-salientes-avisos-respuestas-y-temporizadores)
9. [Mensajes al proveedor](#9-mensajes-al-proveedor)
10. [Documentos y certificados](#10-documentos-y-certificados)
11. [Sugerencias y felicitaciones](#11-sugerencias-y-felicitaciones)
12. [Ejecutivo comercial](#12-ejecutivo-comercial)
13. [Seguridad del formulario público](#13-seguridad-del-formulario-público)
14. [Configuración](#14-configuración)
15. [Desarrollo local y pruebas](#15-desarrollo-local-y-pruebas)
16. [Hoja de ruta](#16-hoja-de-ruta)
17. [Preguntas abiertas y riesgos](#17-preguntas-abiertas-y-riesgos)

---

## 1. Estado actual

Checkpoint del **26 de octubre de 2026**.

### Entregas abiertas

| Repo | PR | Rama | Contenido |
|---|---|---|---|
| API | [#28](https://github.com/JDHTI/JDH-INTERNAL-PLATFORM-API/pull/28) | `feat/module-pqrsf-classification` (local `Lovealone1/pqrsf-updates`) | Formulario clasificado, administración de asuntos y causales, área, productos, videos, línea de tiempo legible, mensajes al proveedor, ejecutivo comercial |
| UI | [#35](https://github.com/JDHTI/JDH-INTERNAL-PLATFORM/pull/35) | `feat/pqrsf-classification` | Lo mismo del lado de la interfaz. Depende del PR #28 |

Antes de fusionar el PR #28 se fusionó `master` y se agregó la migración `0028` para unir esta
cadena con `0024_app_monitoring` (una sola cabeza). Pasan 831 pruebas y ruff.

### Qué funciona hoy

- **Formulario público** clasificado: tipo con descripción, sede, área, asunto, causal,
  productos con proveedor resuelto, orden de recogida, fotos y un video.
- **Solicitudes de documentos** con respuesta automática de los certificados configurados.
- **Casos** con radicado por sede, estados, SLA en días hábiles, responsables, comentarios con
  archivos, respuesta parcial al cliente y línea de tiempo legible.
- **Correo entrante** a `pqrsf@jdh.com.co` que abre casos o se suma a su línea de tiempo.
- **Mensajes al proveedor** desde el caso, con su respuesta de vuelta en la línea de tiempo.
- **Ejecutivo comercial** tomado de la factura en el DWH.
- **Sugerencias y felicitaciones** aparte, con agradecimiento automático.
- **Avisos** de asignación y temporizadores (resumen diario, recordatorio de vencidos).

### Entorno local (Docker)

| Servicio | Dirección | Notas |
|---|---|---|
| API | `localhost:8000` | Construida desde el worktree `internal-platform-api/pqrsf-updates`. Usa la base de prueba `internal_platform_pqrsf` mediante un override de Compose (fuera del repo) |
| UI | `localhost:3000` (`/pqrsf` es el formulario) | Construida desde el worktree `internal-platform-ui/ui-features` con `docker compose -p internal-platform up -d --build` |
| MinIO | `localhost:9004` / `9005` | Publicado con `S3_HOST_PORT=9004` y `S3_CONSOLE_HOST_PORT=9005` |

- **Base de prueba:** `internal_platform_pqrsf` tiene el maestro de productos cargado (27.792 productos) y un solo usuario.
- **Base compartida:** `internal_platform` estaba en `0024_app_monitoring`. Con la `0028` ya se puede migrar.
- **Correo:** las llaves de Graph del buzón están en el `.env` local, pero `MAIL_INTAKE_ENABLED` y `PQRSF_NOTIFICATIONS_ENABLED` siguen apagados.

### Pendientes inmediatos

- **Calidad:** entregar la matriz oficial de asuntos y causales para reemplazar el sembrado
  provisional, que se hace desde Configuración.
- **Factura impresa:** confirmar con 2 o 3 facturas físicas que el número impreso de la factura
  electrónica coincide con `nro_transaccion` del DWH (sección 12).
- **Panel interno:** reclasificación, filtros nuevos y formulario del comercial (etapas I-3 a
  I-6 de la sección 16). La clasificación y los productos en el caso (I-1, I-2) ya están.
- **UI sin consumir:** certificados, solicitudes de documentos respondidas al instante y
  respuesta de un documento con archivo (las APIs de la sección 10).
- **Respaldo del ejecutivo comercial:** usar el vendedor asignado al cliente cuando la factura no
  aparezca.

---

## 2. Negocio: definiciones y flujo

Origen: reunión de planificación con Calidad del 22 de octubre de 2026
(`2026-10-22_planificacion-pqrsf-definicion-y-gestion-documental.md`, en la raíz del checkout
principal) y decisiones posteriores con el equipo.

### Qué se busca

Que **toda** novedad de un cliente, proveedor o parte interesada (incluidas las que hoy se
quedan en correos y en órdenes de recogida de CIESA) entre por un solo canal parametrizado, se
enrute sola al área que debe actuar, se trate según su naturaleza (proveedor o fallo de proceso
interno), mantenga informado al cliente, cierre con una respuesta formal y, cuando hubo fallo de
proceso, alimente una matriz de planes de acción cuya eficacia se pueda medir. La norma ISO con
la que JDH está certificada exige ese histórico.

### Tipos

La descripción de cada tipo se muestra siempre visible bajo la opción, en una línea de máximo
120 caracteres, sin tooltip. Calidad la cambia sin despliegue.

| Tipo | Descripción en el formulario | Abre caso |
|---|---|---|
| Petición | Solicitudes a JDH: documentos, visitas, auditorías y otras relacionadas | Sí |
| Queja | Novedad en el momento de la entrega, antes de firmar la factura o cerrar el día | Sí |
| Reclamo | Novedad después de la entrega: factura firmada a satisfacción o ya pasó tiempo | Sí |
| Sugerencia | Propuesta para mejorar nuestros procesos o el servicio | No (sección 11) |
| Felicitación | Reconocimiento a una acción destacada o a un servicio excepcional | No (sección 11) |

Distinguir queja de reclamo es lo que más importa:
- **Queja:** la novedad ocurre durante la entrega, mientras el auxiliar sigue en el sitio. Es
  urgente.
- **Reclamo:** la novedad es posterior a la entrega.

Según la norma, una queja o un reclamo es una **salida no conforme (SNC)**. JDH unificó la matriz
de SNC y la de PQR, así que en conversación se usan como sinónimos.

### Asunto, causal y área

- **Asunto:** la novedad de la tabla de canales de comunicación del procedimiento, por ejemplo
  calidad del producto, comportamiento en la entrega, tiempo de entrega, presentación personal,
  servicio al cliente, documentos del producto o facturación.
- **Causal:** depende del asunto, por ejemplo producto vencido, corta fecha, trocado, error en
  cargue, sin EPP…
- **Ruta:** la causal la define. `supplier` va al proveedor (calidad del fabricante), `internal`
  es un fallo de proceso de JDH y `none` no tiene ruta.
- **Área que atiende el caso:** la decide el servidor en este orden: causal > asunto > categoría
  de los documentos pedidos > área elegida por el cliente.
- **Área elegida por el cliente:** se elige junto a la sede y filtra los asuntos que ve. Se
  guarda aparte, porque el área que atiende la puede corregir Calidad.

El catálogo sembrado (10 asuntos, 21 causales) sale de la reunión y es **provisional**.

### Términos

| Término | Significado |
|---|---|
| OM, oportunidad de mejora | Formato con el que Calidad escala al proveedor una novedad de calidad de fabricante |
| ACPM | Acción correctiva y preventiva: acciones inmediatas, técnicas de análisis y planeación. Para fallos de proceso interno |
| Orden de recogida (OR) | Pedido de devolución que el comercial monta en CIESA con un motivo |
| Motivo de devolución | Código de CIESA: 14 error en cargue, 20 calidad del producto (aprueba Calidad), 22 corta fecha o vencido. Los distintos de 20 los aprueba Abastecimiento e Inventarios |
| Nota crédito (NC) | Lo que se emite tras recoger; JDH casi nunca repone producto |
| Dueño de proceso | Responsable de la dependencia y su homólogo en la sede donde ocurrió la novedad |
| Radicado | Consecutivo exigido por ley (`Q26AC0012`) |

### Flujo completo (objetivo)

```
Radicación (formulario público, formulario interno del comercial o correo con plantilla)
  │  tipo · área · asunto · causal · productos · evidencias
  ▼
Caso con radicado ─► acuse al solicitante + aviso a la dependencia (urgente en quejas)
  ├─ Petición de documentos con certificado ─► respuesta automática
  ▼
Calidad valida la evidencia y confirma o reclasifica
  ├─ RUTA PROVEEDOR: mensaje / OM al proveedor ─► su respuesta vuelve al caso
  │                  si no procede: decide el director del canal
  ├─ RUTA PROCESO INTERNO: remisión al dueño de proceso ─► investigación ─► ACPM ─► plan de acción
  ▼
En paralelo: OR aprobada en CIESA · recogida · NC · verificar y bloquear el lote
  ▼
Respuesta formal al cliente (plantilla por causal; con ACPM si el cliente lo exige)
  ▼
Cierre. El plan de acción sigue en su matriz y se mide su eficacia por recurrencia
```

### Decisiones

| # | Decisión |
|---|---|
| D1 | El formulario sirve a clientes, proveedores y cualquier parte interesada |
| D2 | Asunto y causal son listas parametrizadas; lo que falte se agrega al catálogo |
| D3 | Descripción libre opcional; obligatoria cuando se elige «Otro» |
| D4 | La dependencia se deriva de la causal; el área del cliente solo filtra y desempata |
| D5 | Un caso puede llevar varios productos |
| D6 | Los comerciales radican por el formulario interno, con los mismos campos, y quedan como autor |
| D7 | Producto aplica a queja y reclamo y es opcional (hay quejas sin producto) |
| D8 | Se permiten videos además de fotos; foto directa desde el celular |
| D9 | Sugerencia admite adjuntos; felicitación es texto |
| D10 | Los clientes no tienen acceso a SharePoint/OneDrive, solo al formulario |
| D11 | El gestor documental es de la plataforma y se replica hacia SharePoint |
| D12 | Facturas, órdenes de recogida y NC se leen del DWH |
| D13 | Respuesta formal y ACPM como DOCX editable, enviados en PDF |
| D14 | La matriz de planes de acción es un módulo aparte enlazado al caso |
| D15 | El correo con plantilla sigue siendo un canal válido |
| D16 | Después de «Sede» el cliente elige el área y ve solo las novedades de esa área |
| D17 | La solicitud es evidencia inmutable; el caso es la copia de trabajo que Calidad corrige |
| D18 | El ejecutivo comercial sale de la factura en el DWH, nunca se le pide al cliente |

---

## 3. Arquitectura y datos

### Módulo

`app/modules/pqrsf` sigue la guía de módulos (`docs/module-guideline.md`): `domain` (entidades,
puertos), `application` (casos de uso), `infrastructure` (SQLAlchemy, Graph, ClamAV, DWH) y
`presentation` (routers). Lo de otros módulos se consume por puertos cableados en
`app/v1_0/v1_containers.py`:

| Puerto PQRSF | Implementación | Para qué |
|---|---|---|
| `ProductCatalog` | `MasterProductCatalog` sobre `commercial_masters.MasterLookup` | Buscar productos y resolver SKU → descripción y proveedor |
| `SalesLookup` | `WarehouseSalesLookup` (`consumo.ventas` + `consumo.vendedores`) | Vendedor de una factura |
| `PeopleDirectory` | `identity.UserDirectory` | Usuarios por id y por correo |
| `ManagerPolicy` | `AreaManagerPolicy` | Quién gestiona PQRSF (admin o área `calidad`) |
| `DocumentFileSource` | gestor documental (`documents`) | Archivos de certificados |
| Correo | `mail.GraphMailClient` | Leer y enviar desde el buzón |

### Tablas principales

| Tabla | Contenido |
|---|---|
| `pqrsf_request_types` | Los 5 tipos: prefijo, SLA, `handling` (`case` / `acknowledge`), `description`, `allows_attachments`, `asks_area` |
| `pqrsf_requests` | Lo que envió el cliente (inmutable): datos, asunto, causal, área, `products` (JSON), orden de recogida, documentos pedidos |
| `pqrsf_attachments` | Fotos y video de la solicitud |
| `pqrsf_subjects`, `pqrsf_causes` | Catálogo de asuntos y causales con dependencia y ruta |
| `pqrsf_cases` | El caso: radicado, estado, responsable, dependencia, cliente, proveedor, ejecutivo comercial, fechas; clasificación (área elegida, asunto, causal, ruta), orden de recogida y fecha de entrega |
| `pqrsf_case_products` | Productos del caso con proveedor (`source`: `request`, `internal`, `email`); no se editan |
| `pqrsf_case_items` | Documentos pedidos en el caso con su SLA |
| `pqrsf_case_events` | Línea de tiempo e historial del módulo (solo inserción) |
| `pqrsf_case_attachments` | Archivos del caso (correo, comentarios, respuestas) |
| `pqrsf_case_statuses`, `pqrsf_dependencies`, `pqrsf_document_types`, `pqrsf_document_categories` | Catálogos configurables |
| `pqrsf_email_messages`, `pqrsf_mail_sync_state` | Correo entrante |
| `pqrsf_notifications`, `pqrsf_settings`, `pqrsf_job_runs` | Cola de correos salientes y temporizadores |
| `pqrsf_feedback`, `pqrsf_feedback_files` | Sugerencias y felicitaciones |
| `pqrsf_certificates`, `pqrsf_document_deliveries`, `pqrsf_item_deliveries` | Certificados y respuestas de documentos |
| `pqrsf_supplier_contacts` | Correos de proveedor ya usados |

### Migraciones del módulo

| Migración | Qué hace |
|---|---|
| `0006`–`0008` | Sedes, solicitudes y adjuntos |
| `0010`–`0013` | Seguimiento, consecutivos por sede, historial, solicitud de documentos |
| `0016`–`0021` | Correo, notificaciones, respuestas al cliente, sugerencias, certificados, respuestas de documentos |
| `0024_pqrsf_classification` | Descripción de tipos, asuntos y causales sembrados, productos, orden de recogida; adjuntos en sugerencia |
| `0025_pqrsf_request_area` | `asks_area` y área de solicitudes y sugerencias |
| `0026_pqrsf_supplier_contacts` | Contactos de proveedor |
| `0027_pqrsf_sales_executive` | Código, correo, usuario y origen del ejecutivo comercial |
| `0028_merge_pqrsf_app_monitoring` | Une la cadena de PQRSF con `0024_app_monitoring` |
| `0038_pqrsf_case_classification` | Clasificación, orden de recogida y fecha de entrega en el caso; `pqrsf_case_products`; `delivery_date` en la solicitud. Copia lo de las solicitudes a los casos existentes |

---

## 4. Formulario público

### Interfaz

- Ruta `/pqrsf` en `src/app/(public)/pqrsf`, fuera del layout autenticado: sin sidebar, header
  ni sesión.
- Las llamadas usan `credentials: "omit"`.
- Código en `src/features/pqrsf-public`:
  - `public-pqrsf-form.tsx` (formulario);
  - `choice-list.tsx` (tipos con descripción);
  - `product-lines-field.tsx` (productos);
  - `attachments-field.tsx` (fotos y video, con `capture` para la cámara);
  - `document-request-fields.tsx`.
- Orden de los campos:
  1. tipo;
  2. sede y área (si el tipo la pide);
  3. asunto y causal, filtrados por área;
  4. productos y orden de recogida, si la causal los admite;
  5. descripción;
  6. evidencias;
  7. datos del solicitante.
- Al enviar se muestra el radicado (`tracking_code`), o el agradecimiento en sugerencia y
  felicitación.

### Endpoints

| Método | Ruta | Nivel de límite |
|---|---|---|
| `GET` | `/api/v1/public/pqrsf/catalogs` | `read` |
| `GET` | `/api/v1/public/pqrsf/products?q=&limit=` | `read` |
| `POST` | `/api/v1/public/pqrsf/requests` | `strict` (+ `PublicIntakeGuard`) |

### Catálogo

`GET /public/pqrsf/catalogs` devuelve:

- `request_types[]`: `id`, `code`, `name`, `description`, `allows_document_request`,
  `allows_attachments`, `asks_area`, `handling`.
- `sites[]`: `id`, `code`, `name`.
- `dependencies[]`: todas las áreas activas (`id`, `code`, `name`).
- `subjects[]`: `id`, `code`, `name`, `description`, `request_type_codes`, `dependency_id`,
  `is_other` y `causes[]` (`id`, `code`, `name`, `description`, `is_other`, `allows_product`,
  `requires_product`).
- `document_categories[]`: con sus `documents[]` (`id`, `name`, `automatic`).
- `limits`: `max_images`, `max_image_mb`, `max_total_image_mb`, `image_extensions`,
  `max_videos`, `max_video_mb`, `video_extensions`.

La dependencia y la ruta de cada causal no se publican: las decide el servidor.

### Reglas de la interfaz

- **Asuntos:** se ofrecen los que incluyen el código del tipo elegido y son del área elegida.
  Los `is_other` se muestran en todas las áreas.
- **Asunto escrito:** sugerencia, felicitación y solicitud de documentos no tienen asuntos y
  siguen pidiendo `subject` escrito.
- **Causal:** con asunto elegido se muestran sus causales; si no tiene, no se pide causal.
- **«Otro»:** `is_other` en asunto o causal hace obligatoria la descripción; conviene pedir
  también un `subject` corto.
- **Productos:** `allows_product` muestra la sección y `requires_product` exige al menos una
  línea.
- **Búsqueda de productos:** `GET /products?q=` busca por SKU exacto o parte del nombre
  (mínimo 2 caracteres, hasta 20 resultados). Nunca devuelve proveedor ni costos.

### Envío

`POST /public/pqrsf/requests` en `multipart/form-data`, con un UUID nuevo por intento lógico en
`Idempotency-Key`:

| Campo | Regla |
|---|---|
| `request_type_id`, `site_id` | Obligatorios |
| `full_name`, `business_name`, `nit`, `address`, `department`, `city`, `email` | Obligatorios |
| `dependency_id` | Obligatorio si el tipo tiene `asks_area` (petición, queja, reclamo, sugerencia); debe ser una de `dependencies`; se rechaza en los demás |
| `subject_id`, `cause_id` | Obligatorios en los tipos con asuntos (queja, reclamo, petición que no es de documentos). La causal debe ser del asunto |
| `subject` | Texto, máximo 200. Obligatorio sin asuntos y en solicitud de documentos; con «Otro» complementa |
| `description` | Obligatoria en sugerencia, felicitación y con «Otro»; opcional en lo demás |
| `products` | JSON `[{"invoice_number", "sku", "lot", "expiry_date": "AAAA-MM-DD", "affected_quantity"}]`, máximo 20 líneas, solo si la causal lo admite. El SKU debe existir en el maestro |
| `pickup_order_requested` | `yes` / `no` / `unknown`, obligatorio con productos; `pickup_order_number` (máximo 40) solo con `yes` |
| `delivery_date` | `AAAA-MM-DD`, fecha de entrega del pedido. Obligatoria con productos, no futura (hora de Bogotá); se rechaza sin productos |
| `document_request`, `document_category_id`, `document_type_ids`, `document_other` | Solicitud de documentos (sección 10) |
| `images` | Fotos, en tipos con `allows_attachments`, nunca en solicitud de documentos |
| `videos` | Igual que `images`; por defecto 1 video MP4/MOV/WebM de hasta 20 MB |

Respuesta `201`: `{ id, reference, tracking_code, handled_as, delivered_documents, created_at }`.
- `handled_as`:
  - `case`: abrió caso, y `tracking_code` es el radicado que el cliente debe citar;
  - `feedback`: sugerencia o felicitación, sin radicado;
  - `documents`: todo respondido con certificados.
- `tracking_code` puede ser `null` si el caso no se pudo abrir en ese momento.
- Errores:
  - `422` validación, con mensaje en español;
  - `413` tamaño;
  - `409` clave de idempotencia reutilizada con otros datos;
  - `429` límite de envíos;
  - `503` Redis, S3 o ClamAV caídos.

### Qué hace el servidor al recibirla

1. **Valida** tipo, sede, área, asunto, causal y productos. Resuelve la descripción y el
   proveedor de cada SKU en el maestro comercial.
2. **Sanea y analiza los adjuntos** (sección 13) y guarda la solicitud.
3. **Decide el destino:**
   - sugerencia o felicitación: se guardan aparte (sección 11);
   - solicitud de documentos: se envían los certificados (sección 10);
   - lo demás: abre caso.
4. **Arma el caso:**
   - asunto «Asunto · Causal»;
   - dependencia: causal > asunto > categoría > área elegida;
   - proveedor(es) tomados de los productos;
   - ejecutivo comercial tomado de la factura (sección 12);
   - clasificación (área elegida, asunto, causal y la ruta de la causal), orden de recogida,
     fecha de entrega y productos, copiados al caso.
5. **Tolera fallas:** si el caso no se abre, la solicitud queda guardada y un admin la recupera
   con `POST /pqrsf/requests/{id}/case`.

### Endpoints administrativos de solicitudes

Solo admin, con sesión:
- `GET /pqrsf/requests`;
- `GET /pqrsf/requests/{id}`;
- `GET /pqrsf/requests/{id}/attachments/{attachment_id}/download`;
- `POST /pqrsf/requests/{id}/case`.

El detalle (y el `request` del detalle del caso) trae `selected_area`, `selected_subject`,
`selected_cause`, `products`, la orden de recogida y `delivery_date`.

---

## 5. Casos y seguimiento

Reemplaza el Excel P.13.F.01 «Seguimiento Peticiones». La solicitud pública queda inmutable y
cada una abre un **caso** (1:1) con consecutivo, responsable, estado, documentos con SLA y línea
de tiempo. También hay casos internos, importados del Excel y abiertos por correo
(`origin`: `public_form`, `internal`, `import`, `email`).

### Consecutivos

- **Formato:** `{prefijo}{año:2}{sigla de sede}{contador:04d}`, por ejemplo `Q26AC0012`.
- **Contador:** uno por tipo, año y sede, reservado en la misma transacción. No hay duplicados
  ni huecos.
- **Sigla:** sale de `pqrsf_sites.abbreviation`. Sin sede se usa la serie `GN`. La serie queda
  fija al abrir: cambiar la sede después no renumera.
- **Prefijo:** P/Q/R/S/F, configurable por tipo. Cambiarlo no reescribe los radicados ya
  emitidos.
- **Importados:** conservan el número del Excel en `legacy.original_consecutive`, y la búsqueda
  también lo encuentra.

### Estados

Catálogo configurable (`code`, `name`, `color`, `sort_order`, `is_initial`, `is_terminal`,
`is_active`). Sembrados: `recibida` (inicial), `leida`, `en_curso` y `finalizada` (final).

| Regla | Detalle |
|---|---|
| Leída automática | `POST /cases/{id}/read` al abrir el detalle: si quien abre es el responsable y es la primera vez, fija `first_read_at` y pasa de inicial a `leida` |
| En curso automática | Al registrar la primera respuesta de un documento, `recibida`/`leida` pasa a `en_curso` |
| Cerrar | Exige respuesta en todos los documentos y causal en los tardíos. Fija `closed_at` |
| Reabrir | Exige nota. Un caso cerrado es de solo lectura salvo comentarios y estado |

### Tiempos de respuesta (SLA)

- **Calendario:** días hábiles con festivos de Colombia, en `America/Bogota`. Responder el mismo
  día de recepción cuenta como 1 día.
- **Plazo de cada documento:** toma el tiempo de su tipo (62 tipos sembrados del Excel) y lo
  congela al crearse. `due_on` = recepción + ese tiempo.
- **Plazo del caso:** el mayor `due_on` de sus documentos; sin documentos, el tiempo del tipo de
  solicitud (15 días hábiles para P/Q/R). Las felicitaciones no tienen plazo.
- **Al responder:** se guardan `response_days`, `difference_days` e `is_late`. Si se respondió
  tarde, la causal es obligatoria.
- **Vencido:** `is_overdue` = caso abierto con `due_on` anterior a hoy. Cada documento tiene su
  propio `is_overdue`.

### Permisos

| Rol | Puede |
|---|---|
| Gestor PQRSF (admin o miembro del área `PQRSF_MANAGER_AREA_SLUG`, por defecto `calidad`) | Ver todo, asignar, editar catálogos, trabajar cualquier caso, reclasificar, eliminar |
| Responsable del caso | Verlo, editarlo, cambiar estado, responder documentos, escribir al cliente y al proveedor |
| Quien creó el caso y el ejecutivo comercial enlazado | Verlo y comentar; les aparece en su bandeja |
| Cualquier usuario autenticado | Crear casos internos |

Cada caso trae `permissions` (`can_work`, `can_assign`, `can_delete`) para que la UI no duplique
reglas. Un responsable debe haber iniciado sesión una vez.

### Historial

- **Solo inserción:** `pqrsf_case_events` es la línea de tiempo de cada caso y también el
  historial del módulo. No se edita ni se borra.
- **Caso eliminado:** se registra el evento `deleted` con motivo, y sus eventos pierden solo el
  vínculo.
- **Registros de acceso** (`viewed`, `inbox_opened`): uno por persona y caso cada 30 minutos. Se
  purgan a los 90 días.
- **Consultas:** `GET /pqrsf/activity` (gestores, paginado por `before`) y
  `GET /pqrsf/activity/last-seen`.

### Línea de tiempo legible

Cada evento (detalle del caso y `/pqrsf/activity`) trae, además de `event_type` y `data`:

| Campo | Contenido |
|---|---|
| `actor_kind` | `system`, `user`, `client`, `supplier` o `external` (remitente desconocido) |
| `actor_name` | «Sistema», la persona, el cliente o el correo del proveedor |
| `title` | Frase corta en español |
| `category` | `lifecycle`, `access`, `assignment`, `status`, `communication`, `documents`, `classification` |
| `status_change` | `{from, to}` con nombres de estado, o `null` |

| Evento | Se lee |
|---|---|
| `created` (formulario / correo / interno) | Sistema · Creó el caso desde el formulario público / desde un correo de x@y / Ana · Creó el caso |
| `read` | Ana · Abrió el caso por primera vez · Recibida → Leída |
| `viewed` / `inbox_opened` | Ana · Abrió el caso / Entró a la bandeja |
| `assigned` | Asignó el caso a Juan / Tomó el caso / Quitó el responsable / asignación automática |
| `status_changed` | Cambió el estado / Finalizó el caso / Reabrió el caso, con el chip de estados |
| `updated` | Editó el asunto y la sede |
| `comment` | Comentó / Respondió al cliente (2 archivos) |
| `item_*` | Agregó / Quitó / Editó / Respondió el documento X |
| `supplier_message` | Envió una solicitud de información a TEAM FOODS |
| `email_received` | Proveedor · Respondió por correo / Cliente · Escribió por correo |
| Otro | Registró un cambio en el caso |

Los accesos se pueden agrupar («Abrieron el caso: Ana, Juan»). La primera lectura no se agrupa,
porque trae el cambio de estado.

### Endpoints de seguimiento (sesión Entra)

| Método | Ruta | Quién |
|---|---|---|
| GET | `/pqrsf/tracking/catalogs?include_inactive=` | autenticado |
| GET / PATCH | `/pqrsf/request-types`, `/pqrsf/request-types/{id}` (`prefix`, `requires_follow_up`, `sla_days`, `handling`, `description`, `allows_attachments`, `asks_area`) | gestor |
| POST / PATCH | `/pqrsf/statuses`, `/pqrsf/dependencies`, `/pqrsf/document-types` | gestor |
| GET / POST / PATCH | `/pqrsf/subjects`, `/pqrsf/subjects/{id}`, `/pqrsf/subjects/{id}/causes`, `/pqrsf/causes/{id}` | GET autenticado; cambios gestor |
| GET | `/pqrsf/assignees?q=` | gestor |
| GET | `/pqrsf/cases`, `/pqrsf/cases/summary`, `/pqrsf/cases/export` (CSV `;` con BOM, una fila por documento) | autenticado, filtrado por visibilidad |
| GET | `/pqrsf/cases/suggestions?field=customer\|supplier\|sales_executive\|requester&q=` | autenticado |
| POST | `/pqrsf/cases` | autenticado |
| GET / PATCH / DELETE | `/pqrsf/cases/{id}` | visible / responsable o gestor / gestor |
| POST | `/pqrsf/cases/{id}/assignment`, `/status`, `/read`, `/comments` | según la acción |
| POST / PATCH / DELETE | `/pqrsf/cases/{id}/items`, `/items/{item_id}` | responsable o gestor |
| GET | `/pqrsf/cases/{id}/files`, `/files/{file_id}/download`, `/attachments/{id}/download` | visible |
| POST | `/pqrsf/activity/visits` · GET `/pqrsf/activity`, `/activity/last-seen` | autenticado / gestor |

Filtros de `/cases`, `/summary` y `/export`:
- `status_id` (repetible), `request_type_id`, `assignee_user_id`, `unassigned`;
- `dependency_id`, `site_id`, `open_only`, `overdue`, `due_today`;
- `received_from`, `received_to`, `q`, `mine`;
- `sort`: `received_on`, `due_on`, `code`, `updated_at`, con `-` para descendente;
- `page`, `size` (máximo 100).

`PATCH` aplica solo los campos enviados; `null` limpia el campo. Además de los datos del caso
acepta `pickup_order_requested`, `pickup_order_number` (solo con `yes`; pasar a `no` o
`unknown` lo limpia) y `delivery_date` (no futura). Con productos, `supplier_name` sale de ellos y
cambiarlo responde `422`.

Cada caso trae:
- `classification`: `requested_area`, `subject`, `cause` (`{id, code, name}`), `route`,
  `pickup_order_requested`, `pickup_order_number` y `delivery_date`.
- `products[]`: `id`, `source`, `invoice_number`, `sku`, `description`, `supplier_code`,
  `supplier_name`, `lot`, `expiry_date` y `affected_quantity`.

### Equivalencia con el Excel P.13.F.01

| Columna del Excel | En el caso |
|---|---|
| Fecha de Petición | `received_on` |
| Consecutivo | `code` (el original en `legacy.original_consecutive`) |
| Responsable | `assignee_user_id` |
| Proveedor | `supplier_name` (y los productos) |
| Dependencia (Interno) | `dependency_id` |
| Usuario / Cliente | `requester_name`, `requester_email` / `customer_name`, `customer_nit` |
| Ejecutivo Comercial | `sales_executive_*` |
| Tipo de documento / Cantidad | `pqrsf_case_items.document_type_id` (o `description`) / `quantity` |
| Sucursal/Día | `site_id` |
| Fecha de Respuesta / Promedio / Establecido / Diferencia | `responded_on` / `response_days` / `sla_days` / `difference_days` |
| Estado | `status_id` |
| Causal / Detalle > 5 | `delay_cause` / `delay_detail` |

### Migración del Excel

```powershell
poetry run python scripts/import-pqrsf-tracking.py P.13.F.csv --year 2026 `
  --site "Copacabana=medellin" --site "Funza=bogota"            # simulación
# --assignee "Nombre=<correo>" … --apply                         # importación real, repetible
```

Sobre el CSV actual produce 2.720 casos y 3.901 documentos.
- **Filas sin consecutivo:** son más documentos del caso anterior.
- **Estados:** `TERMINADO` → `finalizada`, `EN PROCESO` → `en_curso`.
- **Retrasos históricos:** no se les exige causal.
- **Responsables sin correo mapeado:** quedan en `legacy.responsable` y la UI los muestra como
  «Responsable (histórico)».

---

## 6. Panel interno (UI)

### Rutas

En `src/app/(app)/[area]/pqrsf`, con sesión de Entra y `credentials: "include"`. El código está
en `src/features/pqrsf`.

| Ruta | Pantalla | Componentes principales |
|---|---|---|
| `/[area]/pqrsf` | Bandeja: vistas rápidas (mis casos, sin asignar, vencidos, vencen hoy, abiertos), filtros en la URL, tabla densa, exportar | `case-inbox`, `cases-table`, `case-badges` |
| `/[area]/pqrsf/[caseId]` | Detalle: datos, ejecutivo comercial, documentos, solicitud original, archivos, línea de tiempo, comentar, responder al cliente, escribir al proveedor | `case-detail`, `case-items`, `intake-request`, `case-files`, `case-timeline`, `event-line`, `supplier-message-dialog`, `respond-item-dialog`, `status-menu`, `case-dialogs` |
| `/[area]/pqrsf/nuevo` | Caso interno | `new-case-form` |
| `/[area]/pqrsf/historial` | Historial del módulo (gestores) | `activity-history` |
| `/[area]/pqrsf/configuracion` | Tipos en dos tablas: «En el formulario público» (descripción, evidencias y si pide el área, para los 5 tipos, de `GET /pqrsf/request-types`) y «Seguimiento de los casos» (prefijo, seguimiento y días, solo los 3 que abren caso); asuntos y causales; estados; dependencias; tipos de documento; temporizadores | `catalog-settings`, `subject-settings`, `notification-settings`, `settings-parts` (diálogo y tabla compartidos) |
| `/[area]/pqrsf/sugerencias` y `/[feedbackId]` | Sugerencias y felicitaciones | `feedback-list`, `feedback-detail` |

### Comportamiento

- Al abrir el detalle se llama `POST /cases/{id}/read`.
- Las acciones dependen de `permissions`.
- La línea de tiempo pinta «`actor_name` · `title`», el chip de `status_change` y un icono por
  `category` o `actor_kind`. Ya no traduce `event_type`.
- Agrupa los accesos consecutivos (en el historial, por caso), salvo un acceso que trae
  `status_change`: la primera lectura («Recibida → Leída») va sola, para no perder el chip.
- Estados: `recibida` neutro, `leida` informativo, `en_curso` alerta, `finalizada` éxito. El
  `color` hex de la API no se aplica inline.
- Vencido: error + icono + texto. Vence hoy: alerta + texto. Nunca solo color.
- Cuatro estados en cada vista: cargando, vacío, error con «Reintentar» y sin permisos.
- En móvil, la tabla pasa a tarjetas.

### Lo que cambia con la clasificación

| Antes | Ahora |
|---|---|
| Asunto en texto libre | Asunto y causal del catálogo |
| Dependencia elegida a mano | Dependencia derivada; Calidad la corrige al reclasificar |
| — | Ruta de tratamiento |
| Proveedor en texto | Productos con proveedor del maestro |
| — | Área elegida por el cliente, orden de recogida, ejecutivo comercial de la factura |

**La clasificación vive en el caso** (I-1 e I-2, D17): al abrirse se copia de la solicitud, que
sigue intacta como evidencia en «Solicitud original».

| Dato | En el caso | Editable | Quién |
|---|---|---|---|
| Tipo | `request_type` | No (cambiaría el radicado; si llegó mal, se cierra y se abre otro relacionado) | — |
| Área elegida | `requested_area_id` | No | — |
| Asunto, causal, ruta, dependencia | `subject_id`, `cause_id`, `route`, `dependency` | Sí, al reclasificar (I-3) | Gestor |
| Productos | `pqrsf_case_products` | No: son lo que envió el cliente | — |
| Proveedor | `supplier_name`, derivado de los productos | Solo sin productos | Responsable o gestor |
| Orden de recogida, fecha de entrega | `pickup_order_*`, `delivery_date` | Sí (el número de OR se conoce después, en CIESA) | Responsable o gestor |
| Evidencias | se leen de la solicitud | No se borran | — |

**En el detalle:**
- **Bloque «Clasificación»:** área elegida, asunto, causal, ruta, orden de recogida y fecha de
  entrega. «Editar» abre la orden de recogida y la fecha si hay `can_work` y el caso está
  abierto.
- **Bloque «Productos»:** de solo lectura, con SKU, descripción, proveedor, factura, lote,
  vencimiento y cantidad.
- **«Editar datos»:** con productos ya no ofrece el proveedor.

**Reclasificación (I-3):**
- **Endpoint:** `POST /pqrsf/cases/{id}/classification` con `subject_id`, `cause_id`, `route?`,
  `dependency_id?` y `note`, que es obligatoria.
- **Quién:** solo gestores.
- **Línea de tiempo:** evento `reclassified` con el antes y el después.
- **Plazo:** hoy no cambia el SLA. Con la criticidad (fase 5) se recalcula; ver P16.
- **Cliente:** reclasificar no le escribe.
- **Caso cerrado:** no se reclasifica.

**Bandeja (I-4):**
- **Filtros nuevos:** `requested_area_id`, `subject_id`, `cause_id`, `route`, `supplier_code` y
  `has_products`.
- **Búsqueda:** `q` también por factura, SKU, lote y orden de recogida.
- **Columnas nuevas:** «Asunto · Causal», ruta y proveedores.
- **CSV:** una fila por producto.

**Productos del caso (I-2):**
- **Tabla:** `pqrsf_case_products`, con `source` (`request`, `internal`, `email`).
- **Al abrir el caso:** se copian las líneas de la solicitud; la migración `0038` copió las de
  los casos existentes.
- **Inmutables:** no hay CRUD de líneas. Las de casos internos y de correo llegan con I-5 e I-6.

**Formulario del comercial (I-5):**
- **Endpoint:** `POST /pqrsf/cases` con los mismos campos del público, en multipart con
  evidencias.
- **Cliente:** por NIT, desde `GET /v1/commercial/customers?nit=`.
- **Ejecutivo:** el comercial queda como ejecutivo, con `source: internal`.
- **Búsqueda de productos:** interna, en `GET /pqrsf/products`, con proveedor (pasó de I-2 a
  esta etapa).

---

## 7. Correo entrante

Cualquier correo a `pqrsf@jdh.com.co` se convierte en caso (origen `email`) o se suma a uno
abierto. Usa Microsoft Graph con permisos de aplicación, sin contraseñas de buzón.

### Plantilla

```
Tipo: Petición | Queja | Reclamo | Sugerencia | Felicitación
Sede: (sede o sucursal)
Nombre: (quien solicita)
Razón social: (empresa)
NIT: (de la empresa)
Novedad: (en quejas y reclamos: Calidad del producto, Comportamiento en la entrega…)
Causal: (en quejas y reclamos: Producto vencido, Producto trocado…)
Motivo: (resumen corto)
Descripción:
(todo lo que sigue es la descripción)
```

- **Claves:** no distinguen mayúsculas ni tildes y aceptan sinónimos (`Razón social`/`Empresa`,
  `Descripción`/`Detalle`, `Motivo`/`Asunto`).
- **Asunto del caso:** es el Subject del correo, sin `RE:` ni `[etiquetas]`. Si `Motivo:` dice
  algo distinto, se conserva como primera línea de la descripción.
- **`Novedad` y `Causal`:** se buscan en el catálogo y la causal decide la dependencia. Basta la
  causal si su nombre es de un solo asunto; «Otro» necesita también la novedad. Lo que no
  coincide marca el caso para revisión.
- **Productos:** todavía no se leen del correo.
- **Correo sin plantilla:** abre el caso igual (tipo Petición), marcado `needs_review`.
- **`Tipo` y `Sede`:** se comparan por nombre, código o abreviatura.
- **Historial citado** de una respuesta: no se guarda.

### Qué hace cada correo

| Situación | Resultado |
|---|---|
| Correo nuevo | Caso nuevo y acuse con el radicado, en el mismo hilo |
| Misma conversación o radicado en el asunto, caso abierto | Evento `email_received` en la línea de tiempo |
| Misma conversación y caso finalizado | Caso nuevo con `related_case` |
| `Tipo: Sugerencia` / `Felicitación` | Se guarda como sugerencia y se agradece en el hilo |
| Enviado por `pqrsf@`/`info@`, automático, `bulk`, `no-reply` | Ignorado |

Cada mensaje se guarda con su `Internet-Message-Id` único: un correo repetido no abre dos casos.

### Adjuntos y sincronización

- **Validación:** se aceptan las extensiones de `MAIL_ATTACHMENT_EXTENSIONS`. Las imágenes se
  recodifican, a los documentos se les comprueba que el contenido corresponda a la extensión, y
  todo pasa por ClamAV.
- **Archivo rechazado:** no impide el caso; queda en `rejected_files`.
- **Sincronización:** un worker usa la *delta query* de Graph cada `MAIL_POLL_INTERVAL_SECONDS`
  y guarda el cursor.
- **Primer arranque:** solo toma lo recibido desde que se enciende (o desde `MAIL_INTAKE_SINCE`).
- **Permisos de la app en Entra:** `Mail.Read`, `Mail.ReadWrite` y `Mail.Send` de tipo
  Application, limitados al buzón con una *Application Access Policy*. Ya están otorgados y
  probados.

---

## 8. Correos salientes: avisos, respuestas y temporizadores

Todo sale desde `pqrsf@jdh.com.co` por una cola (`pqrsf_notifications`). Un worker envía cada
30 segundos y reintenta a 1 min, 5 min, 30 min y 2 h; después el correo queda `failed`.
- **Encendido:** `PQRSF_NOTIFICATIONS_ENABLED`.
- **Pruebas:** con `PQRSF_NOTIFY_REDIRECT` todo llega a esa dirección, con el asunto
  `[PRUEBA para …]`.
- **Textos:** viven en `application/notification_templates.py`.

| Correo | Cuándo | A quién |
|---|---|---|
| Aviso de asignación | Alguien asigna el caso a otra persona (o lo crea asignado) | Nuevo responsable |
| Acuse por correo | Caso abierto desde el buzón | Remitente, en el hilo |
| Respuesta parcial al cliente | Comentario con `reply_to_client` | Solicitante |
| Agradecimiento | Sugerencia o felicitación | Remitente |
| Certificados / documento del caso | Sección 10 | Solicitante |
| Mensaje al proveedor | Sección 9 | Proveedor |
| `daily_summary` (17:30) | Días hábiles | Miembros de Calidad: casos abiertos y vencidos |
| `overdue_reminder` (07:30) | Días hábiles | Cada responsable con sus vencidos; los vencidos sin responsable, a Calidad |

**Temporizadores:**
- **Configuración:** `GET/PUT /pqrsf/settings/notifications`
  (`{"daily_summary": {"enabled", "time"}, "overdue_reminder": {…}}`, hora `HH:MM`).
- **Frecuencia:** corren una vez por día hábil.
- **Festivos:** salen de Nager.Date, con la librería `holidays` como respaldo.
- **API caída a la hora:** si vuelve dentro de las 2 horas siguientes, el temporizador se
  ejecuta; pasado ese margen se omite el día.

### Comentarios con archivos y respuesta parcial

`POST /pqrsf/cases/{id}/comments`:
- **Formatos:** JSON (`body`, `reply_to_client`) o multipart (`body`, `reply_to_client`,
  `files`).
- **Archivos:** mismas reglas que el correo entrante. Quedan en `/cases/{id}/files` con su
  `comment_event_id`.
- **`reply_to_client`:** solo quien trabaja el caso, con el caso abierto y correo del
  solicitante. Si el caso nació por correo, responde en ese hilo; si no, es un correo nuevo con
  el radicado.
- **Tope:** máximo 3 MB de adjuntos por respuesta.
- **Seguimiento del envío:** `GET /cases/{id}/client-replies` y
  `POST /client-replies/{notification_id}/retry`.

---

## 9. Mensajes al proveedor

Quien trabaja un caso le escribe al proveedor desde la plataforma, y todo queda en la línea de
tiempo. El cliente no ve estos mensajes.

`POST /pqrsf/cases/{case_id}/supplier-messages` (`201`, multipart):

| Campo | Contenido |
|---|---|
| `kind` | `request` (Solicitud de información), `response` (Respuesta), `improvement` (Oportunidad de mejora), `notice` (Mensaje informativo) |
| `to` | Repetible, de 1 a 5 correos |
| `body` | Hasta 5000 caracteres |
| `supplier_code`, `supplier_name` | Opcionales; salen de los productos del caso |
| `include_case_data` | Por defecto `true`: cliente, sede, fecha y productos con lote, vencimiento, cantidad y factura |
| `include_evidence` | Adjunta las fotos y el video del cliente |
| `files` | Archivos nuevos (quedan en el caso) |

- **Condiciones:** solo con el caso abierto (`409` si no). Los adjuntos no pueden pasar de 25 MB
  en total.
- **Envío:** un correo nuevo por destinatario. El asunto empieza con el radicado entre
  corchetes (`[Q26AC0001] Solicitud de información: …`), y por eso la respuesta vuelve al caso
  por el buzón.
- **Línea de tiempo:** evento `supplier_message`. La respuesta del proveedor llega como
  `email_received` con `actor_kind: supplier`.
- **Seguimiento:** `GET /cases/{id}/supplier-messages` y
  `POST /supplier-messages/{notification_id}/retry`.
- **Contactos:** `GET /pqrsf/supplier-contacts?supplier_code=&q=` devuelve los correos ya usados.
  La tabla se llena sola al enviar; el catálogo de Abastecimiento se puede cargar ahí.
- **UI:** acción «Escribir al proveedor» en el detalle, con el estado de cada correo bajo el
  evento y «Reintentar».

La OM con formato (DOCX/PDF) y el registro del resultado (procede o no procede) van en la fase 3.

---

## 10. Documentos y certificados

### Solicitud de documentos (petición)

- **Campos:** con `document_request=true` se exige `document_category_id` y al menos un
  `document_type_ids` de esa categoría, o `document_other` (texto, máximo 300).
- **Restricciones:** no admite imágenes ni videos.
- **Caso:** nace con esos documentos como ítems, con su plazo.

### Certificados automáticos

- **Configuración:** cada tipo de documento puede apuntar a un archivo del gestor documental de
  Calidad (`PQRSF_CERTIFICATES_AREA_SLUG`). Es opt-in por certificado.
- **Lectura del archivo:** el archivo no se copia, se lee al enviar. Si Calidad lo reemplaza, se
  envía la versión nueva.
- **Al recibir la solicitud:**
  - todo con certificado: no hay caso y `handled_as: documents`;
  - mezclado: se abre un caso solo con lo manual y el correo de certificados cuenta qué quedó
    pendiente;
  - nada con certificado: caso normal.
- **Cuándo no se envían:**
  - con las notificaciones apagadas;
  - si la dirección ya recibió 5 correos de documentos en 24 h;
  - si falla el registro del envío.
- **Correos grandes:** se parten (`PQRSF_DOCUMENT_EMAIL_MAX_MB`, 15 MB).

| Endpoint (gestores) | Para qué |
|---|---|
| `GET /pqrsf/certificates` | Tipos de documento con su certificado, `has_file`, `automatic` y ruta del archivo |
| `GET /pqrsf/certificates/documents?q=&folder_id=` | Archivos de Calidad para elegir |
| `PUT /pqrsf/certificates/{document_type_id}` | `{document_id, is_active}` |
| `DELETE /pqrsf/certificates/{document_type_id}` | Quitar el certificado |
| `GET /pqrsf/document-deliveries/summary`, `GET /pqrsf/document-deliveries`, `GET /{id}`, `POST /{id}/retry` | Registro de lo respondido al instante |

Antes de activar certificados conviene revisar los sensibles (cédula del representante legal,
certificación bancaria, estados financieros): el formulario es público.

### Responder un documento del caso con un archivo

`POST /pqrsf/cases/{case_id}/items/{item_id}/delivery` (`201`):
- **Vías:**
  - `{"source": "vault", "document_id"}`: archivo del gestor de Calidad;
  - multipart `source=upload` + `file`: solo documentos libres o tipos con
    `allows_direct_upload`.
- **Efecto:** marca el documento como respondido hoy, guarda el archivo y lo envía al cliente si
  `send_to_client` (por defecto).
- **Retraso:** pide `delay_cause` y `delay_detail` si se respondió tarde.
- **Consultas:** `GET /cases/{id}/documents/search` y `GET /cases/{id}/deliveries`.

**Pendiente de UI:** la configuración de certificados, la sección «Solicitudes de documentos» y
esta respuesta con archivo existen en la API, pero master de la interfaz todavía no las consume.
El código ya existe sin fusionar en la rama `Lovealone1/pqrsf-certificates-ui` (commit
`2abf131`), que solo entró en `Lovealone1/pqrsf-feedback`: hay que llevarlo a master, no
rehacerlo.

---

## 11. Sugerencias y felicitaciones

- **Manejo:** `pqrsf_request_types.handling = acknowledge` las guarda en `pqrsf_feedback`, sin
  radicado, estado ni plazo, y se agradecen al instante. Se puede cambiar por tipo.
- **Contenido:** la sugerencia admite adjuntos y área; la felicitación es texto y no pide área.
- **Por correo:** con `Tipo: Sugerencia` / `Felicitación` se agradece en el mismo hilo.
- **Cuándo no se agradece:**
  - la dirección ya recibió 3 agradecimientos en 24 h (`recipient_limit`);
  - ya se agradeció en esa conversación;
  - las notificaciones están apagadas.

| Endpoint (gestores) | Para qué |
|---|---|
| `GET /pqrsf/feedback/summary` | `total`, `unread`, `by_kind` |
| `GET /pqrsf/feedback?kind=&q=&unread=&from=&to=&page=&size=` | Lista (10 por página por defecto) |
| `GET /pqrsf/feedback/{id}` | Detalle con archivos y `area`; la primera apertura lo marca leído |
| `GET /pqrsf/feedback/{id}/files/{file_id}/download` | Descarga |

---

## 12. Ejecutivo comercial

Las quejas y reclamos sobre productos llevan el ejecutivo comercial que vendió la factura. Se
saca de las ventas del DWH; no se le pide al cliente.

- **Cuándo:** al abrir el caso de una solicitud con productos.
- **Fuente:**
  - `consumo.ventas`: vista sobre `mart_ventas.fact_venta`, una fila por línea de factura, en
    producción;
  - `consumo.vendedores`: cruzada por `vendedor_key` para código, nombre y correo.
- **Filtros de la consulta:**
  - `nro_transaccion = ANY(...)`;
  - `fecha` de los últimos 2 años;
  - `concepto = 'Ventas'`;
  - `estado <> 'Anulada'`.
- **Unicidad:** el vendedor es de la cabecera, uno por factura.
- **Número de factura:** `nro_transaccion` = `<tipo>-<consecutivo>` (`FEV-1386163`). Lo escrito
  se normaliza en `domain/sales.py: invoice_candidates` (`fev 001386163` → `FEV-1386163`). Un
  número sin tipo se prueba como FEV, FEE y NND.
- **NIT:** con el NIT del caso solo cuenta la factura de ese cliente. Así un vendedor de otro
  cliente no recibe acceso al caso.
- **Qué guarda el caso:**
  - `sales_executive_name`, `sales_executive_code` y `sales_executive_email`;
  - `sales_executive_user_id`: el usuario de la plataforma con ese correo, si ya inició sesión.
    Con él, el ejecutivo ve y comenta el caso;
  - `sales_executive_source`: `invoice` (de la factura), `internal` (lo radicó el comercial) o
    `manual` (escrito a mano). Editar el nombre a mano borra el código, el correo y el usuario.
- **En el detalle:** `sales_executive {name, code, email, user_id, source}`. La UI lo muestra en
  «Datos del caso».
- **Si el DWH no responde** o la factura no aparece, el caso abre sin ejecutivo.
- **Carga del DWH:**
  - se publica de lunes a viernes hacia las 07:40 (de lunes a sábado con el PR #138 del DWH);
  - una factura emitida hoy aparece en la siguiente carga;
  - los vendedores se refrescan a las 03:00.
- **Verificado en producción (solo lectura):**
  - `FEV-1386163` devuelve el vendedor, y con otro NIT no devuelve nada;
  - en 90 días, 109 de 126 vendedores tienen correo `@jdh.com.co`.
- **Sin confirmar:** que el número impreso de la factura electrónica (prefijo DIAN) coincida con
  `nro_transaccion`.
- **No disponible en el DWH:** el lote y la fecha de entrega en `consumo.ventas`. La entrega más
  cercana está en `consumo.planillas_entregas`.
- **Pendiente:** respaldo por el vendedor asignado al cliente en el maestro comercial.

---

## 13. Seguridad del formulario público

- **Imágenes:**
  - solo JPG/JPEG, PNG y WebP;
  - se comprueban nombre, extensión, MIME y formato real;
  - topes: 5 MiB cada una, 5 archivos, 25 MiB en total y 20 millones de píxeles;
  - no se aceptan animadas;
  - Pillow las recodifica sin EXIF, luego pasa ClamAV y se guardan en un bucket privado con una
    clave generada por el servidor.
- **Videos:**
  - MP4, MOV o WebM;
  - se comprueban nombre, MIME y contenedor real (firma `ftyp` o EBML);
  - tope de tamaño y ClamAV;
  - no se recodifican.
- **Datos protegidos:** la búsqueda de productos no expone proveedor ni costos, y las facturas
  nunca se consultan desde el público.
- **`PublicIntakeGuard`:** todo `POST /api/v1/public/*` pasa antes de leer el cuerpo por:
  1. tope de cuerpo (`PUBLIC_MAX_BODY_MB`, 50; `411` sin `Content-Length`, `413` si lo supera);
  2. límite por IP (`strict`: 5 envíos cada 10 min);
  3. límite global (`RATE_LIMIT_PUBLIC_GLOBAL_REQUESTS`, 100 por hora);
  4. CAPTCHA de Cloudflare Turnstile, si se configura `PUBLIC_CAPTCHA_SECRET` (cabecera
     `X-Captcha-Token`).
- **Redis caído:** se responde `503` y no se admite nada.
- **Despliegue:** `RATE_LIMIT_TRUSTED_PROXY_IPS` debe listar los proxies.
- **Niveles del resto de la API:** `read` (600/min), `relaxed` (120/min) y `strict`. La tabla
  completa está en `docs/rate-limiting.md`.

---

## 14. Configuración

| Variable | Defecto | Uso |
|---|---|---|
| `PQRSF_MANAGER_AREA_SLUG` | `calidad` | Área que gestiona PQRSF (vacío = solo admins) |
| `PQRSF_TIMEZONE` | `America/Bogota` | Fechas locales |
| `PQRSF_HOLIDAYS_COUNTRY` / `PQRSF_HOLIDAYS_API_URL` | `CO` / Nager.Date | Días hábiles |
| `PQRSF_MAX_ATTACHMENTS` / `PQRSF_MAX_ATTACHMENT_MB` / `PQRSF_MAX_TOTAL_ATTACHMENT_MB` / `PQRSF_MAX_IMAGE_PIXELS` | 5 / 5 / 25 / 20.000.000 | Imágenes del formulario |
| `PQRSF_MAX_VIDEOS` / `PQRSF_MAX_VIDEO_MB` | 1 / 20 | Videos del formulario |
| `PUBLIC_MAX_BODY_MB` | 50 | Tope del cuerpo de los formularios públicos |
| `PQRSF_CLAMAV_HOST` / `_PORT` / `_TIMEOUT_SECONDS` | `clamav` / 3310 / 10 | Antivirus |
| `PQRSF_NOTIFICATIONS_ENABLED` | `false` | Cola y envío de correos |
| `PQRSF_UI_BASE_URL` | — | Enlaces a la UI en los correos |
| `PQRSF_NOTIFY_REDIRECT` | — | Pruebas: todo a esta dirección |
| `PQRSF_FEEDBACK_REPLIES_PER_EMAIL_PER_DAY` | 3 | Tope de agradecimientos |
| `PQRSF_CERTIFICATES_AREA_SLUG` | `calidad` | Área de los certificados |
| `PQRSF_DOCUMENT_EMAILS_PER_ADDRESS_PER_DAY` / `PQRSF_DOCUMENT_EMAIL_MAX_MB` | 5 / 15 | Correos de documentos |
| `MAIL_INTAKE_ENABLED` | `false` | Lectura del buzón |
| `MAIL_GRAPH_TENANT_ID` / `MAIL_GRAPH_CLIENT_ID` / `MAIL_GRAPH_CLIENT_SECRET` | — | App de Entra del buzón (el **valor** del secreto) |
| `MAIL_PQRSF_INBOX` / `MAIL_IGNORED_SENDERS` | `pqrsf@jdh.com.co` / `info@jdh.com.co` | Buzones |
| `MAIL_INTAKE_SINCE`, `MAIL_POLL_INTERVAL_SECONDS`, `MAIL_MAX_ATTEMPTS`, `MAIL_MAX_ATTACHMENTS`, `MAIL_MAX_ATTACHMENT_MB`, `MAIL_MAX_TOTAL_ATTACHMENT_MB`, `MAIL_ATTACHMENT_EXTENSIONS` | ver `.env.example` | Correo entrante |
| `DWH_*` (`DWH_SCHEMA=consumo`) | — | Ventas para el ejecutivo comercial |
| `PUBLIC_CAPTCHA_SECRET`, `RATE_LIMIT_*` | — | Protección pública |

---

## 15. Desarrollo local y pruebas

API desde el host:

```powershell
docker compose up -d
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API y ClamAV en contenedores: `docker compose --profile app up -d --build` (corre la migración
con el servicio `migrate`). UI: `docker compose -p internal-platform up -d --build` en el repo
de la UI.

- **Maestro de productos:** se sincroniza a mano con `scripts/sync-dwh-masters.py --apply`. Sin
  maestro, las quejas con producto se rechazan.
- **Pruebas:** `poetry run pytest` y `poetry run ruff check app tests alembic`.
- **Tabla de límites:** se regenera con `poetry run python -m app.core.rate_policy --write`
  cuando cambian los endpoints.
- **Correo en local:** encender `MAIL_INTAKE_ENABLED` y `PQRSF_NOTIFICATIONS_ENABLED` con
  `PQRSF_NOTIFY_REDIRECT` apuntando a un correo propio, para no escribirle a clientes reales.

---

## 16. Hoja de ruta

### Fase 1. Radicación parametrizada

| Tarea | API | UI |
|---|---|---|
| Descripción corta por tipo, editable | [x] | [x] |
| Asuntos y causales con dependencia y ruta, en el catálogo y en la administración | [x] | [x] |
| Envío con asunto y causal; descripción obligatoria con «Otro» | [x] | [x] |
| Dependencia derivada; asunto del caso «Asunto · Causal» | [x] | — |
| Productos con proveedor del maestro y búsqueda pública | [x] | [x] |
| Orden de recogida | [x] | [x] |
| Adjuntos en sugerencia, videos, topes en el catálogo | [x] | [x] |
| Área elegida por el cliente y filtro de asuntos por área | [x] | [x] |
| Plantilla de correo con `Novedad:` y `Causal:` | [x] | — |
| Asuntos y causales oficiales de Calidad | [ ] | — |
| Productos en la plantilla de correo | [ ] | — |

### Panel interno

| Etapa | Tarea | API | UI |
|---|---|---|---|
| I-1 | Clasificación en el caso (área elegida, asunto, causal, ruta, orden de recogida, fecha de entrega) y bloque «Clasificación» en el detalle | [x] | [x] |
| I-1 | Fecha de entrega en el formulario público, obligatoria con productos | [x] | [x] |
| I-2 | `pqrsf_case_products` copiados de la solicitud, de solo lectura; proveedor derivado | [x] | [x] |
| I-3 | Reclasificación con nota y evento `reclassified` | [ ] | [ ] |
| I-4 | Filtros y columnas nuevos en bandeja, resumen y CSV | [ ] | [ ] |
| I-5 | Formulario interno del comercial con los campos del público y búsqueda interna de productos con proveedor | [ ] | [ ] |
| I-6 | El correo deja la clasificación en el caso | [ ] | — |
| I-7 | Línea de tiempo legible | [x] | [x] |
| I-8 | Mensajes al proveedor y contactos | [x] | [x] |
| I-9 | Ejecutivo comercial desde la factura (DWH) | [x] | [x] |
| I-9 | Respaldo por el vendedor del cliente | [ ] | — |
| — | UI de certificados, solicitudes de documentos y respuesta con archivo (hecha en `Lovealone1/pqrsf-certificates-ui`, sin fusionar en master) | [x] | [ ] |

### Fases siguientes

| Fase | Contenido | Necesita |
|---|---|---|
| 0 | Insumos de Calidad: matriz de asuntos y causales con dependencia, ruta, producto y criticidad; cuadro P.13; tiempos por criticidad; formatos de OM y ACPM; plantillas de respuesta por causal; motivos de devolución de CIESA; dueños de proceso por sede; contactos de proveedores; clientes que exigen ACPM | Calidad |
| 2 | Acuse con radicado al solicitante del formulario; aviso inmediato a la dependencia (urgente en quejas); mensajes automáticos por estado (`notify_client`, `client_message`) | P3, P4 |
| 3 | Estados de escalado y remisión; OM con formato DOCX/PDF y resultado del proveedor (procede/no procede, decisión del director del canal); dueños de proceso por dependencia y sede con remisión y acceso al caso; recordatorio al proveedor | Fase 0, P5 |
| 4 | Facturas del DWH (verificar la factura, fecha de entrega, autocompletar en el interno); órdenes de recogida, motivos y NC enlazados al caso con un trabajo diario; existencias del lote | Vistas del DWH |
| 5 | Criticidad por causal con su SLA; respuesta formal por plantilla (DOCX editable → PDF, con `docxtpl` y un conversor como Gotenberg); ACPM; lista de cierre por ruta | Fase 0, P8, P9 |
| 6 | Módulo aparte de planes de acción; eficacia por recurrencia (misma causal y cliente más de 2 veces) | P10 |
| 7 | Réplica del gestor de Calidad hacia SharePoint (`Sites.Selected`); documentos por SKU y lote | — |
| 8 | Reportes (ranking de proveedores, causales, recurrencias, tiempos); consulta pública de estado por radicado + NIT, base del asistente | — |

### Automatizaciones propuestas

| # | Automatización | Estado |
|---|---|---|
| A1 | Acuse al cliente con radicado | Por correo: hecho. Formulario: fase 2 |
| A2 | Aviso de asignación | Hecho |
| A3 | Recordatorios por vencimiento | Recordatorio diario de vencidos: hecho. «Vence mañana/hoy» y casos sin asignar: pendiente |
| A4 | Resumen diario y semanal | Diario: hecho. Semanal con indicadores de SLA: pendiente |
| A5 | Buzón → caso y respuestas → línea de tiempo | Hecho. Clasificación asistida con modelo de lenguaje: propuesta |
| A6 | Responder con documento adjunto | Hecho en API (`/items/{id}/delivery`); UI pendiente |
| A7 | Asignación automática por reglas y carga | Pendiente |
| A8 | Solicitudes al proveedor con seguimiento | Mensajes: hecho. Recordatorio al proveedor: pendiente |
| A9 | Documentos generados desde el DWH (carta de garantía, certificado por lote) | Pendiente |
| A10 | Encuesta de satisfacción al cierre | Pendiente |
| A11 | Consulta pública de estado | Fase 8 |

---

## 17. Preguntas abiertas y riesgos

| # | Pregunta | Bloquea |
|---|---|---|
| P1 | ¿Queja frente a reclamo se decide solo por la descripción, o el sistema reclasifica (por ejemplo, si la entrega supera 24 h)? | F1 |
| P2 | Cuando radica un proveedor, ¿qué campos pide? | F1 |
| P3 | En casos que radica un comercial, ¿quién recibe acuse, mensajes de etapa y respuesta formal? | F2, I-5 |
| P4 | ¿Quién es «la persona que debe actuar» al crear el caso? | F2 |
| P5 | ¿Cuánto tiempo tiene el proveedor para responder la OM y cuándo se le recuerda? | F3 |
| P6 | ¿El número de OR existe desde que se monta en CIESA, antes de aprobarse? | F4 |
| P7 | Vistas del DWH de órdenes de recogida, motivos y NC. Ventas ya resuelto: `consumo.ventas` (sección 12) | F4 |
| P8 | ¿Qué clientes exigen ACPM y para qué casos? | F5 |
| P9 | ¿Se puede cerrar sin NC emitida o sin verificar inventario? | F5 |
| P10 | Ventana de la regla de recurrencia | F6 |
| P11 | Topes de video: hoy 1 de 20 MB; si hacen falta más largos, subida en dos pasos | F1 |
| P12 | ¿Evidencias guiadas (anverso, reverso, lote, vencimiento) o lista libre? | F1, F3 |
| P13 | Nombres de la grabación a confirmar (aprobadores del motivo 20, director de inventarios, dueños de proceso) | F3 |
| P14 | ¿Por correo se radica un solo producto y el resto se completa en la plataforma? | F1 |
| P16 | Al reclasificar a otra criticidad, ¿se recalcula el plazo o se respeta el informado al cliente? | F5 |
| P18 | Si reclasificar cambia el área, ¿se avisa al nuevo dueño de proceso? (propuesta: sí) | I-3 |
| P19 | ¿El número impreso de la factura electrónica coincide con `nro_transaccion`? | Sección 12 |

Resueltas:
- **P15:** el cliente elige el área en el formulario (D16).
- **P17:** los productos del caso son los que envió el cliente y nadie los corrige.
- **Contacto de proveedores:** se recuerdan los correos al enviar, sin esperar el catálogo.

**Riesgos:**
- **Exposición pública:** sin proveedor ni costos en el formulario; facturas nunca consultables
  desde el público; antivirus antes de guardar.
- **Datos de proveedores:** los contactos salen del uso y de Abastecimiento, no del DWH marcado
  como PII.
- **Grabación de la reunión:** «fecha de nacimiento» es fecha de vencimiento, «cortavecha» es
  corta fecha y «trocado» es producto cambiado.
- **Adopción:** el correo con plantilla se mantiene; lo que no coincide queda en revisión.
- **Continuidad:** cualquier miembro de Calidad trabaja cualquier caso; los suplentes llegan con
  los dueños de proceso.
- **Mismo día:** las facturas de hoy no están en el DWH hasta la siguiente carga.
