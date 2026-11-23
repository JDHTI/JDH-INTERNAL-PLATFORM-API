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
- **Rol líder.** Cada área tiene exactamente un rol líder (`area_roles.is_leader`, índice único
  parcial `uq_area_roles_one_leader`). Toda área nueva nace con él, como "Líder de área".
  - Tiene todos los permisos del área (`*` manage). La resolución lo trata como acceso completo
    aunque falte la fila de permisos.
  - No se puede eliminar ni cambiar sus permisos: `DELETE` y `PUT .../permissions` responden 409.
  - Se puede renombrar (en BI se llama "Director BI").
  - La API lo marca con `is_leader` en `GET /areas/{slug}/roles` (sale primero), en `my_role` de
    `GET /areas` y en `/permissions/me`.
- **Los demás roles.** Son libres: se crean sin permisos y aceptan cualquier módulo del área en
  `read` o `manage`, o el área completa con `*`. Se pueden renombrar y borrar si no tienen miembros.
- **Un rol por área.** Una persona tiene un rol por área (`area_members`).
- **Personas que nunca han entrado.** La sincronización del directorio crea a cada cuenta de Entra
  como usuario, así que se asigna por correo a quien nunca ha iniciado sesión. El primer inicio de
  sesión reutiliza ese usuario (mismo tenant y object id) y conserva sus áreas.
- **Cambios en vivo.** Los permisos se resuelven en cada request, sin caché: un cambio aplica en la
  siguiente llamada de la persona.
- **Responsable.** El responsable del área gestiona toda su área.
- **Reglas por registro.** Siguen dentro de cada módulo: el caso asignado o creado en PQRSF, la cotización propia o el proyecto creado.

### Catálogo (`app/modules/areas/domain/catalog.py`)

La columna "Área" es dónde está activo cada módulo **por defecto**. Un administrador lo activa o
desactiva en cualquier área desde el panel (ver "Módulos por área").

| Área | Módulo | `read` | `manage` |
| --- | --- | --- | --- |
| todas | `documentos` | carpetas, búsqueda, descarga | crear, editar, borrar, colores de etiqueta |
| ti | `proyectos` | proyectos, épicas, tareas, tablero en vivo | crear, editar, archivar. El borrado sigue siendo del creador, del responsable o de un admin |
| ti | `monitoreo` | tablero general y aplicativos | registrar, editar, borrar, chequear |
| ti | `datawarehouse` | las pantallas del monitoreo del DWH | pedir una carga |
| calidad | `pqrsf` | todos los casos en solo lectura, sugerencias, contactos, feedback, certificados, envíos | asignar, catálogos, ajustes, certificados, envíos |
| calidad | `planes-accion` | toda la matriz P.04, indicadores y exportación a Excel | crear acciones (a mano o desde un caso), correcciones, causas, plan, calificar eficacia, nuevo intento, procedencias. Sin el módulo, un responsable ve sus acciones, marca sus filas y sube evidencias |
| comercial | `cotizador` | cotizaciones, clientes, reglas, exportar | cotizar |
| bi | `cotizador` | igual | validar, devolver y marcar como cargada |
| call-center | `cotizador` | sus cotizaciones (el líder, las de todo el área); cualquier cliente | cotizar a cualquier cliente (rol «Operador call center», 2026-11-23) |
| comercial (bloqueado) | `comercial-director` | — (no tiene pantalla) | director (2026-11-23): ve todas las cotizaciones de cliente, clientes y apoyos, y edita solo las suyas. Solo cuenta concedido **por nombre**; `*` no lo da. El líder y el responsable de Comercial lo tienen sin hacer nada. Un coordinador no necesita clave: su vínculo de supervisor SIESA en `/ti/administracion/vendedores` le da su equipo |

**Eliminar para siempre** (2026-11-23):
- **Qué incluye:** borrado masivo de apoyos y borrado de solicitudes de apoyo (JDH e internas).
- **Quién:** solo el **líder del área de apoyos** (BI: su rol líder o su responsable) y los
  **administradores de la plataforma** (owner, admin). No lo da ningún permiso de módulo, ni
  siquiera `*` manage; el moderador tampoco.
- **Dónde está:** `AreaLeaderPolicy` en `areas/infrastructure/policies.py`.
| bi | `actualizacion-listas` | actualizaciones de listas, búsqueda de listas, staging y cargas | crear y editar actualizaciones de cualquier lista **salvo las principales** y marcar ítems «Por validar». Desde un rol de BI (`QUOTATIONS_APPROVER_AREA_SLUG`): validar ítems, descargar el staging y marcarlo cargado |
| bi (bloqueado) | `listas-principales` | el tablero de las listas principales (`LIST_UPDATES_MAIN_LISTS`), staging, cargas e impuesto licor | armar y refrescar las listas, poner precios en cualquiera de ellas y marcar ítems «Por validar» |
| bi (bloqueado) | `validacion-listas` | — (no tiene pantalla) | validar los ítems de las listas principales, descargar el staging y marcarlo cargado (analistas y líder de BI) |
| bi (bloqueado) | `impuesto-licor` | — (no tiene pantalla; también abre la vista del impuesto) | cambiar el impuesto licor de todas las listas y generar su plano de inmediato, sin validación; marcarlo cargado o anularlo (colaboradores puntuales) |
| bi, comercial | `validador-pedidos` | lotes, órdenes, reglas | cargar, aprobar, umbral, revalidar, borrar. `manage` vale desde un rol de BI o de Comercial. `ORDER_VALIDATION_MANAGER_AREA_SLUG` lo limita a un área si se fija |
| bi, comercial | `apoyos` | maestro de apoyos, perfiles, adjuntos | crear, editar, anular, cargas masivas |
| comercial, bi | `solicitudes-apoyos` | solicitudes (en comercial, solo las propias) | comercial solicita; bi aprueba y rechaza |
| bi (se enciende en otras áreas para designar) | `aprobacion-apoyos-internos` | — (no tiene pantalla) | aprobar o rechazar los apoyos internos pedidos desde la cotización (persistente o única vez). También deciden los gestores de `apoyos` en BI. Quien cotiza los pide (también un aprobador, que no aprueba los propios) |
| comercial | `mapa-comercial` | mapa de prospectos, ficha, zonas, rutas | cargar el archivo mensual, corregir ubicación, marcar visitas, crear y editar zonas y rutas. El responsable de una ruta marca sus visitas sin `manage` |
| bi | `maestros` | los maestros activos en el área (`/masters`) y los comerciales (`/commercial`); descarga | editar producto o proveedor, cargar márgenes; ver los campos personales |
| abastecimiento | `analisis-entradas` | entradas de compra contra la cotización vigente (`/costs/entries`), motivos | asignar el motivo de una línea |
| abastecimiento | `novedades-compras` | bandeja de novedades (`/costs/novelties`), su historial y la nota crédito | asignar motivo, registrar o quitar la nota crédito, editar el catálogo de motivos |
| abastecimiento | `alzas-proveedor` | solicitudes de alza de los proveedores, plantilla | cargar, crear, editar, aprobar y rechazar alzas |
| abastecimiento | `acumulado-alzas` | histórico y análisis de alzas (`/commercial/price-increases`) | poner o quitar la fecha de SIESA, cargar el libro (igual que `maestros` en BI) |

`/commercial/*` también lo leen `apoyos`, `cotizador`, `actualizacion-listas`,
`validador-pedidos` y las cuatro claves de Costos, porque esas pantallas buscan proveedores y
productos. El área Abastecimiento la crea la migración `0081` con tres roles: «Líder de área»
(`*`), «Analista de costos» (las cuatro claves) y «Comprador» (`novedades-compras`).

`/quotations/*` lo abren `cotizador`, `actualizacion-listas` y `listas-principales`: comparten
los endpoints, y el servicio revisa cada cotización contra el módulo de su tipo (`kind`), así que
tener uno no abre las de los otros. `/price-list-updates/*` (revisión por ítem, staging, cargas e
impuesto licor) lo abren `actualizacion-listas` y `listas-principales`.

**Módulo bloqueado** (`locked=True` en el catálogo): solo se enciende en sus áreas por defecto. El
panel no lo deja encender en otra (`set_area_module` responde 422) y una fila guardada en otra área
no cuenta. `listas-principales`, `validacion-listas` e `impuesto-licor` están bloqueados a BI, así
que un comercial nunca los recibe, aunque tenga `actualizacion-listas`.

**Agregar un módulo** sigue la skill del proyecto
[`modulo-con-permisos`](../.claude/skills/modulo-con-permisos/SKILL.md): catálogo, gate, política,
contenedor, límite de tráfico, plantilla y página en la UI, y esta tabla.

Un permiso guardado sobre un módulo que salió del catálogo no da nada: `my_modules` y los permisos
de un rol o de una persona no lo muestran, y desaparece de la base la próxima vez que se guardan los
permisos del rol. No hace falta migración para retirar un módulo.

**No son módulos.** La administración de la plataforma (`/users*`, `/roles`, `/sites`,
`/directory/admin/*`, `/pqrsf/requests*`, `/jobs`, escrituras de áreas, roles, miembros, permisos y
módulos) solo depende del rol global. Un rol de área nunca debe conceder administración.

## Módulos por área (`area_modules`)

- **Activar o desactivar.** `PUT /areas/{slug}/modules/{module}` con `{enabled, config}` guarda la
  decisión de un administrador. Sin fila, vale el catálogo, así que la migración `0046` no cambia nada.
- **Volver al valor por defecto.** `DELETE /areas/{slug}/modules/{module}` borra la decisión y el
  módulo sigue de nuevo al catálogo (`is_default=true`). Es idempotente.
- **Qué cambia al desactivar.** El módulo sale de `my_modules` de todos (el menú) y del catálogo de
  permisos del área. Los gates lo cierran a los miembros, también al líder; owner, admin y moderador
  siguen pasando. Los permisos de los roles que lo mencionan **se conservan** y vuelven a valer al
  reactivarlo.
- **Qué cambia al activar.** El área puede conceder el módulo a sus roles (antes daba 422). El
  líder y el responsable lo tienen de una vez.
- **Configuración.** Solo `maestros` la lleva: `{"masters": ["clientes", "proveedores"]}` limita el
  área a esos maestros; sin lista, todos. Los demás módulos no aceptan configuración (422).
- **Bitácora.** Cada cambio queda como `module_toggled` con antes y después; el restablecimiento lleva
  además `reset: true`.
- **Proyectos.** Ya no usa `PROJECTS_AREA_SLUGS`: se habilita por área como cualquier módulo.
- **Significado de `manage`.** Donde un módulo tiene dueño (`QUOTATIONS_*`, `PQRSF_MANAGER_AREA_SLUG`,
  `SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG`, `ti` para monitoreo y datawarehouse), el setting sigue
  decidiendo qué área gestiona. La activación decide dónde se ve y se lee.

## Cómo se aplica

- **Lectura en la presentación.** `require_permission(*módulos, action="read")`
  (`app/modules/areas/presentation/dependencies.py`) protege los routers. Basta con tener uno de los
  módulos en cualquier área donde exista.
  - Va a nivel de router en commercial, perfiles, cargas y adjuntos de apoyos,
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
| `GET /permissions/catalog` | autenticado | Módulos activos que puede conceder cada área, las acciones y el comodín |
| `GET /permissions/modules` | admin o moderador | Todos los módulos y, por área, `{enabled, config, is_default}` |
| `PUT /areas/{slug}/modules/{module}` | admin | `{enabled, config}`. 404 si el módulo no existe; 422 si la configuración no aplica |
| `DELETE /areas/{slug}/modules/{module}` | admin | Vuelve al valor del catálogo y responde ese estado. 404 si el módulo no existe |
| `GET /permissions/me` | autenticado | Rol global y, por área, `{módulo: acción}`. La UI arma el menú con esto |
| `GET /areas/{slug}/roles/{role_id}/permissions` | admin o moderador | Permisos de un rol |
| `PUT /areas/{slug}/roles/{role_id}/permissions` | admin | `{grants: [{module, action}]}` reemplaza todos los permisos. 422 si se da un permiso nuevo sobre un módulo que no existe o está apagado en el área; 409 en el rol líder. Desde el 2026-11-19, un permiso ya guardado sobre un módulo apagado se conserva tal cual, se reenvíe o no (no cuenta mientras esté apagado), y uno sobre un módulo retirado del catálogo se borra |
| `GET /permissions/users/{user_id}` | admin o moderador | Áreas, rol y permisos de una persona |
| `GET /permissions/audit` | admin o moderador | Bitácora: `area_slug`, `target_user_id`, `action`, `offset`, `limit` |
| `GET /areas` | autenticado | Cada área trae `my_modules` |
| `/areas/{slug}/roles`, `/areas/{slug}/members` | admin | Crear roles y asignar personas |
| `PATCH /users/{id}` | admin. Solo el owner cambia el rol | Rol global y estado |

La bitácora (`permission_events`) registra estas acciones: `area_created`, `role_created`,
`role_renamed`, `role_deleted`, `role_permissions_set`, `member_added`, `member_role_changed`, `member_removed`,
`module_toggled`, `global_role_changed`, `user_deactivated` y `user_activated`. Cada registro guarda el actor, el área,
la persona y el detalle de antes y después.

## Despliegue

- **Migración `0041_permissions`.** A **todo rol de área existente** le da `*` manage. Hasta ahora
  cualquier miembro gestionaba todo en su área, así que el despliegue no cambia lo que hacen los
  miembros. Después, los admins estrechan cada rol desde el panel.
- **Migración `0044_area_leader_role`.** Marca el rol líder de cada área: el que tiene "líder" o
  "director" en el nombre; si no hay, "Acceso completo", renombrado "Líder de área"; si tampoco hay,
  uno nuevo. Le deja exactamente `*` manage. Los demás roles conservan sus permisos.
- **Migración `0046_area_modules`.** Crea `area_modules` vacía: todo sigue como el catálogo.
- **Lecturas cerradas.** Quien no es miembro de ningún área pierde las lecturas que antes estaban
  abiertas: lotes del validador de pedidos, apoyos y adjuntos, maestros comerciales, contactos de
  proveedores y sugerencias de PQRSF.
- **Pruebas.** En las pruebas, los gates de lectura pasan por defecto (`tests/conftest.py`), igual que
  el contador de tráfico. Un módulo que prueba los gates declara `REAL_PERMISSION_GATES = True`.
