# Planes de acción (P.04.F.02)

Este módulo digitaliza la matriz «Seguimiento plan de acción» del SIG. Reemplaza el Excel
`P.04.F.02 Seguimiento PA JDH V2 SIG 2026.xlsx`: cada acción correctiva, preventiva o de mejora
lleva sus correcciones inmediatas, sus causas, el plan de actividades con su seguimiento y la
calificación de eficacia.

Las acciones nacen de todos los orígenes del SIG: auditorías, accidentes, inspecciones, partes
interesadas y oportunidades de mejora. La principal fuente son las salidas no conformes de PQRSF.
Una acción de PQRSF se abre desde el caso y llega ya llena. Es el módulo aparte que acordaron
Calidad y TI (D14 en [`docs/pqrsf.md`](pqrsf.md)).

- Clave del catálogo: `planes-accion`, encendida en el área `calidad`.
- Código: `app/modules/action_plans/`.
- Migración: `0065_action_plans`.

## La acción

| Bloque | Columnas del Excel | Campos |
|---|---|---|
| Identificación | A–H | Fecha (`reported_on`), número, intento, código, origen, procedencia (proceso), tipo |
| Registro | I–T | Calificación, quien identifica (NIT, nombre, empresa/cargo), ciudad, quien registra y su cargo, fecha de registro, requisito incumplido, hallazgo, registro fotográfico, antecedentes |
| Corrección inmediata | U–X | Actividad, responsables, fecha límite, evidencias |
| Análisis | Y–Z | Método y causas (cada una con su categoría de las 5M/6M) |
| Plan de acción | AA–AD | Actividad, responsables, fecha límite, evidencias |
| Seguimiento | AE–AJ | Responsables y fecha del seguimiento, realizada, estado, eficaz, descripción de la eficacia |

**Código:** `ORIGEN-ddmmyy-NÚMERO-PROC-INTENTO` (`SNC-290126-121-DIS-1`), la misma fórmula de la
columna E.
- El número es global: el más alto más uno.
- Una acción no eficaz abre un **nuevo intento** con el mismo número y el intento siguiente,
  enlazado al anterior y con antecedentes.

**Listas:**

| Lista | Valores |
|---|---|
| Origen | AI (auditoría interna), AE (externa), AT (accidente de trabajo), IN (inspección), OM (oportunidad de mejora), PI (partes interesadas), SNC (salida no conforme) |
| Procedencia | Tabla `action_plan_processes`, editable: ADM, ABA, ALM, CAL, COM, DIR, DIS, HUM, LOG, MAN, OPE, SIG, SEG. Los nombres sembrados son provisionales hasta que Calidad los confirme |
| Tipo y calificación | Correctiva / real, preventiva / potencial, mejora / oportunidad de mejora. La calificación se propone según el tipo y se puede cambiar |
| Método | 5M, espina de pescado, lluvia de ideas, «no hay fallo en nuestro proceso / respuesta formal» |
| Categoría de la causa | Mano de obra, métodos, maquinaria, materia prima, medio ambiente, medición, otra |
| Eficacia | Sí, no, pendiente |

**Reglas** (notas de la fila 5 del Excel):
- Preventiva y mejora no llevan corrección inmediata.
- Mejora no lleva análisis de causas.

**Estado (derivado):**
- `abierta`: sin actividades.
- `cerrada`: todas las actividades realizadas.
- `en_proceso`: lo demás.

**Eficacia de la acción:**
- `no`: alguna actividad no fue eficaz.
- `si`: todas lo fueron.
- `pendiente`: lo demás.

## Quién hace qué

| Quién | Qué |
|---|---|
| `manage` en `planes-accion` (Calidad) | Crea acciones a mano o desde un caso; edita encabezado, correcciones, causas y plan; califica la eficacia; abre el nuevo intento; mantiene las procedencias; borra |
| `read` en `planes-accion` | Ve toda la matriz, los indicadores y la exportación |
| Responsable (un usuario nombrado en una corrección o actividad) | Sin el módulo, ve solo sus acciones, marca sus filas como realizadas, sube evidencias y quita las suyas |

- Las rutas no tienen el gate del módulo, para que un dueño de proceso de otra área llegue a sus
  acciones. El servicio aplica las reglas de la tabla.
- `GET /action-plans/permissions` responde `false` (nunca 403).
- Al quedar nombrado en una corrección o una actividad, el responsable recibe un aviso en la
  campana de la intranet (con Web Push), con enlace a `/calidad/planes-accion/{id}`.

## Desde un caso PQRSF

`POST /action-plans/from-case/{case_id}` es solo para quien trabaja el caso con `manage`. Crea
la acción ya llena:

| Campo | Fuente en el caso |
|---|---|
| Origen, tipo, calificación | SNC, correctiva, real |
| Procedencia | `pqrsf_dependencies.process_code` de la dependencia del caso (comercial→COM, distribucion→DIS, almacenamiento→ALM, abastecimiento→ABA, calidad→CAL); sin dependencia, CAL |
| Fecha | Recepción del caso |
| NIT, empresa, nombre | NIT y razón social del cliente, solicitante |
| Ciudad | Ciudad de la sede (o su nombre) |
| Hallazgo | Asunto + descripción |
| Registro fotográfico | Si la solicitud trae fotos |
| Antecedentes | Si hay otra acción del mismo NIT y la misma causal |
| Correcciones | «Se aprobó la orden de recogida …» y «Se envió la oportunidad de mejora … al proveedor», si las hubo |
| Quien registra | El usuario, con el cargo del directorio |

El caso registra el evento `action_plan_opened` («Abrió la acción correctiva …»). La UI ofrece
la acción cuando el caso se finalizó con `internal_failure`.

## Endpoints (`/api/v1/action-plans`)

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/permissions` | `{can_read, can_manage}` |
| GET | `/catalog` | Listas del formulario y procedencias |
| POST / PATCH | `/processes`, `/processes/{id}` | Procedencias (Calidad); el código no cambia |
| GET | `` | Matriz paginada. Filtros: `origin`, `process_id`, `action_type`, `status`, `effectiveness`, `city`, `year`, `month`, `case_id`, `q`, `mine`, `overdue` |
| GET | `/indicators` | Por tipo, estado, eficacia, origen, procedencia, mes y ciudad; actividades realizadas, vencidas y su eficacia |
| GET | `/export` | El Excel con el diseño del P.04.F.02 (una fila por corrección, causa o actividad) |
| POST | `` | Crea una acción: `origin`, `process_id` y `action_type` son obligatorios |
| POST | `/from-case/{case_id}` | Crea la acción desde un caso |
| GET / PATCH / DELETE | `/{id}` | Detalle (con evidencias, historial y número de acciones anteriores del cliente), edición del encabezado, borrado |
| PUT | `/{id}/corrections`, `/{id}/causes`, `/{id}/activities` | Reemplazan la sección. Una fila con `id` conserva su avance y su calificación |
| POST | `/{id}/rows/{row_id}/done` | Marca una corrección o actividad realizada (`{done, evidence_note?}`): su responsable o Calidad |
| POST | `/{id}/activities/{activity_id}/rating` | Califica la eficacia (`{effective, note?}`) de una actividad realizada: Calidad |
| POST | `/{id}/next-attempt` | Nuevo intento de una acción no eficaz |
| POST | `/{id}/files` | Evidencias en multipart (`files`, `row_id?`), con las reglas de archivos de PQRSF (extensiones, tamaño, ClamAV). Se guardan en `action-plans/{id}/…` |
| GET / DELETE | `/{id}/files/{file_id}/download`, `/{id}/files/{file_id}` | Descargar o quitar una evidencia |

## Importación del histórico 2026

Comando:

```bash
docker cp "P.04.F.02 Seguimiento PA JDH V2 SIG 2026.xlsx" platform-api:/tmp/p04.xlsx
docker exec platform-api python -m app.modules.action_plans.importer --file /tmp/p04.xlsx           # simula
docker exec platform-api python -m app.modules.action_plans.importer --file /tmp/p04.xlsx --apply
```

**Cómo lee el libro:**
- Lee la hoja «Base datos» directo del XML, porque openpyxl se demora minutos con la caché de
  184 MB de la tabla dinámica.
- Agrupa las filas por código. Las filas con «INTENTO» vacío, cuyo código queda terminado en
  `-`, se unen al primer intento.
- Las fechas vienen como seriales, como `dd-mm-aaaa`, como `mm/dd/aaaa` o como `17-oct-26`.
- Las fechas imposibles («30-02-2026», «En Proceso») quedan vacías y salen en el reporte.
- Los responsables quedan como el texto del Excel, sin cruzarlos con usuarios.

**Qué guarda:**
- Cada acción guarda en `legacy` su fuente, sus filas y sus avisos.
- Es idempotente por código: correrlo dos veces no duplica.

**Resultado en local con el libro real:**
- 79 acciones (375 filas), 184 correcciones, 250 causas y 313 actividades.
- 257 actividades realizadas y 248 eficaces, las mismas cifras de la hoja «Indicador».
- 5 fechas inválidas reportadas.

## Pendiente

- **Recordatorio de vencidas:** recordatorio diario por correo de las actividades vencidas a sus
  responsables. Hoy se avisa al asignar, y la bandeja filtra `overdue`.
- **Documento por acción:** el documento de la acción correctiva para los clientes que lo exigen
  (Cafam, Compensar…), cuando Calidad entregue su formato.
- **Procedencias:** que Calidad confirme sus nombres.
