# PQRSF: plan del panel interno (clasificación de casos)

Plan del panel donde Calidad y las áreas trabajan los casos (`/[area]/pqrsf` en la UI, con
sesión de Entra): cómo se ve y se maneja ahí cada dato que ya captura el formulario público,
qué puede corregir Calidad y en qué orden se entrega. Parte de la fase 1 de
[`pqrsf-plan-implementacion.md`](pqrsf-plan-implementacion.md). El contrato vigente está en
[`pqrsf.md`](pqrsf.md) y [`pqrsf-seguimiento.md`](pqrsf-seguimiento.md).

## Qué cambia en la clasificación

La clasificación de un PQRSF deja de ser «tipo + asunto escrito + dependencia elegida a mano» y
pasa a construirse con los campos nuevos del formulario:

| Antes | Ahora |
|---|---|
| Asunto en texto libre | **Asunto** del catálogo (la novedad de la tabla de canales de comunicación) |
| — | **Causal** del asunto |
| Dependencia elegida a mano por Calidad | **Dependencia derivada**: causal > asunto > categoría de documentos > área elegida por el cliente. Calidad solo la corrige al reclasificar |
| — | **Ruta de tratamiento**: proveedor, proceso interno o ninguna (la propone la causal y Calidad la confirma) |
| Proveedor en texto libre | **Productos** con factura, SKU, lote, FDV y cantidad; el proveedor sale del maestro por SKU |
| — | **Área elegida** por el cliente y **orden de recogida** |

Todo lo que el panel filtra, reporta o envía (bandeja, CSV, OM, mensajes al proveedor) se apoya
en estos campos, no en texto libre.

## Principio: la solicitud es evidencia, el caso es la copia de trabajo

- `pqrsf_requests` guarda **lo que envió el cliente** y no cambia nunca. Es la evidencia ante
  la norma y ante el cliente («usted nos dijo…»).
- `pqrsf_cases` guarda **cómo lo está tratando JDH**: clasificación, área que atiende, ruta,
  productos corregidos. Al abrir el caso se copia de la solicitud; desde ahí solo cambia el
  caso, y cada cambio deja un evento en la línea de tiempo.
- El detalle muestra siempre el valor del caso. Donde difiere de lo que envió el cliente, lo
  indica («El cliente indicó: Producto vencido»), con la solicitud original a un clic.

Hoy la clasificación (asunto, causal, área elegida, productos, orden de recogida) vive solo en la
solicitud y el caso la lee de `request`. Este plan la lleva al caso para poder corregirla,
filtrarla y usarla en casos que no vienen del formulario (correo, comercial).

## Campo por campo

| Dato | Lo envía el cliente | En el caso | ¿Editable en el caso? | Quién | Notas |
|---|---|---|---|---|---|
| Tipo | `request_type` | `request_type` | No | — | Cambiar el tipo cambiaría el radicado (P/Q/R). Si llegó mal, se cierra y se abre otro caso relacionado |
| Sede | `site` | `site` | Sí (ya existe) | Responsable o gestor | El consecutivo no cambia (serie fija al abrir) |
| Área elegida | `request.selected_area` | `requested_area` (copia, solo lectura) | No | — | Es lo que dijo el cliente; el área que atiende es otra columna |
| Asunto | `selected_subject` | `subject_id` + texto `subject` | Sí: **reclasificar** | Gestor | Ver reclasificación |
| Causal | `selected_cause` | `cause_id` | Sí: **reclasificar** | Gestor | La causal debe ser del asunto |
| Área que atiende (dependencia) | — (derivada) | `dependency` | Sí (ya existe; pasa a ser parte de reclasificar) | Gestor | Derivada: causal > asunto > categoría de documentos > área elegida. Editarla a mano deja `dependency_overridden` |
| Ruta | — (derivada de la causal) | `route`: `supplier`, `internal`, `none` | Sí: **reclasificar** | Gestor | Calidad la confirma tras validar evidencias (un «averiado» puede resultar manipulación) |
| Descripción | `description` | `description` | Sí (ya existe) | Responsable o gestor | La original queda en la solicitud |
| Productos | `products` | `pqrsf_case_products` | Sí: agregar, corregir, quitar | Responsable o gestor | SKU validado contra el maestro; descripción y proveedor se vuelven a resolver al editar |
| · Factura, SKU, lote, FDV, cantidad afectada | sí | sí | Sí | | |
| · Descripción y proveedor | resueltos del maestro | copia | No a mano: se recalculan con el SKU | | En el interno **sí se muestra el proveedor** |
| · `invoice_verified` | — | `invoice_verified` | No | Sistema | Llega con la fase 4 (facturas del DWH). Hasta entonces no se muestra |
| · Fecha de entrega | — | `delivered_on` | Sí a mano hasta la fase 4 | Responsable o gestor | Lo necesita la OM |
| Orden de recogida | `pickup_order_requested` + número | `pickup_order_requested`, `pickup_order_number` | Sí | Responsable o gestor | En la fase 4 se completa sola desde el DWH |
| Evidencias (fotos, video) | `attachments` | se leen de la solicitud; nuevas van en comentarios | No se borran | — | Descarga con el endpoint de adjuntos del caso |
| Documentos pedidos | `documents` | `items` (ya existe) | Sí (ya existe) | Responsable o gestor | Sin cambios |
| Cliente, NIT, solicitante | sí | `customer_*`, `requester_*` | Sí (ya existe) | Responsable o gestor | |

## Reclasificación

Calidad valida la evidencia y a veces la causal elegida por el cliente no es la real (el cliente
dice «vencido» y es «corta fecha»; dice «averiado por calidad» y es manipulación).

- **Quién:** solo gestores PQRSF (Calidad y administradores). El responsable de otra área ve el
  caso pero no lo reclasifica: si no le corresponde, lo comenta y Calidad lo reasigna. Esto
  evita que un caso salga de un área sin que Calidad se entere.
- **Qué:** asunto, causal, ruta y dependencia en una sola operación,
  `POST /pqrsf/cases/{id}/classification` con `subject_id`, `cause_id`, `route?`,
  `dependency_id?` y `note` (**obligatoria**, el porqué).
  - Si cambia la causal y no se envía dependencia ni ruta, se toman las de la nueva causal.
  - Si se envía una dependencia distinta a la derivada, queda marcada como asignada a mano.
- **Línea de tiempo:** evento `reclassified` con antes y después (`{subject, cause, route,
  dependency}` con códigos y nombres) y la nota. Si cambia la dependencia y el responsable
  actual no es de esa área, no se desasigna solo; la UI sugiere reasignar.
- **SLA:** hoy el plazo sale del tipo y de los documentos, así que reclasificar **no** lo
  cambia. Cuando la criticidad por causal exista (fase 5 del plan), reclasificar recalcula el
  `due_on` desde la fecha de recepción con la criticidad nueva, y el evento guarda el plazo
  anterior. Pregunta abierta para Calidad (P16): si la nueva causal es menos crítica, ¿se
  acorta o se respeta el plazo ya informado al cliente?
- **Cliente:** reclasificar no le escribe al cliente. Los mensajes por etapa (fase 2) salen por
  cambio de estado, no por clasificación.
- **Caso cerrado:** no se reclasifica (igual que el resto de ediciones); se reabre con nota.

## Bandeja: filtros y columnas

Filtros nuevos en `/pqrsf/cases`, `/cases/summary` y `/cases/export` (los existentes se
mantienen):

| Filtro | Parámetro | Notas |
|---|---|---|
| Área que atiende | `dependency_id` (ya existe) | Es «el área» de la bandeja `/[area]/pqrsf` |
| Área elegida por el cliente | `requested_area_id` | Para ver qué piden los clientes de cada área aunque Calidad lo haya movido |
| Asunto | `subject_id` | |
| Causal | `cause_id` (repetible) | |
| Ruta | `route` | `supplier`, `internal`, `none`, `unset` |
| Proveedor | `supplier_code` | Por las líneas de producto del caso |
| Con producto | `has_products` | |
| Búsqueda | `q` | Además de lo actual: número de factura, SKU, lote y número de orden de recogida |

Columnas nuevas en la lista: «Asunto · Causal», área que atiende, ruta (icono: proveedor /
proceso interno) y proveedor(es). En el CSV, además, una fila por producto con factura, SKU,
lote, FDV, cantidad y proveedor.

## Productos a nivel de caso

- Tabla `pqrsf_case_products` (la del plan general): `case_id`, `invoice_number`,
  `product_sku`, `product_description`, `supplier_code`, `supplier_name`, `lot`,
  `expiry_date`, `affected_quantity`, `delivered_on`, `invoice_verified`, `source`
  (`request`, `internal`, `email`), `sort_order`.
- **Al abrir el caso** se copian las líneas de la solicitud (`source = request`). La migración
  copia las de los casos que ya existen.
- **En el caso** se agregan, corrigen o quitan con eventos `product_added`,
  `product_updated`, `product_removed`. La solicitud no cambia.
- `supplier_name` del caso se recalcula con los proveedores de sus líneas.
- Fuente única para la OM (fase 3), los reportes por proveedor (fase 8) y la verificación de
  facturas (fase 4).

## Formulario interno del comercial

`POST /pqrsf/cases` (ya existe) gana los mismos campos que el público, para que el comercial
radique a nombre del cliente (D6 del plan):

- `subject_id`, `cause_id` con las mismas reglas del público (descripción obligatoria con
  «Otro», productos según la causal).
- `requested_area_id` opcional (el comercial puede no saberlo; manda la causal).
- `products` con el mismo formato; en el interno la búsqueda devuelve también el proveedor.
- `pickup_order_requested` y `pickup_order_number`.
- Evidencias: el endpoint pasa a aceptar `multipart/form-data` (`images`, `videos`) además de
  JSON, como ya hacen los comentarios.
- Cliente por NIT: la UI busca en `GET /v1/commercial/customers?nit=` y llena razón social;
  el comercial queda como `created_by` y como `sales_executive_user_id`.
- Pregunta abierta (P3 del plan): quién recibe el acuse cuando radica un comercial.

## Comunicaciones con el proveedor

Hoy el panel escribe al **cliente** (respuestas parciales desde un comentario, entrega de
documentos). Falta escribirle al **proveedor** desde el mismo caso, sin salir a Outlook, y que
todo quede en la línea de tiempo.

- **Tipos de mensaje:**

  | `kind` | Nombre | Uso |
  |---|---|---|
  | `request` | Solicitud de información | Pedir ficha técnica, certificado, explicación |
  | `response` | Respuesta | Contestarle algo que el proveedor preguntó |
  | `improvement` | Oportunidad de mejora | Escalar una novedad de calidad de fabricante (la OM con formato llega en la fase 3) |
  | `notice` | Informativo | Avisar sin pedir nada (por ejemplo, el cierre) |

- **Quién:** el responsable del caso o un gestor, con el caso abierto.
- **Destinatarios:** de 1 a 5 correos. El panel sugiere los contactos ya usados para ese
  proveedor (`pqrsf_supplier_contacts`, que se llena sola al enviar) y los proveedores salen de
  los productos del caso. Cuando Abastecimiento entregue el catálogo de contactos, se carga en
  la misma tabla.
- **Contenido:** texto, datos del caso opcionales (radicado, cliente, sede, productos con lote,
  FDV y cantidad), las evidencias del cliente (fotos y video) si se marcan, y archivos nuevos.
- **Envío:** desde `pqrsf@`, con el radicado en el asunto (`[Q26AC0001] Solicitud de
  información: …`). Así la respuesta del proveedor vuelve sola al caso por el buzón.
- **Línea de tiempo:** evento `supplier_message` con tipo, destinatarios, proveedor, archivos y
  el estado de envío de cada correo. Cuando el proveedor contesta, el evento `email_received`
  se muestra como «Proveedor respondió» si el remitente es uno de los destinatarios.
- El cliente no ve estos mensajes.

## Línea de tiempo legible

Cada evento llega con una frase lista para mostrar, quién la hizo y de qué tipo es, para que la
UI no tenga que traducir códigos:

| Campo | Contenido |
|---|---|
| `actor_kind` | `system`, `user`, `client` o `supplier` |
| `actor_name` | «Sistema», el nombre de la persona, el cliente o el proveedor |
| `title` | Frase corta en español |
| `category` | `lifecycle`, `access`, `assignment`, `status`, `communication`, `documents`, `classification` |
| `status_change` | `{from, to}` con los nombres de los estados, cuando cambia el estado |

Ejemplos:

| Evento | Título |
|---|---|
| Caso abierto desde el formulario | Sistema · Creó el caso desde el formulario público |
| Caso abierto desde un correo | Sistema · Creó el caso desde un correo de ana@cliente.com |
| Caso creado por alguien | Ana Pérez · Creó el caso |
| Primera lectura | Ana Pérez · Abrió el caso por primera vez · Recibida → Leída |
| Apertura posterior | Ana Pérez · Abrió el caso |
| Asignación | Ana Pérez · Asignó el caso a Juan Gómez |
| Estado | Juan Gómez · Cambió el estado · En curso → Finalizada |
| Comentario / respuesta | Juan Gómez · Comentó / Respondió al cliente |
| Mensaje al proveedor | Juan Gómez · Envió una solicitud de información a TEAM FOODS |
| Correo recibido | Cliente · Escribió por correo / Proveedor · Respondió por correo |
| Documento | Juan Gómez · Respondió el documento Ficha Técnica |

Los registros de acceso (`viewed`) se siguen guardando, pero la UI puede agruparlos («Abrieron el
caso: Ana, Juan») para que no tapen lo importante.

## Ejecutivo comercial

Las quejas y los reclamos sobre un producto o un pedido llevan el ejecutivo comercial que vendió
esa factura. No se le pide al cliente: se saca de las ventas.

- **Cuándo:** al abrir el caso de una solicitud con productos (traen número de factura). Las
  facturas se normalizan (`domain/sales.py: invoice_key`) y se buscan en la vista de ventas del
  DWH junto con el NIT del cliente, para no tomar la factura de otro cliente con el mismo número.
  Gana la primera factura que tenga vendedor.
- **Qué guarda el caso:** `sales_executive_name` (como hasta ahora), `sales_executive_code`,
  `sales_executive_email`, `sales_executive_user_id` y `sales_executive_source`:
  - `invoice`: sale de las ventas.
  - `internal`: el comercial que radica desde el formulario interno (I-5).
  - `manual`: alguien lo escribió. Editar el nombre a mano borra el código, el correo y el usuario.
- **Enlace al usuario:** con el correo del vendedor se busca el usuario de la plataforma. Si ya
  inició sesión, puede ver y comentar el caso, y lo encuentra en su bandeja. Si no, se guardan el
  nombre y el correo, sin acceso.
- **Si las ventas no responden** o la factura no aparece, el caso se abre igual sin ejecutivo.
- **Respaldo por el vendedor asignado al cliente (por NIT en el maestro comercial):** queda para la
  entrega final de esta función.
- **Fuente:** `consumo.ventas` (vista sobre `mart_ventas.fact_venta`, una fila por línea de factura),
  cruzada con `consumo.vendedores` por `vendedor_key` para el código, el nombre y el correo. La
  factura es `nro_transaccion` = `<tipo>-<consecutivo>` (`FEV-1386163`). Solo cuentan ventas
  (`concepto = 'Ventas'`), sin anuladas, de los últimos 2 años. El vendedor es de la cabecera: uno
  por factura. Lo que escribe el cliente se normaliza (`fev 001386163` → `FEV-1386163`); un número
  sin tipo se prueba como FEV, FEE y NND.
- **NIT:** con el NIT del caso solo cuenta la factura de ese cliente; una de otro cliente se
  descarta, para no darle el caso a un vendedor que no lo debe ver.
- **Carga:** la vista se publica de lunes a viernes hacia las 07:40 (de lunes a sábado con el PR
  #138 del DWH). Una factura emitida hoy aparece en la siguiente carga: si el cliente reclama el
  mismo día, el caso abre sin ejecutivo.
- **Verificado** contra el DWH real (solo lectura): `FEV-1386163` devuelve código y nombre del
  vendedor; con otro NIT no devuelve nada. En los últimos 90 días, 109 de 126 vendedores tienen
  correo `@jdh.com.co`; el resto queda con nombre y código, sin enlace.
- **Sin confirmar:** que el número impreso en la factura electrónica (prefijo DIAN) coincida con
  `nro_transaccion`. Hay que revisarlo con 2 o 3 facturas físicas; el ajuste va en
  `invoice_candidates`.
- **Lote:** no existe en ninguna vista del DWH; el cliente lo sigue escribiendo.
- En el detalle del caso: `sales_executive` con `name`, `code`, `email`, `user_id` y `source`.

## Endpoints nuevos y cambios de contrato

| Método | Ruta | Cambio |
|---|---|---|
| `GET` | `/pqrsf/cases/{id}` | El caso trae `classification` (`requested_area`, `subject`, `cause`, `route`, `dependency_overridden`), `products` y la orden de recogida. `request` se mantiene |
| `GET` | `/pqrsf/cases` | Filtros y columnas de la tabla anterior; cada item trae `subject`, `cause`, `route` y `suppliers` |
| `POST` | `/pqrsf/cases/{id}/classification` | Nuevo: reclasificar (gestores), con nota |
| `POST` | `/pqrsf/cases/{id}/products` | Nuevo: agregar línea |
| `PATCH` / `DELETE` | `/pqrsf/cases/{id}/products/{product_id}` | Nuevo: corregir o quitar línea |
| `PATCH` | `/pqrsf/cases/{id}` | Gana `pickup_order_requested`, `pickup_order_number` |
| `POST` | `/pqrsf/cases` | Campos del comercial (sección anterior) y multipart |
| `GET` | `/pqrsf/products?q=` | Nuevo, con sesión: como el público pero con proveedor |
| `GET` | `/pqrsf/cases/export` | Columnas de clasificación y una fila por producto |
| `GET` | `/pqrsf/cases/{id}` y `/pqrsf/activity` | Cada evento trae `actor_kind`, `actor_name`, `title`, `category` y `status_change` |
| `POST` | `/pqrsf/cases/{id}/supplier-messages` | Nuevo: mensaje al proveedor (multipart) |
| `GET` | `/pqrsf/cases/{id}/supplier-messages` | Nuevo: mensajes enviados y su estado |
| `POST` | `/pqrsf/cases/{id}/supplier-messages/{notification_id}/retry` | Nuevo: reintentar un envío fallido |
| `GET` | `/pqrsf/supplier-contacts?supplier_code=&q=` | Nuevo: contactos usados antes |

## Entrega por fases

| Fase | Tarea | API | UI |
|---|---|---|---|
| I-1 | Clasificación en el caso: columnas `requested_area_id`, `subject_id`, `cause_id`, `route`, `dependency_overridden`, orden de recogida; copia al abrir y migración de los casos existentes; `classification` en el detalle | [ ] | [ ] |
| I-1 | Detalle del caso: bloque «Clasificación» y «Productos» con lo del cliente al lado cuando difiere | — | [ ] |
| I-2 | `pqrsf_case_products` con copia al abrir y migración; CRUD de líneas con eventos | [ ] | [ ] |
| I-2 | Búsqueda interna de productos con proveedor | [ ] | [ ] |
| I-3 | Reclasificación con nota y evento `reclassified` | [ ] | [ ] |
| I-4 | Filtros y columnas nuevos en bandeja, resumen y CSV | [ ] | [ ] |
| I-5 | Formulario interno del comercial con los campos del público y evidencias | [ ] | [ ] |
| I-6 | Correo: la plantilla deja la clasificación en el caso (hoy solo deriva el área) | [ ] | — |
| I-7 | Línea de tiempo legible (`actor_kind`, `actor_name`, `title`, `category`, `status_change`) | [x] | [x] |
| I-8 | Mensajes al proveedor desde el caso, contactos sugeridos y respuesta del proveedor en la línea de tiempo ([`pqrsf-proveedor.md`](pqrsf-proveedor.md)) | [x] | [x] |
| I-9 | Ejecutivo comercial desde la factura: campos en el caso, enlace al usuario por correo, acceso del ejecutivo, `sales_executive` en el detalle | [x] | [x] |
| I-9 | Adaptador de la vista de ventas del DWH (`consumo.ventas` + `consumo.vendedores`) | [x] | — |
| I-9 | Respaldo por el vendedor asignado al cliente (maestro comercial) | [ ] | — |
| Fase 4 | `invoice_verified` y fecha de entrega desde el DWH | [ ] | [ ] |
| Fase 5 | Reclasificar recalcula el plazo con la criticidad (según P16) | [ ] | [ ] |

I-1 y I-2 van primero porque todo lo demás (reclasificar, filtrar, el formulario del comercial)
escribe o lee esas columnas.

## Preguntas abiertas

| # | Pregunta | Bloquea |
|---|---|---|
| P16 | Al reclasificar a una causal de otra criticidad, ¿se recalcula el plazo o se respeta el informado al cliente? | Fase 5 |
| P17 | ¿Un responsable de otra área puede corregir productos (lote, cantidad) o solo Calidad? Propuesta: responsable o gestor, como el resto de datos del caso | I-2 |
| P18 | ¿Reclasificar un caso que cambia de área debe avisar al nuevo dueño de proceso? Propuesta: sí, con el aviso de la fase 2 | I-3 |
