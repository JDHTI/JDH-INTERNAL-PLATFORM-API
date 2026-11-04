# Permisos del espacio de trabajo

Cada endpoint del espacio de trabajo (`/[area]/...` en la UI) exige un permiso de módulo además
del límite de tráfico. Hay dos capas: roles globales con bypass y roles de área que conceden
módulos. La vista general (`/directory/*` público, `/users/me`, `/auth/*`, `GET /areas` propias,
`/clientes`, `/permissions/me` y `/permissions/catalog`) sigue abierta a toda sesión.

## Roles globales

| Rol | Qué puede |
| --- | --- |
| `owner` | Todo. Es el único que nombra o quita `admin` y `moderator`. Hay exactamente uno (índice `uq_users_single_owner`), no se puede desactivar ni degradar, y la sincronización del directorio nunca lo bloquea |
| `admin` | Todo en todas las áreas, y el panel de permisos (roles, permisos, miembros, áreas). No cambia roles globales |
| `moderator` | Lee todo en todas las áreas, los usuarios y la bitácora de permisos. Nunca escribe |
| `user` | Por defecto. Solo lo que le den sus roles de área |

- **Quién es owner.** El owner es `PLATFORM_OWNER_EMAIL` (por defecto `analista.ti@jdh.com.co`). La migración `0041` lo asigna si el usuario ya existe. Si no, se corre `scripts/set-owner.py --email ...` después de su primer ingreso o de la sincronización del directorio. El script transfiere el rol y el owner anterior queda como admin.
- **Cambiar un rol global.** Se hace con `PATCH /users/{id}` y `role` en `admin|moderator|user`.
  - Solo el owner puede cambiar un rol global. Cualquier otro recibe 403.
  - Desactivar o degradar al owner responde 409.
  - `owner` no se asigna por API.
  - Se sigue protegiendo al último administrador activo, y el owner cuenta como administrador.
- **Usuarios de desarrollo.** El usuario del bypass de desarrollo es `admin`. El seed de desarrollo deja al analista como `owner`.

## Roles de área

- **Permisos del rol.** Cada rol de un área lleva una lista de `(módulo, acción)` (`area_role_permissions`).
  - `read` permite consultar y descargar.
  - `manage` permite crear, editar, aprobar y configurar, e incluye `read`.
  - El módulo `*` es el área completa.
- **Rol por defecto.** Toda área nueva nace con el rol "Acceso completo" (`*` manage).
- **Un rol por área.** Una persona tiene un rol por área (`area_members`).
- **Responsable.** El responsable del área gestiona toda su área.
- **Reglas por registro.** Siguen dentro de cada módulo: el caso asignado o creado en PQRSF, la cotización propia o el proyecto creado.

### Catálogo (`app/modules/areas/domain/catalog.py`)

| Área | Módulo | `read` | `manage` |
| --- | --- | --- | --- |
| todas | `documentos` | carpetas, búsqueda, descarga | crear, editar, borrar, colores de etiqueta |
| ti | `proyectos` | proyectos, épicas, tareas, tablero en vivo | crear, editar, archivar. El borrado sigue siendo del creador, del responsable o de un admin |
| ti | `monitoreo` | tablero general y aplicativos | registrar, editar, borrar, chequear |
| ti | `datawarehouse` | las pantallas del monitoreo del DWH | pedir una carga |
| calidad | `pqrsf` | todos los casos en solo lectura, sugerencias, contactos, feedback, certificados, envíos | asignar, catálogos, ajustes, certificados, envíos |
| comercial | `cotizador` | cotizaciones, clientes, reglas, exportar | cotizar |
| bi | `cotizador` | igual | validar y devolver |
| bi, comercial | `validador-pedidos` | lotes, órdenes, reglas | cargar, aprobar, umbral, revalidar, borrar. `manage` se concede en el área de `ORDER_VALIDATION_MANAGER_AREA_SLUG`, hoy `comercial` |
| bi, comercial | `apoyos` | maestro de apoyos, perfiles, adjuntos | crear, editar, anular, cargas masivas |
| comercial, bi | `solicitudes-apoyos` | solicitudes (en comercial, solo las propias) | comercial solicita; bi aprueba y rechaza |
| bi | `casos-especiales` | casos especiales | crear, editar |
| bi | `maestros` | maestros comerciales (proveedores, clientes, productos) | editar producto o proveedor, cargar márgenes |

`/commercial/*` también lo leen `apoyos`, `casos-especiales`, `cotizador` y `validador-pedidos`, porque
esas pantallas buscan proveedores y productos.

**No son módulos.** La administración de la plataforma (`/users*`, `/roles`, `/sites`, `/masters`,
`/directory/admin/*`, `/pqrsf/requests*`, escrituras de áreas, roles, miembros y permisos) solo
depende del rol global. Un rol de área nunca debe conceder administración.

## Cómo se aplica

- **Lectura en la presentación.** `require_permission(*módulos, action="read")`
  (`app/modules/areas/presentation/dependencies.py`) protege los routers. Basta con tener uno de los
  módulos en cualquier área donde exista.
  - Va a nivel de router en commercial, casos especiales, perfiles, cargas y adjuntos de apoyos,
    solicitudes, monitoreo del DWH, monitoreo de aplicativos con el tablero, y feedback, certificados
    y envíos de PQRSF.
  - Va por endpoint en el validador de pedidos, el cotizador y el router principal de apoyos.
    `GET .../permissions` queda abierto: la UI lo lee y debe responder `false`, no 403.
  - En PQRSF, `GET /cases/suggestions` y `GET /pqrsf/supplier-contacts` exigen `pqrsf` read. El
    resto del seguimiento sigue protegido por las reglas por caso (`CaseAccess`), porque quien radica
    un caso o lo vende lo sigue aunque no sea de Calidad.
- **Escritura en los servicios.** Cada servicio decide la escritura con su política.
  `PermissionPolicy(areas, área, módulo)` (`app/modules/areas/infrastructure/policies.py`)
  reemplaza a `AreaManagerPolicy`, que queda como alias. `is_manager` pregunta `manage` y `can_read`
  pregunta `read`.
- **Documentos y proyectos.** `AreaService.authorize(slug, principal, module, action)` pasa el módulo
  y la acción. `module=None` conserva la regla anterior de pertenecer al área.
- **Moderador.** Pasa toda lectura. En PQRSF ve todos los casos sin trabajarlos.

## Panel de permisos (UI: `/ti/administracion/permisos`)

| Método y ruta | Quién | Uso |
| --- | --- | --- |
| `GET /permissions/catalog` | autenticado | Módulos que puede conceder cada área, las acciones y el comodín |
| `GET /permissions/me` | autenticado | Rol global y, por área, `{módulo: acción}`. La UI arma el menú con esto |
| `GET /areas/{slug}/roles/{role_id}/permissions` | admin o moderador | Permisos de un rol |
| `PUT /areas/{slug}/roles/{role_id}/permissions` | admin | `{grants: [{module, action}]}` reemplaza todos los permisos. 422 si el módulo no existe en el área |
| `GET /permissions/users/{user_id}` | admin o moderador | Áreas, rol y permisos de una persona |
| `GET /permissions/audit` | admin o moderador | Bitácora: `area_slug`, `target_user_id`, `action`, `offset`, `limit` |
| `GET /areas` | autenticado | Cada área trae `my_modules` |
| `/areas/{slug}/roles`, `/areas/{slug}/members` | admin | Crear roles y asignar personas |
| `PATCH /users/{id}` | admin. Solo el owner cambia el rol | Rol global y estado |

La bitácora (`permission_events`) registra estas acciones: `area_created`, `role_created`,
`role_deleted`, `role_permissions_set`, `member_added`, `member_role_changed`, `member_removed`,
`global_role_changed`, `user_deactivated` y `user_activated`. Cada registro guarda el actor, el área,
la persona y el detalle de antes y después.

## Despliegue

- **Migración `0041_permissions`.** A **todo rol de área existente** le da `*` manage. Hasta ahora
  cualquier miembro gestionaba todo en su área, así que el despliegue no cambia lo que hacen los
  miembros. Después, los admins estrechan cada rol desde el panel.
- **Lecturas cerradas.** Quien no es miembro de ningún área pierde las lecturas que antes estaban
  abiertas: lotes del validador de pedidos, apoyos y adjuntos, maestros comerciales, contactos de
  proveedores y sugerencias de PQRSF.
- **Pruebas.** En las pruebas, los gates de lectura pasan por defecto (`tests/conftest.py`), igual que
  el contador de tráfico. Un módulo que prueba los gates declara `REAL_PERMISSION_GATES = True`.
