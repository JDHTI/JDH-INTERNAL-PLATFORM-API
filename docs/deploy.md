# Despliegue en intranet.jdh.com.co

La plataforma reemplazó a la intranet en el mismo servidor. La UI está en JDH-INTERNAL-PLATFORM
(`docs/deploy.md` de ese repo); este documento cubre el API.

## Cómo encaja

Desde el 2026-11-10 el dominio es `*.jdh.com.co`; `*.jdh.local` y su CA interna ya no se usan.
`intranet.jdh.com.co` es un CNAME de `host.jdh.com.co`, un proxy de infraestructura con el
certificado `*.jdh.com.co` que termina el TLS y reenvía por **http** al Caddy del servidor:

```
https://intranet.jdh.com.co → proxy de infra (TLS) → http → intranet-caddy → /api/v1/*      → platform-api:8000  (este repo)
                                                                             /cms/uploads/* → intranet-cms:1337
                                                                             /*             → platform-ui:3000
```

| Proyecto de compose | Servicios | Red |
| --- | --- | --- |
| `intranet-jdh` (borde, INTRANET-JDH-API) | `caddy`, `cms`, `postgres` (`intranet_jdh`, `intranet_cms`) | `intranet-jdh_default` |
| `internal-platform` (UI) | `ui` (`platform-ui`) | `intranet-jdh_default` |
| `internal-platform-api` (este repo, `deploy/compose.prod.yaml`) | `api` (`platform-api`), `postgres`, `redis`, `minio`, `clamav` | propia; solo `api` se une a `intranet-jdh_default` |

- **Puertos.** Las dependencias del API no publican puertos.
- **Volúmenes.** Los datos de la plataforma viven en los volúmenes `internal-platform-api_*`.
- **Red.** Caddy resuelve `platform-api` en cada petición, así que levantar el API no obliga a
  reiniciar el borde.

## Configuración

Todo lo que no es código vive en `~/apps/platform/api.env` (`chmod 600`, no versionado).
La plantilla es `deploy/api.env.example`. Lo mínimo es:

- **Dependencias.** Contraseñas de Postgres, Redis y MinIO.
- **SSO.** El registro de la intranet (`404211b8-…`) en `ENTRA_*`, el mismo del directorio. Ya
  debe tener registrado como **Web** el redirect URI
  `https://intranet.jdh.com.co/api/v1/auth/callback` (`ENTRA_REDIRECT_URI`).
  - No expone un API propio. Por eso `ENTRA_API_SCOPE` va vacío: la sesión es el ID token, cuya
    audiencia es el client id, y el inicio de sesión pide `User.Read` para leer `/me`, como hacía
    Better Auth.
  - Si algún día el registro expone `access_as_user`, se ponen `ENTRA_API_AUDIENCE` y
    `ENTRA_API_SCOPE` y la sesión pasa a ser el token del API.
- **Origen y cookies.** `AUTH_COOKIE_SECURE=true` y `CORS_ORIGINS=https://intranet.jdh.com.co`;
  `AUTH_FRONTEND_REDIRECT_URI` y `PQRSF_UI_BASE_URL` sobre el mismo origen.
- **Directorio.** El registro de la intranet en `DIRECTORY_GRAPH_*`, que tiene `User.Read.All`.
- **DWH.** `DWH_*` para maestros, cotizador y validador.

`RATE_LIMIT_TRUSTED_PROXY_IPS` vacío hace que `deploy.sh` use la dirección de `intranet-caddy` en
la red del borde.

## Desplegar

`deploy/deploy.sh` corre en este orden:

1. Toma el lock `~/apps/.deploy.lock`, el mismo que usan la UI y el borde.
2. Respalda la base y el almacenamiento (`deploy/backup.sh pre-deploy`), salvo en el primer
   despliegue, cuando aún no hay base.
3. Construye la imagen.
4. Levanta las dependencias.
5. Corre `alembic upgrade head`.
6. Levanta `platform-api`.
7. Verifica a través de Caddy que `GET /api/v1/users/me` responda 401. Un 502 significa que el API
   no está. Va por el mismo salto que usa el proxy de infraestructura: http al Caddy local
   (`EDGE_HTTP`, por defecto `http://127.0.0.1`) con `Host: $PUBLIC_HOST` (por defecto
   `intranet.jdh.com.co`).

```bash
bash deploy/deploy.sh            # desde una copia del repo en el servidor
```

### CD

El job `deploy` de `.github/workflows/ci.yml` corre en cada push a `master`, después de
`quality`, en el runner self-hosted con la etiqueta `internal-platform-api`. Registrar el runner
se hace una vez: un administrador del repo genera el token en Settings, Actions, Runners, New
self-hosted runner, y el token dura una hora.

```bash
deploy/setup-runner.sh <token>   # en el servidor, pide sudo
```

## Respaldos

El respaldo diario de la intranet (`INTRANET-JDH-API/deploy/scripts/backup.sh`) no cubre esta
base. `deploy/backup.sh [etiqueta]` guarda en `~/backups/platform/<fecha>-<etiqueta>/`:

- `internal_platform.dump`: la base (`pg_dump -Fc`).
- `storage.tar.gz`: el volumen de RustFS (adjuntos de PQRSF, documentos, fotos).

Conserva 14 diarios y 10 pre-deploy. Va en su propia carpeta: la limpieza del respaldo de la
intranet borra toda carpeta `~/backups/*-daily` y contaría estas como suyas. Comparte el lock de
despliegue, así que nunca corre en medio de un reinicio.

Cron (una vez, `crontab -e` del usuario del servidor):

```
30 2 * * * ~/apps/JDH-INTERNAL-PLATFORM-API/deploy/backup.sh daily >> ~/backups/platform/backup.log 2>&1
```

Restaurar la base:

```bash
cd ~/apps/JDH-INTERNAL-PLATFORM-API
export PLATFORM_API_ENV=~/apps/platform/api.env
set -a; . "$PLATFORM_API_ENV"; set +a   # compose interpola las contraseñas, como deploy.sh
C="docker compose -f deploy/compose.prod.yaml"
$C stop api
$C exec -T postgres pg_restore -U platform -d internal_platform --clean --if-exists \
  < ~/backups/platform/<carpeta>/internal_platform.dump
$C start api
```

Restaurar el almacenamiento (con `minio` detenido):

```bash
$C stop minio
docker run --rm -v internal-platform-api_minio_data:/dst -v ~/backups/platform/<carpeta>:/in:ro \
  alpine:3 sh -c 'rm -rf /dst/* /dst/.[!.]* && tar -xzf /in/storage.tar.gz -C /dst'
$C start minio
```

Como el de la intranet, protege contra borrados y errores, no contra la pérdida del servidor:
`~/backups` debe copiarse fuera de la VM.

## Primera carga

Después del primer despliegue:

```bash
C="docker compose -f deploy/compose.prod.yaml"
$C run --rm api poetry run python scripts/sync-directory.py
# La base intranet_jdh sigue en intranet-postgres, en la red del borde.
docker run --rm --network intranet-jdh_default \
  --env-file ~/apps/platform/api.env \
  -e DATABASE_URL=... internal-platform-api:latest \
  poetry run python scripts/import-intranet-directory.py "postgresql://<usuario>:<clave>@intranet-postgres:5432/intranet_jdh"
$C run --rm api poetry run python scripts/set-owner.py --email analista.ti@jdh.com.co
```

La importación simula si no se le pasa `--apply`. La migración `0041` ya deja como owner a
`PLATFORM_OWNER_EMAIL` si existe. Si la primera carga ocurre después, `set-owner.py` lo cubre.

## Pendiente

- **Descargas firmadas.** Las descargas de documentos usan URLs firmadas de MinIO, que el navegador
  no alcanza. Falta una ruta de Caddy hacia `minio:9000` con su propio host, y entonces
  `S3_PUBLIC_ENDPOINT_URL`. Las fotos del directorio y los adjuntos de PQRSF salen por el API y no
  dependen de esto.
- **Contenido de Strapi.** La fase 4 trae `/content/*`. Cuando esté, el CMS vuelve a apuntar
  `REVALIDATE_WEBHOOK_URL` y `NOTIFICATIONS_API_URL` al API.
