# Seguimiento de PQRSF

Reemplaza el formato **P.13.F.01 «Seguimiento Peticiones»** (Excel) por casos
con consecutivo, responsable, estado configurable, documentos solicitados con
tiempos de respuesta y una línea de tiempo auditable. Complementa el contrato
del formulario público de [`docs/pqrsf.md`](pqrsf.md).

## Estrategia: solicitud inmutable + caso de seguimiento

Se evaluaron dos caminos: agregar columnas de estado a `pqrsf_requests` o crear
un agregado de seguimiento. Se eligió el segundo:

| Criterio | Estado en `pqrsf_requests` | Caso de seguimiento (elegido) |
|---|---|---|
| Evidencia de lo que radicó el cliente | Se mezcla con datos que cambian | `pqrsf_requests` queda inmutable |
| Casos sin formulario público (flujo del Excel, correo, teléfono) | No caben: la tabla exige NIT, dirección, correo… | `origin = internal` o `import` |
| Historial (quién cambió qué y cuándo) | Se pierde al sobrescribir | `pqrsf_case_events`, en la misma transacción |
| Varios documentos por radicado (filas sin consecutivo del Excel) | Requiere otra tabla igual | `pqrsf_case_items` |
| Nuevos estados | `ALTER TYPE` / despliegue | Fila en `pqrsf_case_statuses` |
| Extraer a microservicio | Acoplado al formulario | Puertos propios (`CaseRepository`, `PeopleDirectory`…) |

Una solicitud pública abre su caso automáticamente (1:1, `request_id` único).
Si la apertura falla, la solicitud igual queda guardada, el recibo sale sin
`tracking_code` y un admin la recupera con `POST /pqrsf/requests/{id}/case`
(idempotente). La migración abrió el caso de todas las solicitudes existentes.

## Consecutivos

`{prefijo}-{año}-{contador:05d}`, por ejemplo `P-2026-00012`.

- Un contador por **tipo y año** (`pqrsf_case_sequences`), reservado con un
  upsert que bloquea la fila dentro de la misma transacción del caso: sin
  duplicados bajo concurrencia y sin huecos si la inserción falla.
- El año evita un contador infinito y replica la práctica del formato (que se
  reinicia por vigencia). El prefijo P/Q/R/S/F se configura por tipo
  (`PATCH /pqrsf/request-types/{id}`); cambiarlo no reescribe códigos emitidos.
- El recibo público ahora devuelve `tracking_code`: es lo que el cliente debe
  citar. `reference` (`PQRSF-<uuid>`) se conserva por compatibilidad.

## Estados

Catálogo configurable (`pqrsf_case_statuses`) con `code`, `name`, `color`,
`sort_order`, `is_initial` (exactamente uno, índice único parcial),
`is_terminal` e `is_active`. Semilla:

| Código | Nombre | Tipo |
|---|---|---|
| `recibida` | Recibida | inicial |
| `leida` | Leída | — |
| `en_curso` | En curso | — |
| `finalizada` | Finalizada | final |

Reglas:

- **Leída automática**: `POST /cases/{id}/read` la llama la UI al abrir el
  detalle. Solo si quien abre es el responsable y es la primera vez: fija
  `first_read_at` y, si el caso está en el estado inicial, lo pasa a `leida`.
- **En curso automática**: al registrar la primera fecha de respuesta de un
  documento, un caso en `recibida`/`leida` pasa a `en_curso`.
- **Cerrar** (estado final) exige que todos los documentos tengan fecha de
  respuesta y causal en los que se respondieron tarde. Fija `closed_at`.
- **Reabrir** exige nota. Un caso cerrado es de solo lectura (salvo comentarios
  y cambio de estado).
- Las automatizaciones usan los códigos `leida` y `en_curso`; si se desactivan
  o se eliminan, la automatización simplemente no ocurre.

Las felicitaciones (`requires_follow_up = false`) reciben consecutivo y entran
a la bandeja, pero no tienen fecha límite.

## Tiempos de respuesta (SLA)

- Días **hábiles** con festivos de Colombia (librería `holidays`), en la zona
  `America/Bogota`. Se cuenta como `NETWORKDAYS` del Excel: responder el mismo
  día de recepción = 1 día.
- Cada documento toma el tiempo de su tipo (`pqrsf_document_types`, 62 tipos
  sembrados con los tiempos observados en el Excel: certificado de calidad 3,
  carta de garantía 2, ficha técnica 5…) y lo **congela** al crearse: cambiar
  el catálogo no altera el histórico.
- `due_on` del documento = recepción + su tiempo. `due_on` del caso = el mayor
  de sus documentos; sin documentos, el tiempo por defecto del tipo de
  solicitud (15 días hábiles para P/Q/R/S).
- Al responder se guardan `response_days` y se expone `difference_days`
  (respuesta − establecido) e `is_late`. Si `is_late`, la causal es obligatoria.
- `is_overdue` = caso abierto con `due_on` anterior a hoy.

## Equivalencia con el Excel

| Columna P.13.F.01 | Nuevo modelo |
|---|---|
| Fecha de Petición | `pqrsf_cases.received_on` |
| Consecutivo | `pqrsf_cases.code` (número original en importación) |
| Responsable | `assignee_user_id` (usuario de la plataforma) |
| Proveedor | `supplier_name` |
| Dependencia (Interno) | `dependency_id` → `pqrsf_dependencies` |
| Usuario | `requester_name` / `requester_email` |
| Cliente | `customer_name` / `customer_nit` |
| Ejecutivo Comercial | `sales_executive_name` |
| Tipo de documento | `pqrsf_case_items.document_type_id` (o `description` libre) |
| Cantidad | `pqrsf_case_items.quantity` |
| Sucursal/Día | `site_id` → catálogo de sedes |
| Fecha de Respuesta | `pqrsf_case_items.responded_on` |
| Promedio de Respuesta | `response_days` (calculado) |
| Tiempo de respuesta Establecido | `sla_days` |
| Diferencia | `difference_days` (calculado) |
| Estado | `status_id` |
| Causal / Detalle > 5 | `delay_cause` / `delay_detail` (obligatoria si hay retraso) |

Cliente, proveedor y ejecutivo quedan como texto con autocompletado a partir
de lo ya digitado (`/cases/suggestions`), porque el Excel tiene cientos de
variantes. Cuando el DWH exponga clientes, proveedores y vendedores, se
reemplazan por identificadores sin cambiar el caso de uso.

## Permisos

| Rol | Puede |
|---|---|
| Gestor PQRSF: admin de plataforma o miembro del área `PQRSF_MANAGER_AREA_SLUG` (por defecto `calidad`) | Ver todos los casos, asignar, editar catálogos, trabajar cualquier caso |
| Responsable del caso | Verlo, editarlo, cambiar estado, registrar respuestas |
| Quien creó el caso | Verlo y comentar |
| Cualquier usuario autenticado | Crear casos internos (sin asignar responsable) |

Cada caso trae `permissions.can_work` y `permissions.can_assign` para que la UI
no duplique estas reglas. El responsable debe haber iniciado sesión una vez
(igual que en áreas).

## Endpoints (`/api/v1`, sesión Entra)

| Método | Ruta | Quién |
|---|---|---|
| GET | `/pqrsf/tracking/catalogs?include_inactive=` | autenticado |
| PATCH | `/pqrsf/request-types/{id}` (`prefix`, `requires_follow_up`, `sla_days`) | gestor |
| POST / PATCH | `/pqrsf/statuses`, `/pqrsf/statuses/{id}` | gestor |
| POST / PATCH | `/pqrsf/dependencies`, `/pqrsf/dependencies/{id}` | gestor |
| POST / PATCH | `/pqrsf/document-types`, `/pqrsf/document-types/{id}` | gestor |
| GET | `/pqrsf/assignees?q=` | gestor |
| GET | `/pqrsf/cases` | autenticado (filtrado por visibilidad) |
| GET | `/pqrsf/cases/summary` | autenticado |
| GET | `/pqrsf/cases/export` (CSV `;` con BOM, una fila por documento) | autenticado |
| GET | `/pqrsf/cases/suggestions?field=customer\|supplier\|sales_executive\|requester&q=` | autenticado |
| POST | `/pqrsf/cases` | autenticado |
| GET / PATCH | `/pqrsf/cases/{id}` | visible / responsable o gestor |
| POST | `/pqrsf/cases/{id}/assignment` (`assignee_user_id` o `null`) | gestor |
| POST | `/pqrsf/cases/{id}/status` (`status_id`, `note`) | responsable o gestor |
| POST | `/pqrsf/cases/{id}/read` | visible (efecto solo para el responsable) |
| POST | `/pqrsf/cases/{id}/comments` | visible |
| POST | `/pqrsf/cases/{id}/items` | responsable o gestor |
| PATCH / DELETE | `/pqrsf/cases/{id}/items/{item_id}` | responsable o gestor |
| GET | `/pqrsf/cases/{id}/attachments/{attachment_id}/download` | visible |
| POST | `/pqrsf/requests/{id}/case` | admin (recuperación) |

Filtros de `/cases`, `/cases/summary` y `/cases/export`: `status_id`
(repetible), `request_type_id`, `assignee_user_id`, `unassigned`,
`dependency_id`, `site_id`, `open_only`, `overdue`, `received_from`,
`received_to`, `q` (código, cliente, NIT, proveedor, ejecutivo, usuario, asunto),
`mine` y `sort` (`received_on`, `due_on`, `code`, `updated_at`, con `-` para
descendente). Paginación: `page`, `size` (máx. 100).

`PATCH` aplica solo los campos enviados; enviar `null` limpia el campo. El
detalle (`GET /cases/{id}`) incluye `events` (línea de tiempo con autor) y, si
viene del formulario, `request` con los datos del cliente y sus adjuntos.

Errores: `401`, `403` (sin acceso o sin rol), `404`, `409` (caso cerrado,
consecutivo o código repetido), `422` (validación con mensaje accionable).

## Migración del Excel

```powershell
# Simulación: reporta casos, documentos y valores sin coincidencia
poetry run python scripts/import-pqrsf-tracking.py P.13.F.csv --year 2026 `
  --site "Copacabana=medellin" --site "Funza=bogota"

# Importación real (repetible: omite filas ya cargadas)
poetry run python scripts/import-pqrsf-tracking.py P.13.F.csv --year 2026 `
  --site "Copacabana=medellin" --site "Funza=bogota" `
  --assignee "Cristian Camilo Garzón=<correo>" `
  --assignee "Juan Camilo Avendaño=<correo>" `
  --assignee "Juan David Martinez=<correo>" `
  --assignee "Manuela Florez=<correo>" --apply
```

Sobre el CSV actual (3.904 filas con datos) produce **2.720 casos y 3.901
documentos**. Todos los tipos de documento y dependencias coinciden con los
catálogos. Decisiones:

- Filas sin consecutivo = más documentos del caso anterior.
- Se conserva el consecutivo original. Los 26 consecutivos repetidos más abajo
  reciben un número nuevo después del mayor, con el original en
  `legacy.original_consecutive`.
- `TERMINADO` → `finalizada`, `EN PROCESO` → `en_curso`; sin estado se infiere
  de las fechas de respuesta.
- Se respetan los tiempos del Excel por fila y no se exige causal a retrasos
  históricos (35 de los 37 documentos tardíos no la tienen).
- Responsables sin correo mapeado (o que no han iniciado sesión) quedan sin
  asignar, con el nombre en `legacy.responsable`; la UI lo muestra como
  «Responsable (histórico)». Copacabana y Funza no existen como sedes: el
  ejemplo las mapea a Medellín y Bogotá; confirmar ese mapeo antes de aplicar.

## Configuración

| Variable | Defecto | Uso |
|---|---|---|
| `PQRSF_MANAGER_AREA_SLUG` | `calidad` | Área cuyos miembros gestionan PQRSF; vacío = solo admins |
| `PQRSF_TIMEZONE` | `America/Bogota` | Fecha local de recepción y de «hoy» |
| `PQRSF_HOLIDAYS_COUNTRY` | `CO` | Festivos para días hábiles |

Las propuestas de automatización (notificaciones y procesos que hoy van por
correo) están en [`docs/pqrsf-automatizaciones.md`](pqrsf-automatizaciones.md).
