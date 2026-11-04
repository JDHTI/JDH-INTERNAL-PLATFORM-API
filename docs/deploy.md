# Despliegue en intranet.jdh.local

La plataforma reemplazó a la intranet en el mismo servidor y el mismo dominio. La UI está en
JDH-INTERNAL-PLATFORM (`docs/deploy.md` de ese repo); este documento cubre el API.

## Cómo encaja

```
https://intranet.jdh.local  → intranet-caddy → /api/v1/*      → platform-api:8000  (este repo)
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
- **SSO.** El registro de la plataforma en `ENTRA_*`. El redirect URI
  `https://intranet.jdh.local/api/v1/auth/callback` debe estar registrado como **Web** en ese
  registro de Entra, y el API debe exponer el scope `access_as_user`.
- **Origen y cookies.** `AUTH_COOKIE_SECURE=true` y `CORS_ORIGINS=https://intranet.jdh.local`.
- **Directorio.** El registro de la intranet en `DIRECTORY_GRAPH_*`, que tiene `User.Read.All`.
- **DWH.** `DWH_*` para maestros, cotizador y validador.

`RATE_LIMIT_TRUSTED_PROXY_IPS` vacío hace que `deploy.sh` use la dirección de `intranet-caddy` en
la red del borde.

## Desplegar

`deploy/deploy.sh` corre en este orden:

1. Toma el lock `~/apps/.deploy.lock`, el mismo que usan la UI y el borde.
2. Construye la imagen.
3. Levanta las dependencias.
4. Corre `alembic upgrade head`.
5. Levanta `platform-api`.
6. Verifica a través de Caddy que `GET /api/v1/users/me` responda 401. Un 502 significa que el API
   no está.

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
