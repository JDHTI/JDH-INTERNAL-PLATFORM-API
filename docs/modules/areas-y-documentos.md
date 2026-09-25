# Áreas y gestor documental

Tres módulos que trabajan juntos:

| Módulo | Responsabilidad |
|---|---|
| `identity` | Usuarios, roles de plataforma (`admin` / `user`) y sesión SSO con Entra ID. |
| `areas` | Áreas, su catálogo de roles y sus miembros. Decide quién entra a cada área. |
| `documents` | Gestor documental por área: área / carpeta / archivo, sobre S3. |

`documents` no lee las tablas de `areas`: pregunta por acceso a través del
puerto `AreaAccess`, que implementa `AreaService`. `areas` resuelve personas con
el puerto `UserDirectory` (`identity/application/directory.py`) y nunca lee la
tabla `users`. Ambos módulos reciben al usuario como `Principal`
(`identity/domain/principal.py`), resuelto por `get_current_principal`.

## Autenticación

- La sesión es la del módulo `identity`: SSO de Entra con los tokens en cookies
  HttpOnly (`/api/v1/auth/login`, `/auth/callback`, `/auth/refresh`,
  `/auth/logout`). El navegador envía las cookies (`credentials: "include"`);
  no hay token en el cuerpo de las respuestas.
- El usuario se registra en `users` en su primer inicio de sesión, con rol
  `user`. Como cada usuario necesita su identidad de Entra (tenant y object id),
  **solo se puede agregar a un área, o nombrar responsable, a quien ya inició
  sesión una vez**; si no, la API responde 404 con ese motivo.
- Desarrollo: con `AUTH_DEV_BYPASS=true` (solo `ENVIRONMENT=dev`), un request sin
  cookies actúa como el administrador de desarrollo (`AUTH_DEV_USER_*`).

## Acceso

- **Administradores de plataforma** (rol `admin` de `identity`): crean y editan
  áreas, roles y miembros, y ven todas las áreas.
- **Miembros y responsable de un área**: ven el área, su equipo y su catálogo
  de roles, y gestionan carpetas y documentos de esa área.
- Cualquier otra persona recibe 403 en esa área, y la búsqueda sin área solo
  recorre las áreas propias.

## Modelo

- `areas`: la tabla de `0002_identity` (`id`, `tenant_id`, `name`; a ella
  apunta `users.area_id`), que `0003_areas` extiende con `slug` (segmento de URL
  del UI, único e inmutable), `remit`, `icon` (nombre lucide-react), `sort_order`
  y `responsible_user_id`. El modelo es `identity.AreaModel`, que ambos módulos
  mapean. La migración asigna slug a las áreas existentes, fusiona por nombre
  las que coinciden con las 8 áreas que el UI tenía mockeadas y crea las que
  faltan en el tenant configurado.
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
| GET | `/users/me` (módulo `identity`) | autenticado |
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
| GET / PUT | `/areas/{slug}/tag-colors` (PUT: `{"tag", "color"}`) | acceso |
| DELETE | `/areas/{slug}/tag-colors?tag=` | acceso |

Errores: `401` sin sesión, `403` sin acceso, `404` no existe, `409` nombre
duplicado o borrado bloqueado, `413` archivo grande, `415` extensión no
permitida, `422` datos inválidos. Todos responden `{"detail": "..."}`.

## Colores de etiqueta

Las etiquetas siguen siendo texto en cada documento; `document_tag_colors`
guarda un color `#rrggbb` por (área, etiqueta). Colorear «contratos» una vez
lo colorea en todos los documentos del área. La etiqueta se normaliza igual
que en los documentos (minúsculas, espacios colapsados), y viaja en el cuerpo
o en la query porque puede contener `/`. `PUT` crea o reemplaza (gana la
última escritura); `DELETE` sobre una etiqueta sin color responde 204.

## Descargas

`download-url` devuelve una URL firmada (`DOCUMENTS_DOWNLOAD_URL_TTL_SECONDS`)
que descarga con el nombre visible. La firma incluye el host: con la API en
Compose, `S3_PUBLIC_ENDPOINT_URL` apunta al puerto publicado de MinIO.
