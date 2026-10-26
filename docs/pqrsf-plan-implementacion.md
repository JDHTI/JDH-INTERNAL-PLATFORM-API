# PQRSF: plan de implementación (definición y gestión documental)

Plan derivado de la reunión de planificación con Calidad del **22 de octubre de 2026**
(transcripción `2026-10-22_planificacion-pqrsf-definicion-y-gestion-documental.md`),
contrastado con lo que el módulo `pqrsf` hace hoy. Reúne las definiciones de negocio, las
decisiones ya tomadas, lo que falta construir o adaptar, y los huecos que solo Calidad puede
cerrar.

Documentos relacionados: [`pqrsf.md`](pqrsf.md), [`pqrsf-seguimiento.md`](pqrsf-seguimiento.md),
[`pqrsf-correo.md`](pqrsf-correo.md), [`pqrsf-certificados.md`](pqrsf-certificados.md),
[`pqrsf-notificaciones.md`](pqrsf-notificaciones.md), [`pqrsf-automatizaciones.md`](pqrsf-automatizaciones.md).

## Avance

Se marca cada tarea al quedar lista en la API (`[x]`). La columna UI dice si la interfaz
ya la consume.

### Bloqueos externos

- [x] Aplicación de Entra con permisos sobre el buzón `pqrsf@jdh.com.co`: `Mail.Read`,
  `Mail.ReadWrite` y `Mail.Send` de tipo *Application*, con consentimiento de administrador.
  Ya se probó la lectura y el envío.
- [ ] **Llaves de esa aplicación en el entorno (pendiente).** Configurar `MAIL_GRAPH_TENANT_ID`,
  `MAIL_GRAPH_CLIENT_ID` y `MAIL_GRAPH_CLIENT_SECRET` (el **valor** del secreto, no su ID) y
  encender `MAIL_INTAKE_ENABLED` y `PQRSF_NOTIFICATIONS_ENABLED`. Mientras tanto no entra ningún
  caso por correo y **no sale ningún correo** del módulo (acuses, avisos de asignación,
  certificados, respuestas al cliente, resúmenes). El código ya existe (`docs/pqrsf-correo.md`,
  `docs/pqrsf-notificaciones.md`).
- [ ] Insumos de Calidad de la fase 0 (matriz oficial de asuntos y causales, formatos,
  plantillas, tiempos por criticidad).

### Fase 1. Radicación parametrizada

| Tarea | API | UI |
|---|---|---|
| Descripción corta por tipo, visible bajo cada opción (`request_types[].description`, máx. 120) | [x] | [x] |
| Descripción editable por gestores (`PATCH /pqrsf/request-types/{id}`: `description`, `allows_attachments`) | [x] | [x] |
| Catálogo de asuntos y causales con dependencia y ruta (migración `0024`, sembrado provisional) | [x] | — |
| Asuntos y causales en el catálogo público (`subjects`) | [x] | [x] |
| Envío con `subject_id`/`cause_id`; descripción opcional, obligatoria con «Otro» | [x] | [x] |
| Dependencia del caso derivada de la causal; asunto del caso «Asunto · Causal» | [x] | — |
| Líneas de producto (factura, SKU, lote, FDV, cantidad afectada) con proveedor resuelto del maestro | [x] | [x] |
| Búsqueda pública de productos sin proveedor ni costos (`GET /public/pqrsf/products`) | [x] | [x] |
| ¿Ya solicitó orden de recogida? + número opcional | [x] | [x] |
| Adjuntos en sugerencia | [x] | [x] |
| Asunto, causal y productos visibles en el detalle del caso (`request.selected_subject`, `selected_cause`, `products`) | [x] | [x] |
| Administración de asuntos y causales para gestores (`/pqrsf/subjects`, `/pqrsf/causes`) | [x] | [x] |
| Asuntos y causales oficiales (reemplazar el sembrado provisional) | [ ] | — |
| Videos en el formulario público (1 video MP4/MOV/WebM de hasta 20 MB, `videos`) | [x] | [x] |
| Topes de adjuntos en el catálogo público (`limits`) | [x] | [x] |
| Área elegida por el cliente (`asks_area`, `dependency_id` en el envío, `selected_area`) | [x] | [ ] |
| Panel interno: clasificación y productos en el caso, reclasificación, filtros, formulario del comercial (detalle en [`pqrsf-panel-interno-plan.md`](pqrsf-panel-interno-plan.md)) | [ ] | [ ] |
| Plantilla de correo con `Novedad:` y `Causal:` (dependencia derivada; revisión si no coincide) | [x] | — |
| Productos en la plantilla de correo | [ ] | — |

Nota de diseño sobre videos: se reciben en el mismo envío del formulario (no en dos pasos
con cuarentena, como proponía la sección 6), con tope de 20 MB y antivirus, y el cuerpo
público sube a 50 MB. Es suficiente para clips cortos de celular; si Calidad necesita videos
más largos (P11), se pasa a la subida en dos pasos.

Nota de diseño: en esta primera entrega los productos viven en la solicitud (`pqrsf_requests.products`,
inmutable) y el caso los muestra desde ella. La tabla `pqrsf_case_products` llega con el
formulario interno, que crea casos sin solicitud pública.

### Fases 2 a 8

- [ ] Fase 2. Acuse al solicitante, aviso inmediato a la dependencia (urgente en quejas), mensajes por estado
- [ ] Fase 3. Contactos de proveedor, OM, respuesta del proveedor, remisión al dueño de proceso
- [ ] Fase 4. Facturas, órdenes de recogida y notas crédito desde el DWH
- [ ] Fase 5. Criticidad, respuesta formal DOCX/PDF, ACPM, lista de cierre
- [ ] Fase 6. Módulo de planes de acción y eficacia por recurrencia
- [ ] Fase 7. Réplica a SharePoint y documentos por producto
- [ ] Fase 8. Reportes y consulta pública de estado

## 1. Qué se pidió, en una frase

Que **toda** novedad de un cliente, proveedor o parte interesada (incluidas las que hoy se
quedan en correos y en órdenes de recogida de CIESA) entre por un solo canal parametrizado, se
enrute sola al área que debe actuar, se trate según su naturaleza (proveedor o fallo de proceso
interno), mantenga informado al cliente en cada etapa, cierre con una respuesta formal y, cuando
hubo fallo de proceso, alimente una matriz de planes de acción cuya eficacia se pueda medir. La
norma ISO con la que JDH está certificada exige ese histórico; hoy no existe.

## 2. Definiciones de negocio

### 2.1 Tipos (con la descripción breve que pidió Juan Camilo)

Fuente: el cuadro del procedimiento PQR (P.13) que Carlos envió por el chat de la reunión.

| Tipo | Descripción para el formulario | Ejemplos |
|---|---|---|
| Petición | Solicitud a JDH: auditorías, visitas, documentación y solicitudes relacionadas | Certificados, fichas técnicas, agendar una visita o auditoría |
| Queja | Disconformidad o novedad **en tiempo real**, relacionada con la entrega y antes del cierre del día: aún no se ha firmado la factura, el auxiliar de ruta o el transportador siguen en el sitio. Se notifica de inmediato | Producto trocado detectado al recibir, actitud del auxiliar |
| Reclamo | Novedad **posterior a la entrega**: ya se firmó la factura a satisfacción o ya pasó tiempo desde la entrega (se mencionó 24 h como ejemplo, ver P1) | Producto vencido, averiado o en mal estado encontrado días después |
| Sugerencia | Oportunidad de mejora de procesos: una propuesta del cliente para mejorar la prestación del servicio | Abrir los sábados, pago con QR |
| Felicitación | Felicitación o reconocimiento a una acción destacada, actitud positiva o servicio excepcional | — |

La misma regla aplica al revés cuando JDH es quien recibe del proveedor de suministro.

**Estos textos se muestran en el formulario**, público e interno, para que quien radica sepa
cuál elegir sin conocer el procedimiento. Deben ser **visibles sin buscarlos, pero breves**:

- Una sola línea corta (máximo unos 120 caracteres) en texto secundario, debajo del nombre de cada
  opción de tipo. Se ve sin tocar nada. No es un párrafo ni un bloque de ayuda.
- No se esconde detrás de un tooltip que aparece al pasar el mouse, porque en el celular no existe
  ese gesto y la mayoría de quienes radican desde la entrega lo hacen desde el celular.
- Si hace falta más detalle (ejemplos), va en un «Ver ejemplos» que se despliega y está cerrado
  por defecto.
- El mismo criterio aplica a la descripción de asuntos y causales.

Son los textos iniciales de la migración. Calidad los ajusta después con el texto oficial del
cuadro del procedimiento P.13, sin despliegue, y la API limita su largo para que no crezcan. Distinguir bien queja de
reclamo es lo que más importa, porque define si la novedad se trata como del momento de la
entrega o como posterior.

Según la norma, una queja o un reclamo **es una salida no conforme (SNC)**. JDH unificó la
matriz de SNC y la de PQR en una sola, así que en conversación «queja/reclamo» y «salida no
conforme» se usan como sinónimos.

### 2.2 Asunto (novedad) y causal

El cliente no escribe: selecciona. Se elige un **asunto** (novedad o canal) y una **causal**
que depende del asunto. Todo sale de la matriz de SNC/PQR de Calidad, y lo que falte se agrega al
catálogo en vez de dejar texto libre.

La fuente es la **tabla de canales de comunicación** del procedimiento: novedad → dependencia.
Asuntos mencionados en ella: calidad del producto, documentos del producto, servicio al cliente,
comportamiento en la entrega, tiempo de entrega, presentación personal.

El asunto y la causal deciden **a qué área va** el caso (D4). El formulario interno muestra,
solo para lectura, «Se dirige a: Distribución» al elegir la causal. En el formulario público se
propone no mostrarlo, porque el nombre del área interna no le dice nada al cliente (pregunta P15).

Causales mencionadas (incompletas, la lista oficial está en la matriz):

| Asunto | Causales |
|---|---|
| Calidad del producto | Averiado (por manipulación o por calidad), vencido, corta fecha, trocado (llegó otro producto), mal estado (aplastado, derramado), contaminación biológica (gusano, gorgojo), contaminación cruzada (químicos con alimentos), empaque débil o costura |
| Comportamiento en la entrega / carga | Error en cargue, producto no solicitado |
| Presentación personal | Conductor o auxiliar sucio, sin EPP, sin afeitar, olor fuerte |
| Servicio al cliente | Actitud de servicio |

Cada causal determina la **dependencia** que actúa (decisión: la dependencia se deriva de la
causal; Calidad puede corregirla) y la **ruta de tratamiento** inicial (sección 3).

### 2.3 Otros términos

| Término | Significado |
|---|---|
| OM, oportunidad de mejora | Formato con el que Calidad escala al proveedor una novedad de calidad de fabricante |
| ACPM | Formato de acción correctiva y preventiva: acciones inmediatas, técnicas de análisis y planeación. Se usa cuando hubo fallo de proceso interno |
| Orden de recogida (OR) | Pedido de devolución que el comercial monta en CIESA con un motivo |
| Motivo de devolución | Código de CIESA. Mencionados: **14** error en cargue, **20** calidad del producto (lo aprueba Calidad), **22** corta fecha o vencido. Los distintos de 20 los aprueba Abastecimiento e Inventarios |
| Nota crédito (NC) | Lo que se emite tras recoger. JDH casi nunca repone producto: aprueba la OR y hace NC |
| Dueño de proceso | Responsable de la dependencia y su homólogo en la sede donde ocurrió la novedad (Distribución, Almacenamiento con el director de sede, Comercial…) |
| Radicado | Número consecutivo exigido por ley a toda PQR. Ya existe (`P26MD0012`) |

## 3. El flujo completo

```
Radicación (formulario público, formulario interno del comercial o correo con plantilla)
  │  tipo · asunto · causal · productos (factura, SKU, lote, vencimiento, cantidad) · evidencias
  ▼
Caso con radicado ─► acuse al solicitante + aviso inmediato a la dependencia de la causal
  │
  ├─ Petición de documentos con certificado ─► respuesta automática (ya existe)
  │
  ▼
Calidad valida la evidencia (fotos claras de anverso y reverso, lote y vencimiento visibles)
  │
  ├─ RUTA PROVEEDOR (calidad del fabricante, producto dentro de fecha)
  │    Calidad decide si escala (hay proveedores que nunca reconocen ciertas novedades)
  │    ─► «Generar OM» llena el formato con los datos del caso ─► correo al contacto del proveedor
  │    ─► cliente: «su caso se escaló al proveedor, tiempos de respuesta X»
  │    ─► respuesta del proveedor (procede / no procede) a la línea de tiempo
  │    ─► si no procede: decide el director del canal (Abastecimiento e Inventarios)
  │
  ├─ RUTA PROCESO INTERNO (vencido, corta fecha, trocado, error en cargue, presentación personal…)
  │    No se envía al proveedor: es fallo de JDH
  │    ─► se remite al dueño de proceso de la dependencia y de la sede
  │    ─► cliente: «su caso fue remitido al dueño de proceso, tiempos de respuesta X»
  │    ─► investigación y trazabilidad ─► ACPM ─► plan de acción (módulo aparte)
  │
  ▼
En paralelo, dentro del tiempo de respuesta que fija la criticidad:
  OR aprobada en CIESA · recogida · NC · verificar existencias en bodega y bloquear el lote
  ▼
Respuesta formal al cliente (carta por plantilla de la causal; con ACPM si el cliente lo exige)
  ▼
Cierre del caso. El plan de acción sigue vivo en su matriz y se mide su eficacia por recurrencia
```

## 4. Decisiones tomadas

| # | Decisión | Origen |
|---|---|---|
| D1 | El formulario sirve a clientes, proveedores y cualquier parte interesada | Reunión |
| D2 | Asunto y causal son listas parametrizadas; se agregan valores al catálogo cuando falten | Reunión |
| D3 | Descripción libre **opcional**; **obligatoria** cuando se elige «Otro» en asunto o causal | 23-oct |
| D4 | La dependencia se **deriva de la causal**; quien radica no la elige | 23-oct |
| D5 | Un caso puede llevar **varios productos** | 23-oct |
| D6 | Los comerciales radican por el **formulario interno** (con sesión), con los mismos campos que el público, y quedan como autor | 23-oct |
| D7 | Producto aplica a queja y reclamo, opcional (hay quejas sin producto) | Reunión |
| D8 | Se permiten videos además de fotos; se puede tomar la foto desde el celular | Reunión |
| D9 | Sugerencia admite adjuntos; felicitación queda como texto libre | Reunión |
| D10 | Ningún acceso de clientes a SharePoint/OneDrive; solo al formulario | Reunión |
| D11 | El gestor documental es propio de la plataforma y se **replica hacia SharePoint** (plataforma como fuente) para no subir dos veces | Reunión + 23-oct |
| D12 | Facturas, órdenes de recogida, motivos y NC **existen en el DWH** y se leen desde allí | 23-oct |
| D13 | Respuesta formal y ACPM se generan como **DOCX editable** y se envían en **PDF** | 23-oct |
| D14 | La matriz de planes de acción vive en un **módulo aparte**, enlazado al caso | 23-oct |
| D15 | El correo con plantilla sigue siendo un canal válido, con plantilla estricta | Reunión |

## 5. Lo que hay hoy frente a lo que falta

| Requerimiento | Hoy | Brecha |
|---|---|---|
| Descripción breve por tipo | `pqrsf_request_types` sin descripción | Agregar `description` y exponerla en el catálogo |
| Asunto como lista | `subject` texto libre de 200 | Catálogos de asunto y causal; guardar ids |
| Causal → dependencia | `dependency_id` solo por categoría de documento | Mapeo causal → dependencia y ruta |
| Producto (factura, SKU, lote, vencimiento, cantidad) | No existe | Líneas de producto en solicitud y caso |
| SKU → descripción y proveedor | `commercial_masters.products` + `MasterLookup.products_by_sku()` | Puerto en PQRSF que consuma `MasterLookup` |
| Proveedor del caso | `supplier_name` texto | Código de proveedor por línea (para reportes) |
| Videos | Solo JPG/PNG/WebP, 5 MiB, 5 archivos, cuerpo público ≤ 28 MB | Tipos de video y subida que no pase por el cuerpo del formulario |
| Adjuntos en sugerencia | `allows_attachments = false` | Activar, y exponer `allows_attachments` en `PATCH /request-types` |
| Formulario interno del comercial | `POST /pqrsf/cases` con cliente y proveedor en texto, sin adjuntos al crear | Mismos campos que el público, cliente por NIT del maestro, evidencias al crear |
| Correo con plantilla | Tipo, Sede, Nombre, Razón social, NIT, Motivo, Descripción | Agregar Asunto, Causal y bloque de productos |
| Acuse al solicitante del formulario | Solo existe para casos por correo | Acuse con radicado, tipo, fecha límite |
| Aviso inmediato a quien actúa | Solo aviso de asignación y resumen diario | Aviso al crear, a la dependencia de la causal |
| Mensajes al cliente por etapa | Solo respuesta parcial manual (comentario) | Mensaje automático configurable por estado |
| Estados de tratamiento | `recibida`, `leida`, `en_curso`, `finalizada` | Estados de escalado a proveedor y remisión al dueño de proceso |
| Contactos del proveedor | No están (el maestro excluye contactos a propósito) | Catálogo propio editable por Calidad |
| Generar y enviar OM | No existe | Formato prellenado, envío y registro de la respuesta |
| Dueños de proceso por dependencia y sede | No existe | Catálogo dependencia × sede → personas |
| Facturas, OR, motivos, NC | No hay nada en la plataforma | Puerto a vistas del DWH y vínculo con el caso |
| Tiempos por criticidad | SLA por tipo de documento y por tipo de solicitud | Criticidad por causal con su SLA |
| Respuesta formal por plantilla | No existe | Plantillas por causal → DOCX → PDF |
| ACPM y matriz de planes de acción | No existe | Módulo nuevo |
| Eficacia (no repetir más de 2 veces por cliente) | No existe | Regla de recurrencia y alerta |
| Ranking de proveedores y causales | No existe | Reportes |
| Consulta de estado por el cliente («el robot») | No existe | Consulta pública por radicado + NIT; el bot va después |
| Documentos por producto (ficha técnica, certificado por lote) | Un certificado = un archivo por tipo de documento | Documentos indexados por SKU (y lote) |
| Réplica a SharePoint | No existe | Espejo desde el gestor |

## 6. Plan por fases

Cada fase es entregable por sí sola. El orden prioriza lo que más pidió Calidad (que todo entre
clasificado y nadie se quede sin saber) antes que las automatizaciones de cierre.

### Fase 0. Insumos de Calidad (bloqueante, sin código)

Sin esto las fases 1 y 3 arrancan con catálogos inventados:

1. Matriz de SNC/PQR con **asuntos y causales oficiales**, y para cada causal: dependencia,
   ruta (proveedor o proceso interno), si exige producto, y criticidad.
2. Cuadro del procedimiento P.13 con la descripción de cada tipo.
3. **Tiempos de respuesta del procedimiento PQR por criticidad** (se mencionaron 5 días).
4. Formato de **OM** vigente y formato de **ACPM** (código, versión, campos).
5. **Plantillas de respuesta** por causal (contaminación biológica, contaminación cruzada,
   producto vencido…) en Word.
6. Lista de **motivos de devolución de CIESA** con su significado y quién los aprueba.
7. Dueños de proceso por dependencia y sede, y sus suplentes.
8. Contactos de calidad de los proveedores (correo, teléfono), que entrega Abastecimiento.
9. Clientes que exigen ACPM además de la carta.

### Fase 1. Radicación parametrizada

**Modelo** (migración `0024_pqrsf_classification`):

- `pqrsf_request_types.description` (hasta 300 caracteres), sembrada con los textos de la
  sección 2.1. Viaja en `GET /public/pqrsf/catalogs` y en `GET /pqrsf/tracking/catalogs`, y el
  formulario la muestra junto a cada tipo. Asuntos y causales también llevan `description`
  opcional, con el mismo uso.
- `pqrsf_subjects`: `id`, `code`, `name`, `request_type_codes` (a qué tipos aplica),
  `is_other`, `sort_order`, `is_active`.
- `pqrsf_causes`: `id`, `subject_id`, `code`, `name`, `dependency_id`, `route`
  (`supplier` | `internal` | `none`), `requires_product`, `criticality_id`,
  `return_reason_code` (motivo de CIESA equivalente, opcional), `is_other`, `sort_order`,
  `is_active`.
- `pqrsf_requests`: `subject_id`, `cause_id`, `products` (JSONB, inmutable como el resto de la
  solicitud); `subject` y `description` pasan a opcionales en la validación (no en la columna:
  los históricos ya los tienen).
- `pqrsf_cases`: `subject_id`, `cause_id`, `route`, `sales_executive_user_id` (comercial que
  radicó), `pickup_order_requested` (¿ya se solicitó orden de recogida?: sí, no o no sabe) y
  `pickup_order_number` (opcional). La solicitud guarda los mismos dos campos.

**Campos del formulario para queja y reclamo**, en este orden:

| Campo | Tipo | Regla |
|---|---|---|
| Asunto | Lista del catálogo + «Otro» | Obligatorio |
| Causal | Lista dependiente del asunto + «Otro» | Obligatoria; con «Otro», la descripción es obligatoria y debe ser específica |
| Descripción | Texto | Opcional, salvo con «Otro» |
| Productos | Sección repetible, **opcional** | Obligatoria solo si la causal lo exige (`requires_product`). Una queja sobre el repartidor no lleva producto |
| · Número de factura | Texto | Obligatorio por línea |
| · Referencia (SKU JDH) | Búsqueda en el maestro | Obligatoria. Al elegirla se muestra la descripción; el proveedor se resuelve solo (en el formulario interno se muestra, en el público no) |
| · Número de lote | Texto | Obligatorio por línea |
| · Fecha de vencimiento (FDV) | Fecha | Obligatoria por línea |
| · Cantidad afectada | Entero > 0 | Obligatoria; es lo dañado, no lo facturado |
| · Fecha de entrega | — | No se pide: sale de la factura (fase 4). Mientras tanto, el formulario interno la pide a mano |
| ¿Ya solicitó orden de recogida? | Sí / No / No sé | Obligatoria cuando hay productos |
| Número de orden de recogida | Texto | Opcional, solo si respondió «Sí» |
| Evidencias | Fotos y videos | Opcional; recomendado con producto |
- `pqrsf_case_products`: `id`, `case_id`, `invoice_number`, `product_sku`,
  `product_description` (copia), `supplier_code`, `supplier_name` (copia), `lot`,
  `expiry_date`, `affected_quantity`, `delivered_on`, `invoice_verified`, `sort_order`.
  Las copias congelan lo que se vio al radicar, igual que el SLA de los documentos.

**Comportamiento:**

- Validación: asunto y causal obligatorios para P/Q/R (no para documentos ni felicitación);
  la causal debe pertenecer al asunto; descripción obligatoria si alguno es «Otro»; productos
  solo si la causal lo permite y obligatorios si `requires_product`.
- La dependencia del caso sale de la causal (D4). La ruta inicial también.
- Nuevo puerto `ProductLookup` en `pqrsf/domain/ports.py`, cableado en `v1_containers.py` con el
  `MasterLookup` de `commercial_masters` (mismo patrón que `supplier_supports`). Resuelve SKU →
  descripción y código de proveedor al guardar.
- **Formulario público:** `GET /public/pqrsf/products?q=` devuelve SKU y descripción, **sin
  proveedor ni costos**. El proveedor se resuelve en el servidor. La factura no se consulta
  desde el público (sería enumerable); se verifica en el servidor y queda `invoice_verified`.
- **Formulario interno** (D6): `POST /pqrsf/cases` acepta asunto, causal, productos y evidencias
  (multipart). Cliente por NIT desde el maestro (`GET /v1/commercial/customers`) y comercial =
  usuario en sesión. Ahí sí se consulta la factura para autocompletar (fase 4).
- `PATCH /pqrsf/request-types/{id}` acepta `description` y `allows_attachments`; la migración
  activa adjuntos en Sugerencia (D9).
- CRUD de asuntos y causales para gestores, como dependencias y tipos de documento.
- Plantilla de correo (`domain/email.py`): claves `Asunto`, `Causal` y un bloque repetible de
  producto (`Factura`, `Referencia`, `Lote`, `Vencimiento`, `Cantidad`), más
  `Orden de recogida: Sí / No (número)`. Lo que no case con el
  catálogo marca `needs_review`, como hoy con Tipo y Sede.

**Videos** (D8):

- MP4, MOV y WebM. No se pueden recodificar como las imágenes: se verifica la firma del
  contenedor y pasan por ClamAV. Tope sugerido de 50 MB por video y 2 videos por solicitud
  (a confirmar).
- No caben en el límite del cuerpo público (28 MB). Se propone subida en dos pasos: el formulario
  pide un permiso de carga (`POST /public/pqrsf/uploads`, con el mismo guard y CAPTCHA), sube a
  una URL prefirmada de un prefijo de cuarentena, y el envío referencia esos ids. Un worker
  escanea y mueve; lo no reclamado en 24 h se borra.
- «Tomar foto» es del frontend (`accept="image/*" capture="environment"`); la API no cambia.

**Aceptación:** una queja con causal «producto trocado» y dos productos crea un caso con
dependencia Distribución, ruta interna, dos líneas con proveedor resuelto, y el correo con
plantilla produce el mismo resultado.

### Fase 2. Avisos y comunicación con el cliente

- **Acuse al solicitante** del formulario (tipo `case_ack`): radicado, tipo, asunto, fecha
  límite y cómo consultar. Hoy solo lo recibe quien escribe al buzón. En casos del formulario
  interno, ver pregunta P3.
- **Aviso inmediato de caso nuevo** (tipo `case_created`) a la dependencia de la causal y a
  Calidad, para todos los tipos que abren caso. Requiere saber quién es «la dependencia»:
  `pqrsf_dependencies.area_id` (área de la plataforma) o la tabla de dueños de proceso de la
  fase 3. En las **quejas** el aviso es inmediato y marcado como urgente, porque la novedad está
  ocurriendo en la entrega y aún se puede actuar (devolver en el sitio, hablar con el auxiliar).
- **Mensajes automáticos por etapa:** `pqrsf_case_statuses` gana `notify_client` y
  `client_message` (plantilla). Al pasar a un estado con aviso se encola el correo al
  solicitante. Así cada etapa nueva (escalado, remitido, OR aprobada…) informa sin código nuevo.
- Todo por la cola actual (`pqrsf_notifications`), con `dedupe_key` por caso y estado para no
  repetir.

**Aceptación:** radicar desde el público genera acuse y aviso a la dependencia; cambiar a un
estado con aviso escribe al cliente una sola vez.

### Fase 3. Tratamiento: proveedor y proceso interno

**Modelo** (migración `0025_pqrsf_treatment`):

- Estados nuevos sembrados: `escalada_proveedor` («En valoración con el proveedor») y
  `en_dueno_proceso` («Remitida al dueño de proceso»), con mensaje al cliente.
- `pqrsf_supplier_contacts`: `supplier_code`, `name`, `email`, `phone`, `is_active`. Editable
  por Calidad. El DWH (`consumo.proveedores`) tiene `correo` y `telefono` marcados como datos
  personales; se puede ofrecer como sugerencia de carga, pero Carlos pidió que los entregue
  Abastecimiento.
- `pqrsf_supplier_escalations`: `case_id`, `supplier_code`, `recipients`, `om_document`
  (archivo generado), `sent_at`, `notification_id`, `outcome` (`pending` | `accepted` |
  `rejected` | `partial`), `responded_at`, `response_note`, `response_email_id`,
  `director_decision`, `director_user_id`, `decided_at`.
- `pqrsf_process_owners`: `dependency_id`, `site_id` (nulo = todas las sedes), `user_id`,
  `role` (`owner` | `site_counterpart` | `backup`).

**Comportamiento:**

- **Ruta del caso** editable por Calidad tras validar (`PATCH /cases/{id}` con `route`), con
  evento en la línea de tiempo.
- **Generar OM** (`POST /cases/{id}/supplier-escalations`): toma sede, productos, proveedor,
  lote, vencimiento, cantidad, factura, fecha de entrega, causal, descripción y evidencias;
  genera el formato (DOCX → PDF) y lo deja para revisión antes de enviar. Un caso con
  productos de varios proveedores genera una OM por proveedor.
- **Enviar OM**: correo desde `pqrsf@` al contacto, con el radicado en el asunto para que la
  respuesta vuelva al caso por el buzón (`email_intake` ya enlaza por conversación y
  radicado). Los videos van como enlace firmado con vencimiento, no como adjunto.
- **Registrar respuesta del proveedor**: marcar un correo recibido del caso como respuesta y
  elegir el resultado. Si es `rejected`, la decisión pasa al director del canal.
- **Remitir al dueño de proceso** (`POST /cases/{id}/referrals`): notifica al dueño de la
  dependencia y al homólogo de la sede del caso, y los deja como participantes con acceso al
  caso (hoy solo ven el responsable, el creador y los gestores).
- Recordatorio al proveedor sin respuesta (A8 de `pqrsf-automatizaciones.md`), con plazo a
  confirmar.

**Aceptación:** un reclamo por empaque débil se escala, el cliente recibe «en valoración con el
proveedor», la respuesta del proveedor entra a la línea de tiempo y queda su resultado; uno por
error en cargue se remite a Distribución de la sede y el homólogo recibe el aviso.

### Fase 4. Integración con el DWH: facturas, órdenes de recogida y notas crédito

- Puertos `InvoiceLookup` y `ReturnsLookup` en `pqrsf/domain/ports.py`. Los adaptadores leen
  vistas del esquema `consumo`, en vivo como hace `consumption`, sin copiar tablas.
- **Factura** (formulario interno y validación del público): número + NIT del cliente →
  líneas con SKU, descripción, cantidad facturada, lote si existe y fecha de entrega. Autocompleta
  productos y `delivered_on`, que la OM necesita (algunos proveedores no reconocen después de
  cierto tiempo de entregado).
- **OR y NC**: como el DWH se carga una vez al día, un trabajo diario (mismo mecanismo de
  `pqrsf_job_runs`) busca órdenes de recogida por cliente, factura y SKU de los casos abiertos y
  las enlaza (`pqrsf_case_returns`: número de OR, motivo, estado, aprobador, número y fecha de
  NC). El caso muestra el estado sin que nadie lo transcriba. El comercial también puede
  escribir el número de OR al radicar (pregunta P6).
- **Bloqueo de inventario**: consultar existencias del lote en bodega para el aviso «quedan N
  unidades del lote X en Y». El bloqueo en sí se hace en el ERP.
- Catálogo `pqrsf_return_reasons` (código CIESA, nombre, dependencia que aprueba), cargado desde
  el DWH o a mano, y enlazado a las causales (`return_reason_code`).

**Aceptación:** en el formulario interno, digitar factura y NIT lista sus productos; al día
siguiente de aprobarse una OR en CIESA, el caso la muestra con su motivo y, luego, su NC.

### Fase 5. Cierre: respuesta formal, ACPM y tiempos por criticidad

- `pqrsf_criticalities`: `code`, `name`, `sla_days`. La causal define la criticidad y el
  `due_on` del caso sale de ella (hoy sale del tipo). Configurable por gestores.
- `pqrsf_response_templates`: plantilla DOCX por causal, con marcadores (cliente, proveedor,
  radicado, asunto, acciones inmediatas, respuesta del proveedor…). Se carga desde el gestor
  documental de Calidad para que la editen sin despliegue.
- **Generar respuesta formal** (`POST /cases/{id}/formal-response`): llena la plantilla, devuelve
  el DOCX para editar; al enviar se sube la versión final, se convierte a PDF y se envía al
  cliente con la respuesta del proveedor adjunta si la hay.
- **ACPM**: mismo mecanismo con su formato. Obligatorio si la ruta es interna **y** el cliente
  lo exige (`customers_requiring_capa`, por NIT) o Calidad lo decide.
- **Lista de cierre** por ruta. Para cerrar se exige respuesta formal enviada. OR aprobada, NC y
  verificación de inventario se muestran como pendientes pero no bloquean (pregunta P9).
- Dependencia técnica: `docxtpl` para llenar plantillas y un conversor DOCX → PDF. LibreOffice
  dentro de la imagen de la API pesa unos cientos de MB; un servicio aparte (Gotenberg) en
  Compose aísla ese costo. Elegir antes de empezar.

**Aceptación:** un caso de ruta interna para un cliente que exige ACPM no cierra sin respuesta
formal enviada; la carta sale en PDF con el radicado y la respuesta del proveedor adjunta.

### Fase 6. Módulo de planes de acción (`corrective_actions`)

Módulo nuevo, independiente de PQRSF (D14), reutilizable para auditorías y no conformidades del
sistema integrado.

- Plan: origen (`pqrsf_case` + id, u otro), proceso o dependencia, sede, dueño, descripción de
  la novedad, análisis de causa (técnica usada), acciones (responsable, fecha compromiso,
  estado, evidencia), verificación de eficacia.
- PQRSF solo conoce el puerto: «crear plan para este caso» y «estado del plan».
  El caso puede cerrarse con el plan abierto.
- **Eficacia por recurrencia**: si la misma causal se repite con el mismo cliente más de 2 veces
  (ventana a confirmar) después de un plan, el plan se marca **no eficaz** y se avisa al dueño
  de proceso y a Calidad.

### Fase 7. Gestor documental

- **Réplica a SharePoint** (D11): cada carpeta del gestor de Calidad puede mapearse a una
  carpeta de la biblioteca «Documentos externos». Al crear, reemplazar o borrar un documento se
  encola la réplica por Graph (`Sites.Selected` limitado a ese sitio). Una sola dirección; lo que
  se edite directo en SharePoint no vuelve.
- **Documentos por producto**: fichas técnicas, certificados de calidad y hojas de seguridad son
  por SKU (y a veces por lote). Hoy un certificado es un archivo por tipo de documento. Propuesta:
  indexar documentos del gestor por SKU (etiqueta o campo) y que la petición de esos tipos pida
  la referencia; si existe el archivo, se envía solo, como los certificados.
- La carpeta «Documentos internos» de OneDrive se migra a la intranet: fuera de este plan.

### Fase 8. Reportes y consulta del cliente

- Indicadores: casos por proveedor (ranking de SNC), por causal, por dependencia y sede, por
  cliente; tiempo de respuesta de proveedores; OM aceptadas frente a rechazadas; recurrencias.
- Exportación con las columnas de la matriz SNC/PQR para auditoría.
- **Consulta pública** por radicado + NIT (A11 de `pqrsf-automatizaciones.md`): estado y mensaje
  de la etapa, sin datos internos. Es la base del «robot» que mencionó Gabriel; el chat queda
  para después.

## 7. Preguntas abiertas

Para Carlos o Calidad, salvo que se indique otra cosa. Cada una dice qué bloquea.

| # | Pregunta | Bloquea |
|---|---|---|
| P1 | ¿Queja frente a reclamo se decide solo por la descripción del tipo, o el sistema reclasifica (por ejemplo, si la fecha de entrega de la factura supera 24 h)? ¿Son 24 h el umbral o fue un ejemplo? | F1 |
| P2 | Cuando radica un **proveedor** (D1), ¿qué pide? ¿Los mismos campos que un cliente o un formulario distinto (por ejemplo, novedades en la recepción de mercancía en JDH)? | F1 |
| P3 | En los casos que radica un comercial, ¿quién recibe acuse, mensajes de etapa y respuesta formal: el comercial, el cliente o ambos en copia? | F2 |
| P4 | ¿Quién es «la persona que debe actuar» al crear el caso: el dueño de la dependencia, todo el equipo del área o solo Calidad para que asigne? | F2 |
| P5 | ¿Cuánto tiempo tiene el proveedor para responder la OM y cuándo se le recuerda? ¿Varía por proveedor? | F3 |
| P6 | El formulario pregunta si ya se solicitó la OR y acepta su número opcional; el sistema la enlaza al día siguiente desde el DWH. Carlos dijo que la OR solo se puede adjuntar cuando está aprobada: ¿el número existe desde que se monta en CIESA (antes de aprobar)? | F1, F4 |
| P7 | Nombres del DWH: vistas y campos de facturas (¿la línea trae lote?), OR, motivos y NC. Esto lo puede responder el equipo de datos | F4 |
| P8 | ¿Qué clientes exigen ACPM (se mencionaron Compensar y otro nombre que no quedó claro en la grabación)? ¿Es por contrato y para todo caso, o solo para fallos de proceso? | F5 |
| P9 | ¿El caso puede cerrarse sin NC emitida o sin verificar inventario, o eso es requisito de cierre? | F5 |
| P10 | Ventana de la regla de recurrencia: «más de 2 veces por cliente», ¿en qué periodo y con la misma causal o con la misma dependencia? | F6 |
| P11 | Topes de video (tamaño, cantidad) y si hacen falta en peticiones | F1 |
| P12 | ¿Las evidencias deben ser guiadas (foto de anverso, reverso, lote y vencimiento como campos separados) para que la OM salga completa, o basta una lista libre de archivos? | F1, F3 |
| P13 | Nombres a confirmar de la grabación: aprobadores del motivo 20 (Manuela, Cristian en Funza, Carlos; suplentes Juan Camilo y Juan David Martínez), director de inventarios (¿Brailen Muñoz?), dueños de proceso (John Dairo Vergara en Distribución; Paula Molina e Isabel Quiroga en Comercial) | F3 |
| P14 | La plantilla de correo es más difícil con varios productos. ¿Se acepta que por correo se radique un solo producto y el resto se complete en la plataforma? (para nosotros) | F1 |
| P15 | **Resuelta (26-oct):** después de «Sede» el cliente elige el área y solo ve las novedades de esa área (`subjects[].dependency_id`, `dependencies` en el catálogo público). Es un filtro: la causal sigue decidiendo el área del caso (D4) | F1 |

## 8. Riesgos y notas

- **Exposición pública.** El formulario es abierto: búsqueda de productos sin proveedor ni
  costos, facturas nunca consultables desde el público, videos en cuarentena hasta el análisis
  antivirus y mismos topes del `PublicIntakeGuard`.
- **Datos personales de proveedores.** Los contactos salen de Abastecimiento y se guardan en
  PQRSF, no se copian del DWH marcado como PII sin decisión explícita.
- **Grabación.** La transcripción automática confunde términos: «fecha de nacimiento» es
  *fecha de vencimiento*, «cortavecha/cortapella» es *corta fecha*, «trocado» es *producto
  cambiado*. Los nombres propios de la sección 7 deben confirmarse.
- **Adopción.** Carlos anticipó resistencia («no me gusta meterme allá»). El correo con
  plantilla se mantiene como puerta de entrada, pero lo que no case con el catálogo queda en
  revisión: no se puede abrir caso sin causal.
- **Continuidad.** Carlos pidió que el área funcione aunque falte alguien: suplentes en
  `pqrsf_process_owners` y la regla actual (cualquier miembro de Calidad trabaja cualquier caso)
  cubren eso.
- **Documentos vigentes.** Al implementar F1 hay que actualizar `pqrsf.md` (campos
  obligatorios, adjuntos) y `pqrsf-correo.md` (plantilla).
