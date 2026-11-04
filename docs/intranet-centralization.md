# Centralización de la intranet en internal-platform

Fecha: 2026-11-04. Estado: re-evaluado el mismo día (sección 0). La fase 1, directorio y
provisión de usuarios, está implementada en [`directory.md`](directory.md).

## 0. Re-evaluación: un solo aplicativo corporativo

El objetivo cambió. Ya no es solo ahorrar máquinas: es tener **un único aplicativo
corporativo**. Las secciones 1 a 8 son el análisis original. Esta sección lo reemplaza donde
se contradicen.

### Veredicto

Centralizar en internal-platform, por módulos, sin portar la UI de la intranet. Strapi queda
como backend de contenido (noticias, eventos, documentos corporativos) que la plataforma lee.
Su panel ya era el editor real. La UI y la API NestJS de la intranet se apagan cuando sus
funciones existan en la plataforma.

- **La estimación C asumía un porte.** Los 85–125 días suponían trasladar 27 000 líneas de UI
  con su kit, Lenis y la home editorial. La vista general se construye nativa, con Base UI y
  React Query. De la intranet se reutilizan el contrato y las reglas, no el código.
- **Buena parte no se migra.** Eventos, certificados y vacaciones sin uso, admin vacíos,
  búsqueda rota, Better Auth, su throttling y su caché de Redis.
- **La plataforma ya tiene lo caro.** SSO con Entra, cliente Graph de aplicación, workers, S3,
  Redis, límites de tráfico, áreas y roles.
- **Dos sistemas cuestan más.** Dos logins, dos listas de personas que no se hablan y dos
  shells. Los permisos de la plataforma necesitan el directorio de todos modos.

Sigue vigente: Strapi no se reescribe, la máquina se apaga solo cuando Strapi se mude (B1), y
falta definir el despliegue de la plataforma (B3).

### Fases

| Fase | Qué | Repo | Estado |
| --- | --- | --- | --- |
| 1 | Directorio desde Graph, provisión de usuarios, sedes, fotos, historial de sincronización e importación desde la intranet | API | Hecha ([`directory.md`](directory.md)) |
| 2 | Vista general: `/` con cumpleaños y nuevos ingresos, `/directorio`, `/directorio/[id]`; menú General y Espacio de trabajo | UI | Siguiente |
| 3 | Cargo, nacimiento y sede desde el DWH; visibilidad definitiva | API | Confirmar si el DWH expone empleados |
| 4 | `/noticias`, `/eventos`, `/documentos`: proxy `/content/*` de Strapi en la API, con caché | API + UI | Decidido el proxy; depende de B1 |
| 5 | Perfil y onboarding (contacto, horarios, sede), notificaciones e ingesta desde Strapi | API + UI | — |
| 6 | Redirecciones 301 desde `intranet.jdh.local` y apagado de la UI y la API de la intranet | Infra | — |

### Rutas y shell

- **Vista general para toda persona autenticada.** `/` deja de redirigir a la primera área y
  muestra la home. Junto a `/configuracion` y `/sugerencias` van `/directorio`, `/noticias`,
  `/eventos`, `/documentos` y `/perfil`. Una persona sin áreas deja de ver una pantalla vacía.
- **Los módulos productivos se quedan en la raíz**, sin prefijo `/workspaces`. Siguen en
  `/calidad/pqrsf` o `/comercial/cotizador`. El espacio de trabajo es un bloque del menú, no un
  segmento de URL: aparece debajo de General solo si la persona tiene áreas.
- **Motivo de mantener la raíz.** Las URL actuales ya circulan en los correos de PQRSF y en
  marcadores. Un prefijo obligaría a redirecciones sin ganancia funcional. Si algún día se
  quiere, la opción coherente es `/espacios/[area]` con 301.
- **Slugs reservados.** En Next, una ruta fija gana a `[area]`, así que la API ya rechaza áreas
  con esos nombres (`RESERVED_SLUGS` en `app/modules/areas/domain/entities.py`).
- **`/documentos` no choca con `/[area]/documentos`.** El primero muestra los documentos
  corporativos de Strapi y el segundo el gestor por área.
- **La autenticación no cambia para la UI.** Sigue sin BFF y llama a la API con cookies.

### Decisiones del usuario sobre la UI y el contenido (2026-11-04)

- **Dos frentes en una sola app.** La vista general y el espacio de trabajo. Nada queda fuera
  de esos dos frentes.
  - La vista general es el route group `(general)`: `/`, `/directorio`, `/directorio/[id]`,
    `/configuracion` y `/sugerencias`; después `/noticias`, `/eventos` y `/documentos`.
  - El espacio de trabajo son las áreas.
- **La administración pasa a ser un módulo del área TI.** Vive en
  `/ti/administracion/{areas,usuarios,maestros,directorio,sedes}` y es solo para
  administradores. `/administracion` redirige allí, y el slug `administracion` sigue reservado.
  Los endpoints no cambian: siguen exigiendo el rol `admin` de la plataforma, no la membresía
  en TI.
- **Strapi se consume solo por la API, con un proxy `/content/*` y caché.** Strapi queda
  privado y solo alimenta la interfaz gráfica de la plataforma. Esto es la fase 4.
  - El contrato propuesto está en `docs/intranet-centralization.md`, sección 2, del repo
    internal-platform-ui.
  - El traspaso desde intranet-ui sobre Strapi va en `docs/intranet-strapi-handoff.md` de ese
    mismo repo.
  - Riesgo para la fase 4: el tipo de contenido `quinquenio` no está commiteado en master de
    INTRANET-JDH-CMS.

### Decisiones de la fase 1

- La sincronización crea los usuarios de la plataforma antes del primer ingreso. Así las áreas
  pueden agregar a cualquier empleado, y quien sale de Entra queda bloqueado.
- La visibilidad pública usa una regla automática (cuenta habilitada, miembro, dominio
  corporativo). El administrador puede mostrar u ocultar a cada persona.
- El login sigue con el registro de la plataforma (`ENTRA_*`). El directorio usa el de la
  intranet (`DIRECTORY_GRAPH_*`, con `User.Read.All`). B2 queda resuelto sin tocar Entra.
- B4 queda resuelto: las sedes físicas son `directory_sites` en `/directory/sites`, separadas
  de `pqrsf_sites`.
- Redis, S3 y los límites de tráfico de la intranet no se migran. Se usan los de la plataforma.

Objetivo: ahorrar máquinas y dejar un solo portal. La API de la intranet
(INTRANET-JDH-API) tiene poca carga: sobre todo directorio y cumpleaños. El directorio
se seguirá usando para asignar permisos en la plataforma. El frontend de la intranet
(INTRANET-JDH-UI) usa en apariencia el mismo stack que internal-platform-ui (Next.js,
React, TanStack Query, Tailwind).

El análisis se hizo leyendo el código de los cuatro repos (intranet API y UI, plataforma
API y UI) y sus archivos de despliegue. No se consultaron el servidor ni las bases de
producción: los volúmenes son estimaciones.

## 1. Conclusión

**Fusionar el código cuesta mucho y por sí solo no apaga ninguna máquina.** El servidor
de la intranet (`intranet.jdh.local`, Debian 13) corre siete contenedores:

| Contenedor | Qué es |
| --- | --- |
| `caddy` | Proxy en 80/443 con una CA interna (`*.jdh.local`) repartida por GPO |
| `ui` | Next.js 16 (más el one-shot `ui-auth-migrate` de Better Auth) |
| `api` | NestJS 11 + Prisma (más el one-shot `api-migrate`) |
| `cms` | Strapi 5, con su volumen de medios `cms_uploads` |
| `postgres` | PostgreSQL 17 con dos bases: `intranet_jdh` y `intranet_cms` |
| `redis` | Redis 7: caché del directorio y límites de tráfico |
| `storage` | RustFS (S3) con las fotos de los empleados |

Mientras Strapi siga en esa máquina, la máquina sigue encendida aunque se migren la API
y la UI.

## 2. Caminos y costo

| Camino | Esfuerzo | ¿Libera la máquina? | ¿Centraliza el portal? |
| --- | --- | --- | --- |
| **A. Mudar los contenedores de la intranet al servidor de la plataforma**, sin tocar código (Postgres y Redis compartidos, cada uno con su base) | ~4–7 días de infraestructura | **Sí** | No |
| **B. Un solo dominio y proxy**: la intranet bajo `/intranet` (`basePath`) o un subdominio hermano, enlazada desde la barra lateral de la plataforma | 3–5 días | No | Sí, un solo punto de entrada |
| **C. Fusión completa**: API a FastAPI y UI dentro de internal-platform-ui | API 25–35 + UI 60–90 ≈ **85–125 días-persona** | Solo si además se muda o se reemplaza Strapi | Sí |

A y B juntos dan casi todo el ahorro y la centralización por menos de dos semanas. Las
dos aplicaciones ya inician sesión con Entra, así que con B la persona no vuelve a
escribir credenciales.

C es una reescritura, no una migración: el stack coincide solo en la superficie.

- **Kit de UI:** la intranet usa Radix/shadcn y unos 70 componentes propios `jdh-*`; la
  plataforma usa Base UI.
- **Autenticación:** la intranet usa Better Auth en el servidor Next, con sesiones en
  Postgres que la API NestJS valida por Bearer. La plataforma usa su propio flujo OIDC
  con Entra y una cookie HttpOnly que el navegador envía a la API.
- **Shell y diseño:** la intranet tiene header, mega menú, footer, scroll de documento,
  Lenis y una home editorial. La plataforma tiene barra lateral, scroll interno y
  `MOTION 1` en `DESIGN.md`.
- **Datos:** la intranet lee en Server Components (Strapi y NestJS); la plataforma lee
  con React Query desde el navegador.
- **Pruebas:** la intranet no tiene pruebas automáticas, así que la regresión sería manual.

## 3. Inventario de la API de la intranet

NestJS 11 (Express 5), Prisma 7 sobre PostgreSQL 17, Redis, S3 (RustFS), `@nestjs/schedule`
y `web-push` (apagado por defecto). Prefijo `/api/v1`, unas 45 rutas. Solo la llaman la
UI y Strapi por la red interna de Docker.

| Módulo | Uso | Consumidor |
| --- | --- | --- |
| Directorio | Empleados: búsqueda sin tildes, cumpleaños, nuevos ingresos, equipo por jefe, foto | UI: directorio, home, perfil, avatares |
| Directorio (admin) | CRUD de empleados, sincronización con Entra, historial | UI: `/admin/directorio` |
| Sedes | Sedes físicas y su dirección | UI: `/admin/sedes`, onboarding |
| Onboarding, contacto, horarios | Perfil propio del empleado | UI: `/completar-perfil`, `/perfil` |
| Notificaciones | Campana, leídas, Web Push | UI |
| Ingesta de notificaciones | Publicaciones de Strapi, protegidas con el secreto `x-jdh-ingest-secret` | Strapi |
| Health | `live` y `ready` | Despliegue |

Tareas programadas: sincronización completa del directorio con Graph a las 23:59
(`User.Read.All` en modo aplicación) y despacho de notificaciones programadas cada minuto.

Datos con uso real: `employees` (unos 630 usuarios de Entra; el directorio público solo
muestra los cruzados con el DWH), `employee_details`, `sites`, `contact_info`,
`contact_schedules`, `directory_sync_runs`, `notifications`, `notification_recipients`
(la que más crece) y `push_subscriptions`. El cargo y la fecha de nacimiento vienen del
DWH de SIESA por un CSV que se carga a mano.

Sin código que las use: `events`, `event_attendances`, `certificate_*`, `vacation_*`.

## 4. Inventario de la UI de la intranet

Unas 27 000 líneas, 14 módulos.

| Función | Fuente de datos | Estado |
| --- | --- | --- |
| Home (10 bloques) | Strapi + API (cumpleaños, nuevos ingresos) | Real |
| Noticias y comunicados | Strapi | Real; la confirmación de lectura no persiste |
| Eventos | Strapi | Real; el RSVP no persiste |
| Documentos | Strapi (descarga servida por Caddy) | Real |
| Directorio, cumpleaños, perfil, onboarding | API | Real |
| Notificaciones y Web Push | API | Real |
| Admin de directorio y sedes | API | Real |
| Organigrama, manual de marca | Datos estáticos | Real |
| Admin de noticias, eventos, documentos y solicitudes | — | Vacías: el editor real es el panel de Strapi |
| Certificados, vacaciones, solicitudes | Datos de prueba | Sin uso: se delegaron al portal de autogestión |
| Búsqueda global | — | No funciona |

## 5. Cruce con la plataforma

| Pieza de la intranet | En internal-platform | Veredicto |
| --- | --- | --- |
| Usuarios y rol admin | `identity` (`users.entra_object_id`, administradores de la plataforma) | Existe, pero solo crea usuarios al iniciar sesión |
| Permisos | `areas` (áreas, roles, miembros) | Existe; la intranet solo tiene un rol global |
| Directorio de empleados | No existe | **Módulo nuevo** `directory` |
| Sincronización con Graph | Cliente Graph en modo aplicación en `mail` | Falta el cliente de directorio y el permiso `User.Read.All` |
| Tareas programadas | Workers con `asyncio` e intervalo (`dwh_monitoring`, `pqrsf`) | Reutilizable; falta la hora fija y el candado |
| Sedes | `sites` es el catálogo de sedes de PQRSF | **Choque de nombres**: usar otro nombre o unificar |
| Fotos | S3 y `users.photo_key` | Reutilizable; falta la miniatura de 96 px |
| Notificaciones in-app | Solo las de PQRSF | Módulo nuevo |
| Web Push | No existe | Módulo nuevo, opcional |
| Documentos | `documents` (gestor por área en S3) | Distinto de los documentos de Strapi; no se funden sin decidirlo |

La llave para unir el directorio con los usuarios ya existe:
`users.entra_object_id` es el mismo `oid` de Entra que `Employee.entraId`.

## 6. Estimación del camino C

### API (25–35 días-persona sin Web Push, 28–39 con Web Push)

| Pieza | Días |
| --- | --- |
| Autenticación: pasar la UI al login de la plataforma | 4–8 |
| Módulo directorio (listado, búsqueda, cumpleaños, equipo, foto, CRUD) | 8–12 |
| Sincronización con Graph, cron y historial | 4–5 |
| Importación del DWH (o leerlo directo si el DWH expone empleados) | 1–2 |
| Sedes | 1–2 |
| Onboarding, contacto y horarios | 3–5 |
| Notificaciones in-app e ingesta desde Strapi | 6–9 |
| Web Push (opcional) | 3–4 |
| Migración de datos | 3–4 |
| Pruebas, contrato con la UI y despliegue | 4–6 |

### UI (60–90 días-persona)

| Bloque | Días |
| --- | --- |
| Cimientos: hosting y sesión, sección Intranet en el shell, kit de UI y tokens, auth, Strapi en el servidor Next, despliegue | ~26 |
| Noticias, eventos y documentos (solo Strapi) | 10–12 |
| Directorio, perfil, onboarding y notificaciones (dependen de la API) | 18–22 |
| Home | 7–9 |
| Admin de directorio y sedes, organigrama, enlaces | 6–8 |

Encaje propuesto: una sección **Intranet** fuera de `[area]` (`/intranet/...`), visible
para toda persona autenticada. `/` muestra la home de la intranet. El admin de directorio
y sedes pasa a `/administracion`. Las URL viejas necesitan redirecciones 301 y conviene
mantener `intranet.jdh.local` como alias al menos un trimestre.

## 7. Recomendación

1. **Ya (1–2 semanas):** caminos A y B. Se apaga la máquina de la intranet y queda un solo
   dominio, sin reescribir código. Requiere saber dónde corre la plataforma (bloqueo B3).
2. **Siguiente:** el módulo `directory` en internal-platform-api (unos 12–17 días con la
   sincronización), porque los permisos de la plataforma lo necesitan de todos modos. La
   UI de la intranet puede pasar a leerlo desde la plataforma y retirar ese módulo de
   NestJS.
3. **Después, solo si sigue teniendo sentido:** portar perfil, onboarding y
   notificaciones, y decidir sobre la UI y Strapi. Cada paso se evalúa por separado.

No migrar en ningún caso:

- Las tablas y pantallas de eventos, certificados, vacaciones y solicitudes.
- Better Auth y sus tablas, si se adopta el login de la plataforma.
- Los niveles de límite de tráfico de NestJS: se usa el middleware de la plataforma.
- El catálogo de componentes de la intranet, las páginas de admin vacías y la búsqueda
  global.
- La caché de Redis del directorio: con unos cientos de filas no hace falta.
- Web Push, si está apagado en producción.

## 8. Bloqueos y decisiones pendientes

| Id | Tema | Quién decide |
| --- | --- | --- |
| B1 | Qué pasa con Strapi: se muda, se queda o se reemplaza. Decide si la máquina se apaga con el camino C | Negocio y TI |
| B2 | ~~Consentimiento de Entra para `User.Read.All`~~ Resuelto: el directorio usa el registro de la intranet, que ya lo tiene | Administrador de Entra |
| B3 | Dónde corre internal-platform en producción, con qué capacidad, dominio, TLS y respaldos. La API tiene Dockerfile e integración continua sin despliegue; la UI no tiene ninguno de los dos | TI |
| B4 | ~~Nombre del módulo de sedes físicas frente a `sites` de PQRSF~~ Resuelto: `directory_sites` | Desarrollo |
| — | Si la home de la intranet puede perder su diseño editorial al entrar en el shell | Producto |
| — | Si Web Push está activo en producción | TI |

No se pudo verificar: tamaño y costo de la máquina, número real de empleados, tamaño de
los medios de Strapi y si el DWH expone empleados.

La documentación de la plataforma dice que la intranet es el shell y que usa OIDC desde
el BFF de Next. En el código, la intranet usa Better Auth y la plataforma tiene su propio
shell. Conviene corregirla según lo que se decida.
