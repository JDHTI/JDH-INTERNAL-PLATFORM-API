# Áreas y gestor documental

Tres módulos que trabajan juntos:

| Módulo | Responsabilidad |
|---|---|
| `identity` | Usuarios de la plataforma y autenticación de cada request (Entra ID). |
| `areas` | Áreas, su catálogo de roles y sus miembros. Decide quién entra a cada área. |
| `documents` | Gestor documental por área: área / carpeta / archivo, sobre S3. |

`documents` no lee las tablas de `areas`: pregunta por acceso a través del
puerto `AreaAccess`, que implementa `AreaService`. `areas` resuelve personas con
el puerto `UserDirectory`, que implementa `IdentityService`.

## Autenticación

- Producción: `Authorization: Bearer <access token de Entra>` emitido para esta
  API (audiencia `AUTH_MICROSOFT_ENTRA_ID_ID` o `api://<id>`). Se valida la
  firma con el JWKS del tenant y se aceptan los emisores v1 y v2.
- El usuario se crea en `users` en su primer request. Si un administrador lo
  agregó antes a un área por correo, ese registro se vincula (por correo) en
  su primer inicio de sesión.
- Desarrollo: con `AUTH_DEV_BYPASS=true` (solo `ENVIRONMENT=dev`; en otro
  entorno la API no arranca), un request sin token actúa como el correo del
  header `X-Dev-User-Email`, o como `AUTH_DEV_USER_EMAIL` si no lo trae.

## Acceso

- **Administradores de plataforma** (`PLATFORM_ADMIN_EMAILS`): crean y editan
  áreas, roles y miembros, y ven todas las áreas.
- **Miembros y responsable de un área**: ven el área, su equipo y su catálogo
  de roles, y gestionan carpetas y documentos de esa área.
- Cualquier otra persona recibe 403 en esa área, y la búsqueda sin área solo
  recorre las áreas propias.

## Modelo

- `areas`: `id` (uuid), `slug` (segmento de URL del UI, inmutable), `name`,
  `remit`, `icon` (nombre lucide-react), `sort_order`, `responsible_user_id`,
  `created_at` y `updated_at`. La migración `0003_areas` siembra las 8 áreas que
  el UI tenía mockeadas.
- `area_roles`: catálogo por área ("Líder BI", "Analista BI").
- `area_members`: usuario más un rol **de la misma área**, garantizado por una
  FK compuesta.
- `document_folders`: sin `parent_id`, así que hay un solo nivel. Un área con
  carpetas no se borra (FK `RESTRICT`).
- `documents`: índice de búsqueda (nombre, etiquetas con índice GIN, extensión,
  tamaño, sha256, autor). El binario vive en
  `areas/{area_id}/{folder_id}/{document_id}.{ext}`, así que renombrar no
  toca el bucket.

## Endpoints (`/api/v1`)

| Método | Ruta | Quién |
|---|---|---|
| GET | `/me` | autenticado |
| GET | `/areas` | autenticado (las suyas; admin: todas) |
| POST | `/areas` | admin |
| GET / PATCH / DELETE | `/areas/{slug}` | acceso / admin / admin |
| GET / POST | `/areas/{slug}/roles` | acceso / admin |
| PATCH / DELETE | `/areas/{slug}/roles/{role_id}` | admin |
| GET / POST | `/areas/{slug}/members` (alta por `email` + `role_id`) | acceso / admin |
| PATCH / DELETE | `/areas/{slug}/members/{member_id}` | admin |
| GET / POST | `/areas/{slug}/folders` | acceso |
| PATCH / DELETE | `/areas/{slug}/folders/{folder_id}` | acceso |
| POST | `/areas/{slug}/folders/{folder_id}/documents` (multipart: `file`, `name?`, `description?`, `tags?`) | acceso |
| GET | `/documents/search?area=&folder_id=&q=&tags=a,b&extension=&page=&size=` | autenticado |
| GET / PATCH / DELETE | `/documents/{id}` | acceso al área del documento |
| GET | `/documents/{id}/download-url` | acceso al área del documento |

Errores: `401` sin sesión, `403` sin acceso, `404` no existe, `409` nombre
duplicado o borrado bloqueado, `413` archivo grande, `415` extensión no
permitida, `422` datos inválidos. Todos responden `{"detail": "..."}`.

## Descargas

`download-url` devuelve una URL firmada (`DOCUMENTS_DOWNLOAD_URL_TTL_SECONDS`)
que descarga con el nombre visible. La firma incluye el host: con la API en
Compose, `S3_PUBLIC_ENDPOINT_URL` apunta al puerto publicado de MinIO.
