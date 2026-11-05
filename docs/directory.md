# Directorio de empleados

Primer paso de la centralización de la intranet en la plataforma
([`intranet-centralization.md`](intranet-centralization.md)). El módulo `directory` copia cada
noche los usuarios de Entra, crea su usuario de la plataforma y sirve el directorio que la
vista general de la UI (`/directorio`, cumpleaños, nuevos ingresos) va a mostrar.

Reemplaza los módulos `directorio` y `sedes` de INTRANET-JDH-API. No migra Better Auth, su
caché de Redis ni sus niveles de throttling: usa el login, Redis y los límites de la plataforma.

## Modelo

| Tabla | Qué guarda |
| --- | --- |
| `directory_employees` | Una persona. Si viene de Entra tiene `entra_object_id` y `user_id` (su fila en `users`); si se agregó a mano no tiene ninguno de los dos (CHECK `ck_directory_employees_identity_pair`) |
| `directory_sites` | Sedes físicas. Se llama distinto de `pqrsf_sites` (catálogo de PQRSF) a propósito |
| `directory_sync_runs` | Una fila por sincronización. El índice único parcial `uq_directory_sync_runs_running` deja una sola en curso |

`directory` nunca escribe la tabla `users`: crea y bloquea usuarios con el puerto
`UserProvisioning` de identity (`app/modules/identity/application/provisioning.py`). Guarda
su propia copia del nombre, el correo y el cargo de Entra para listar sin cruzar módulos.

### Quién es dueño de cada campo

| Campo | Dueño |
| --- | --- |
| `full_name`, `email`, `job_title`, `department`, `is_active`, `hire_date`, `manager_id` | Entra, en cada sincronización. Un PATCH del administrador los ignora en empleados de Entra |
| `office_location` | Entra (texto crudo de `officeLocation`, para ver diferencias) |
| `site_id` | Entra solo al crear (por nombre de `officeLocation`); después, el directorio |
| `position` (cargo SIESA), `birth_date` | Importación desde la intranet; más adelante, el DWH. Editables por el administrador |
| `visibility`, `is_manager` | Administrador. `is_manager` también se enciende si alguien activo le reporta |
| `photo_*` | Entra, por la versión (`@odata.mediaEtag`) de la foto |

## Visibilidad

El directorio público muestra a un empleado cuando está activo y además:

- `visibility = shown`, o
- `visibility = auto` y `auto_visible`: la cuenta está habilitada, es `Member` (no invitado) y
  su correo es de un dominio de `DIRECTORY_CORPORATE_DOMAINS`.

Los empleados manuales tienen `auto_visible = true`. `visibility = hidden` saca del directorio
buzones compartidos y cuentas de servicio que pasan la regla (en la primera carga real
aparecieron, por ejemplo, `INFO` y `PQRSF`). Ocultar no bloquea el usuario de la plataforma.

Los invitados de Entra (`userType = Guest`) no crean empleado ni usuario.

## Sincronización

`DirectorySyncService.execute` (`app/modules/directory/application/sync.py`):

1. Lista `/users` con `$select` y `$top=999`, siguiendo `@odata.nextLink`. `birthday` no se
   pide: con permisos de aplicación hace fallar toda la consulta.
2. Por cada miembro, crea o refresca su usuario de la plataforma y luego su empleado. Al crear,
   el cargo queda vacío y la sede sale de `officeLocation`.
3. En lotes de `DIRECTORY_SYNC_BATCH_SIZE`: jefe (`/users/{id}/manager`), fecha de ingreso
   (`employeeHireDate`, o `createdDateTime` si falta) y foto. La foto se baja en 96 y 432 px
   solo si cambió su versión, y se borra si Entra ya no la tiene. Un error de una persona se
   cuenta en `errors` y la corrida sigue.
4. Desactiva a los empleados de Entra que ya no aparecen y bloquea sus usuarios.
5. Marca `is_manager` a quien tenga reportes activos.

Un error al listar o escribir deja la corrida en `failed` con `error_code` y `error_message`.

### `users.is_active`

La sincronización solo escribe flancos, para que un bloqueo manual del administrador sobreviva
la noche:

| Situación | Efecto en `users.is_active` |
| --- | --- |
| La cuenta está deshabilitada en Entra | `false` |
| La cuenta estaba deshabilitada y vuelve a estar habilitada | `true` |
| La persona ya no aparece en Entra | `false` |
| Cualquier otro caso | No se toca |

Nunca bloquea al último administrador activo (`counts.last_admin_protected`). Los usuarios de
desarrollo y del seed no tienen empleado, así que la sincronización no los toca. El usuario se
crea en el tenant del SSO (`ENTRA_TENANT_ID`, o `development`), el mismo que usa el login.

Al iniciar sesión, identity refresca ahora el nombre, el correo y el cargo de un usuario
existente si cambiaron en Entra. Antes quedaban como en el primer ingreso.

### Programación

- `DirectorySyncWorker` corre a `DIRECTORY_SYNC_AT` en `DIRECTORY_SYNC_TIMEZONE` (23:59 en
  Bogotá por defecto).
- Al arrancar cierra como `failed` (`interrupted`) las corridas que un proceso detenido dejó en
  curso. Con `DIRECTORY_SYNC_CATCH_UP`, sincroniza una vez si no hubo éxito en 24 horas.
- `POST /directory/admin/sync` registra la corrida y despierta al worker con su id. Responde
  409 si la sincronización está apagada o ya hay una en curso.
- El script `scripts/sync-directory.py` corre una sincronización completa aunque el worker esté
  apagado. La base impide que el script y el worker se pisen.

## Configuración

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `DIRECTORY_GRAPH_TENANT_ID` | `ENTRA_TENANT_ID` | Tenant del registro de aplicación |
| `DIRECTORY_GRAPH_CLIENT_ID` | — | Registro con el permiso de aplicación `User.Read.All` consentido |
| `DIRECTORY_GRAPH_CLIENT_SECRET` | — | Su secreto. Nunca va a Git |
| `DIRECTORY_SYNC_ENABLED` | `false` | Enciende el worker. Exige las credenciales |
| `DIRECTORY_SYNC_AT` | `23:59` | Hora local diaria (HH:MM) |
| `DIRECTORY_SYNC_TIMEZONE` | `America/Bogota` | Zona de la hora y de "hoy" |
| `DIRECTORY_SYNC_CATCH_UP` | `true` | Sincroniza al arrancar si no hubo éxito en 24 h |
| `DIRECTORY_SYNC_BATCH_SIZE` | `10` | Personas cuyo jefe y foto se leen a la vez |
| `DIRECTORY_CORPORATE_DOMAINS` | `jdh.com.co` | Dominios visibles por defecto |

El registro de aplicación puede ser distinto del del login. Hoy el directorio usa el de la
intranet (`INTRANET-JDH-DEV`, con `User.Read.All`). El de la plataforma
(`JDH-INTERNAL-PLATFORM`) no tiene permisos de aplicación: con él Graph responde 403.

## Endpoints (`/api/v1`)

Toda persona autenticada:

| Método | Ruta | Parámetros | Respuesta |
| --- | --- | --- | --- |
| GET | `/directory/employees` | `q`, `site_id`, `department`, `page`, `size` (1–100, 24) | `{items, total, page, size}` |
| GET | `/directory/employees/me` | — | Perfil propio; 404 hasta la sincronización |
| GET | `/directory/employees/birthdays` | `month` (1–12); sin él, el año completo | `{month, items}` por mes y día, con `birth_month` y `birth_day`. `month` es `null` para el año |
| GET / POST | `/directory/employees/me/onboarding` | POST: `{siteId?, mobilePhone?, landlinePhone?, showInDirectory?}` | `{pendientes: ("sede"\|"contacto")[], siteId, mobilePhone, landlinePhone, showInDirectory, sedes: [{id, nombre}]}`. Es el contrato de la intranet, en camelCase. 404 sin empleado |
| GET / PUT | `/directory/employees/me/contact-schedule` | PUT: `{franjas: [{dia, inicio, fin}]}` | El horario propio. PUT lo reemplaza completo; 422 si una franja se cruza o termina antes de empezar |
| GET | `/directory/employees/{id}/contact-schedule` | — | Horario de un empleado público |
| GET | `/directory/employees/new-hires` | `limit` (1–24, 6) | `{items}` con `hire_date`, del más reciente |
| GET | `/directory/employees/{id}` | — | Perfil; 404 si no es público |
| GET | `/directory/employees/{id}/team` | `limit` (1–24, 3) | Mismo departamento |
| GET | `/directory/employees/{id}/photo` | `size` (96 o 432), `v` | Imagen con `ETag`; 304 con `If-None-Match` |
| GET | `/directory/sites` | — | Sedes activas con `employee_count` público |

`q` busca sin tildes por inicio de palabra, en cualquier orden, sobre nombre, cargo, cargo de
Entra y correo: `per mar` encuentra a "María Pérez". La columna `search_text` guarda el texto
normalizado, así que no hace falta la extensión `unaccent`.

Completar perfil pide dos cosas: la sede (`sede`, si el empleado no tiene) y el celular
(`contacto`, si no lo registró).
- **Sede.** Solo acepta sedes activas.
- **Teléfonos.** Se guardan como se escribieron. Admiten números, espacios, `+`, `-` y paréntesis,
  y deben tener al menos 7 dígitos.
- **Celular en el perfil.** El perfil público muestra `landline_phone` siempre. `mobile_phone` solo
  aparece cuando la persona marcó `showInDirectory`.
- **Horario de contacto.** Usa días de `LUNES` a `DOMINGO` y horas `HH:MM` (`directory_contact_schedules`).

La foto se pide con `v = photo_version` del empleado. Con `v` se responde
`Cache-Control: private, max-age=31536000, immutable`; sin `v`, una hora. El perfil público
muestra el día y el mes de nacimiento, no el año.

Administradores (`require_admin`):

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/directory/admin/employees` | Todo el directorio; `include_hidden` (sí), `include_inactive` (no) |
| POST | `/directory/admin/employees` | Persona que no está en Entra |
| GET / PATCH / DELETE | `/directory/admin/employees/{id}` | DELETE responde 409 si viene de Entra |
| POST | `/directory/admin/sync` | 202 `{run_id}`; nivel de límite `strict` |
| GET | `/directory/admin/sync` | `{enabled, scheduled_at, timezone, next_run_at, running, latest}` |
| GET | `/directory/admin/sync/runs` | Historial; `limit` 1–100 |
| GET / POST | `/directory/admin/sites` | Sedes, también inactivas |
| PATCH / DELETE | `/directory/admin/sites/{id}` | DELETE responde 409 mientras tenga empleados |

## Puesta en marcha

1. `poetry run alembic upgrade head`. La migración `0039_merge_heads` une las dos cabezas
   `0038`, y `0040_directory` crea las tablas.
2. Configurar `DIRECTORY_GRAPH_*` con el registro de la intranet.
3. `poetry run python scripts/sync-directory.py` para la primera carga.
4. `poetry run python scripts/import-intranet-directory.py "<dsn de intranet_jdh>"` revisa la
   importación. Con `--apply` la escribe. Trae sedes, cargo, fecha de nacimiento, sede,
   `is_manager`, la marca del DWH y las personas sin Entra. Omite la sede comodín
   "Sin sede asignada" y se puede repetir.
5. Ocultar los buzones compartidos que pasen la regla.
6. `DIRECTORY_SYNC_ENABLED=true` y reiniciar la API.

Primera carga contra Entra (2026-11-04, base local):

| Dato | Valor |
| --- | --- |
| Miembros sincronizados | 486 |
| Invitados omitidos | 153 |
| Fotos descargadas | 86 |
| Errores | 0 |
| Duración | ~60 s |
| Visibles en el directorio público | 451 |

## Pendiente

- La UI (vista general) consume este contrato. Está en internal-platform-ui.
- `GET /auth/me/photo` sigue usando la foto que identity baja por OBO en el primer ingreso. El
  siguiente paso es que use la foto del directorio y retirar esa descarga.
- Cargo y fecha de nacimiento desde el DWH en lugar de la importación.
- Notificaciones (campana y push).
