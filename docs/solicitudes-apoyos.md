# Plan: solicitudes de apoyo JDH

Estado: planificación aprobada; entrega documental, sin funcionalidad implementada.

## Decisiones de negocio

- Comercial tendrá un maestro filtrable de solo lectura, inicialmente sin filtro por vendedor.
- Cada solicitud apunta a un único registro compartido del maestro. No propaga cambios a otras filas por producto o cliente.
- BI y administradores administran el maestro y resuelven solicitudes; Comercial consulta y solicita. Trasladar a BI los permisos actuales de Comercial para edición, perfiles, cargas y adjuntos.
- Para `costo_neto`, solicitar un nuevo costo pactado JDH (`jdh_net_cost`), conservando `supplier_value` como costo del proveedor. Para todos los demás métodos, solicitar porcentaje en `jdh_pct`, con su base y convenciones actuales.
- Solicitar exige acuerdo vigente, habilitado y sin revisión pendiente. Los demás acuerdos siguen consultables.
- Comercial ve sus solicitudes; BI ve todas. BI aprueba el valor pedido o rechaza con motivo, sin editar la propuesta.
- Cancelar JDH elimina únicamente su efecto vigente. Conserva acuerdo, aporte del proveedor e historial. Anular el acuerdo completo sigue siendo una acción distinta.

## Persistencia y consistencia

Agregar `jdh_net_cost` al maestro. Crear tabla de solicitudes con acuerdo, modalidad, valor pedido, justificación, solicitante, fecha, fotografía del acuerdo y estado (`pendiente`, `aprobada`, `rechazada`). La propuesta es inmutable: otro valor exige otra solicitud.

Crear eventos inmutables con actor, fecha UTC, motivo, solicitud relacionada, versión y valores anteriores/nuevos. Registrar creación, aprobación, rechazo, cancelación JDH y modificaciones administrativas del aporte. Ningún PATCH, carga o importación debe eludir la auditoría. Preservar valores existentes mediante una línea base identificada como migración, sin inventar solicitudes históricas.

Aprobar en una transacción: bloquear acuerdo y solicitud, comprobar permisos, estado y versiones, actualizar maestro, resolver solicitud y guardar evento. El fallo de cualquier paso revierte todo. Resolver una solicitud dos veces no vuelve a aplicar el cambio.

Permitir varias solicitudes pendientes. Si cambió el acuerdo, devolver 409; BI recarga, compara propuesta con valor vigente y confirma nuevamente contra esa versión. No cerrar automáticamente las demás solicitudes. Repetir una cancelación ya efectiva no genera otro cambio ni evento.

Conservar el costo del proveedor y mostrar el costo efectivo JDH por separado. Al cancelar JDH vuelve a regir el costo del proveedor. Respetar unidad/caja/kg y conversiones existentes. Mantener separados financiación del proveedor y aporte JDH en cálculos y consumidores, incluido el validador; no atribuir JDH al proveedor. Las solicitudes pendientes o rechazadas no afectan cálculos.

## Contrato API propuesto

Rutas relativas al prefijo vigente; reutilizar arquitectura, errores y paginación del módulo `supplier_supports`.

| Ruta | Comportamiento |
| --- | --- |
| `POST /supplier-supports/{id}/requests` | Crear pendiente: valor, motivo y versión consultada. |
| `GET /supplier-support-requests` | Lista paginada y filtrable: propias para Comercial, todas para BI. |
| `GET /supplier-support-requests/{id}` | Detalle autorizado, propuesta original y decisión. |
| `POST /supplier-support-requests/{id}/approve` | Aprobar valor solicitado contra versión vigente revisada. |
| `POST /supplier-support-requests/{id}/reject` | Rechazar con motivo obligatorio. |
| `GET /supplier-supports/{id}/jdh-history` | Historial completo para BI; Comercial accede a sus solicitudes. |
| `DELETE /supplier-supports/{id}/jdh-support` | Cancelación lógica del aporte, con motivo y control de versión. |

Ampliar `/supplier-supports/permissions` con capacidades de solicitar y resolver, conservando `can_manage`. La autorización se aplica en backend. Mantener lectura existente del maestro y proteger separadamente las solicitudes y eventos.

Los porcentajes mantienen la validación vigente; el costo pactado admite importes no negativos y usa la unidad del acuerdo. Las fechas se presentan en `America/Bogota`.

## Frontend coordinado

- Reutilizar navegador, filtros, detalle y documentos en `/comercial/apoyos`, con modo explícito de consulta incluso para usuarios que también administran BI.
- Solicitud individual: modalidad determinada por el acuerdo, valor vigente, unidad/base, valor pedido y justificación.
- `/comercial/apoyos/solicitudes`: seguimiento propio.
- `/bi/solicitudes-apoyos`: pendientes por defecto, filtros, comparación original/actual/propuesta, aprobación, rechazo e historial.
- Maestro BI: cancelar aporte JDH e historial, diferenciados de anulación del acuerdo.
- Invalidar listas, detalle y solicitudes tras cambios. Contemplar carga, vacío, error, permisos y 409. Reutilizar componentes y convenciones existentes.
- Sin solicitudes masivas, notificaciones ni retiro por el solicitante en esta entrega.

## Validación prevista

- Permisos: Comercial no modifica ni resuelve; no accede a solicitudes ajenas.
- Creación/rechazo sin efecto; aprobación porcentual y de costo neto; cancelación y nueva solicitud posterior.
- Preservar proveedor, alcance y unidad; historial íntegro y reintentos sin duplicación.
- Dos aprobadores, solicitudes concurrentes, edición administrativa intermedia y rollback.
- Regresión de cálculos, importación, adjuntos y validador.
- Migración PostgreSQL: subir, revertir y verificar una sola cabeza Alembic; encadenar tras la cabeza vigente (actualmente 0035).
- API: pytest, Ruff y formato. UI: TypeScript, ESLint, build y recorrido Comercial/BI, móvil y ambos temas.

Estas pruebas están planificadas; esta entrega documental no las ejecuta ni certifica funcionalidad.

## Entrega y convenciones

API: rama `feat/module-supplier-supports-requests` desde `master`; commit documental `docs(module-supplier-supports): define JDH support requests and approval workflow`.

UI: coordinar con el agente de `internal-platform-ui/supplier-supports-form`, rama `feat/supplier-supports-requests` desde su `master` y commit `docs(supports): plan commercial requests and BI approvals`. La señal pide planificación y documentación, sin implementar funcionalidad todavía.

Referencias de estilo revisadas: API PR #30 y #31; UI PR #36. Títulos y commits convencionales en inglés; descripción de PR en español con Resumen, Notas de despliegue (o Notas) y Pruebas, enlazando ambos frentes. Identificar pruebas pendientes. Esta fase no publica PRs.
