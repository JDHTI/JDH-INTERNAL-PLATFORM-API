# Solicitudes de apoyo JDH

Funcionalidad implementada en API, rama `feat/module-supplier-supports-requests`. Frontend coordinado en `internal-platform-ui/supplier-supports-form`, rama `feat/supplier-supports-requests`.

## Comportamiento

- Comercial consulta el maestro y solicita un valor para un registro compartido. Inicialmente no se filtra por vendedor. No se propagan cambios a otras filas por producto o cliente.
- BI y administradores administran el maestro y resuelven solicitudes; Comercial ve sus propias solicitudes.
- `costo_neto` solicita `jdh_net_cost`: nuevo costo pactado JDH, independiente de `supplier_value`, que conserva el costo del proveedor. Los demás métodos solicitan `jdh_pct`, con su base y semántica actuales.
- Una solicitud nace `pendiente` y no afecta el maestro. Solo admite una decisión: `aprobada` o `rechazada`. La propuesta no se edita; otro valor exige otra solicitud.
- Crear o aprobar requiere acuerdo vigente, habilitado y sin revisión pendiente. El rechazo exige motivo y no altera valores.
- Cancelar JDH limpia exclusivamente su aporte vigente; conserva acuerdo, proveedor y trazabilidad. Anular el acuerdo completo es una operación distinta.
- Sin notificaciones, retiro de propuestas ni filtro por vendedor en esta entrega.

## Permisos y contrato

Rutas relativas al prefijo vigente `/api/v1`:

| Ruta | Uso |
| --- | --- |
| `GET /supplier-supports/permissions` | `can_manage`, `can_request`, `can_review`. |
| `POST /supplier-supports/{id}/requests` | Crear pendiente: `{version,value,reason}`. |
| `POST /supplier-supports/requests/batch` | Selección múltiple atómica: `{items:[{support_id,version,value,reason}]}`, de 1 a 100 registros. |
| `GET /supplier-support-requests` | Lista: `status`, `q`, `page`, `size`, `mine`. |
| `GET /supplier-support-requests/{id}` | Propuesta, decisión, maestro actual y eventos; admite `mine`. |
| `POST /supplier-support-requests/{id}/approve` | `{version,support_version}`: confirmar contra versión vigente revisada. |
| `POST /supplier-support-requests/{id}/reject` | `{version,reason}`. |
| `GET /supplier-supports/{id}/jdh-history` | Array de eventos completos, exclusivo para BI y administradores. |
| `DELETE /supplier-supports/{id}/jdh-support` | `{version,reason}`: cancelar solo JDH; devuelve maestro actualizado. |

La lista siempre filtra por usuario para Comercial. `mine=true` fuerza ese filtro también para BI; usarlo en lista y detalle del contexto Comercial. Buscar por solicitante, motivo, SKU, NIT o nombre del cliente. `size` máximo 100; respuesta `items,total,page,size`.

Solicitud: `id,support_id,mode,value,reason,requested_by_id,requested_by,created_at,status,version,support_version,snapshot,decided_by,decided_at,decision_reason`. `mode` es `porcentaje` o `costo_neto`. El detalle añade `support` y `events`. Aprobar/rechazar devuelven la solicitud resuelta.

La lista y el detalle añaden `supplier_name` y `supplier_code`, resueltos desde el
maestro para mostrar «Apoyo JDH · proveedor» también en solicitudes anteriores.
Son datos de presentación actuales; no modifican la propuesta ni su snapshot inmutable.

Decimales en texto, finitos, máximo seis decimales. Porcentaje entre -1 y 1 (0.05 = 5 %), costo entre 0 y menos de 10^12. Motivos de 3 a 2000 caracteres, eliminando espacios extremos. Fechas UTC; presentación en `America/Bogota`.

El snapshot conserva identidad, versión, método, proveedor, valores JDH, referencias, unidad/factor, producto, cliente/sucursal, programa, vigencia y estado. Eventos: `id,support_id,request_id,event_type,actor,created_at,reason,before,after`.

## Transacciones y trazabilidad

La aprobación bloquea maestro y solicitud, comprueba versiones, vigencia y modalidad, modifica maestro, resuelve solicitud y registra eventos en una transacción. Ante un fallo, nada queda aplicado. Una solicitud resuelta no se aplica otra vez.

Se permiten varias pendientes. Si otra aprobación o edición cambió el acuerdo, responder 409: BI recarga, compara y confirma con la versión actual. Las otras solicitudes siguen pendientes. Cancelar de nuevo un aporte ya vacío no modifica versiones ni agrega eventos.

PostgreSQL impide UPDATE/DELETE de eventos, borrado de solicitudes, edición de propuestas y nuevas decisiones sobre solicitudes resueltas. Un trigger audita cambios del maestro por edición, importación o carga. Tipos: `migracion`, `creacion`, `solicitada`, `aprobada`, `rechazada`, `cancelada`, `actualizacion_maestro`. El evento de decisión y el cambio efectivo del maestro se guardan en la misma transacción.

`derived.net_cost` y sus conversiones usan el costo JDH vigente en `costo_neto`; `derived.supplier_amount` conserva exclusivamente el aporte del proveedor. Cancelar JDH restablece el costo del proveedor. El validador sigue leyendo el valor del proveedor sin atribuirle dinero JDH; archivos anteriores conservan su fotografía. Los demás métodos mantienen las fórmulas existentes de `jdh_pct`.

## Frontend

- `/comercial/apoyos`: seleccionar acuerdos con checkboxes y solicitar desde la barra de selección; detalle de consulta, incluso para usuarios BI.
- `/comercial/apoyos/solicitudes`: seguimiento propio.
- `/bi/solicitudes-apoyos`: pendientes por defecto, filtros, comparación original/actual/propuesta, aprobación, rechazo e historial.
- Maestro BI: cancelación exclusiva JDH e historial, diferenciados de anular el acuerdo.
- Refrescar consultas tras cambios; contemplar carga, vacío, errores, permisos y 409 con revisión explícita.

## Selección múltiple y búsqueda de clientes

El lote exige el mismo `commercial_supplier_id` y cliente. Con NIT, permite sucursales
distintas del mismo cliente; sin NIT, exige igualdad de grupo, nombre y sucursal.
No permite agrupar proveedores sin identidad. `SupportResponse` expone
`commercial_supplier_id` para validar la selección en la interfaz; la API vuelve a
validar todo con bloqueos ordenados. Valores y motivos son individuales, incluso
cuando el lote combina porcentajes y costos netos. Cada registro conserva su
solicitud, estado y decisión independientes. Si una fila falla, no se guarda ninguna.

La búsqueda comercial por nombre compara palabras, sin exigir frase continua ni
orden, y omite diferencias de acentuación. Por ejemplo, «Salamanca Miguel Ángel»
encuentra «SALAMANCA MEDINA MIGUEL ANGEL». La búsqueda `q` por NIT admite prefijos,
puntos, espacios y dígito de verificación separado por guion; no adivina el DV de
una secuencia numérica sin separador. El cliente observado en la base local es
`80140929`; tiene tres sucursales en el maestro y cuatro acuerdos de apoyo.
El selector pagina resultados y mantiene errores visibles. No hay corrección
automática de errores ortográficos como «slamanca».

## Despliegue y validación

Aplicar `alembic upgrade head`: migración `0036_support_requests`, tras `0035_full_margin_reason`. Crea solicitudes, eventos, triggers y costo JDH independiente. La línea base conserva valores anteriores y aclara que no son solicitudes históricas.

Configurar `SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG=bi` si el entorno tenía explícitamente `comercial`; defaults de aplicación, ejemplo y Compose ya usan BI. Cubre también cargas, perfiles, adjuntos y casos especiales. Las solicitudes usan membresía de Comercial; administradores tienen ambas capacidades.

- Suite completa: **982 pruebas pasan**, incluidas catorce de PostgreSQL.
- Ruff y formato limpios; `git diff --check` correcto.
- Migración comprobada: subir, bajar y subir; una cabeza Alembic.
- PostgreSQL: aprobación, cancelación, rechazo, conflictos, dos aprobadores, múltiples pendientes, aislamiento, rollback e inmutabilidad real.
- `alembic check` señala siete índices preexistentes ajenos a esta entrega (búsquedas comerciales y `ix_supplier_supports_special_case`); ninguna diferencia en las tablas nuevas. Esos índices no se modifican.

Para integrar, configurar `SUPPORT_REQUEST_TEST_DATABASE_URL` hacia una base desechable migrada a head. Las pruebas dejan auditoría deliberadamente, ya que comprueban que no puede eliminarse. Sin esa variable se omiten.

Convenciones revisadas contra API PR #30/#31 y UI #36: commits y títulos en inglés; descripción de PR en español con Resumen, Notas de despliegue y Pruebas. No se hace push ni se publican PRs en esta entrega.
