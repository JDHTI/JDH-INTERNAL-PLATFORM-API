# Internal Platform API

Base modular para construir servicios HTTP con FastAPI, Clean Architecture y
Dependency Injector. El repositorio inicia con infraestructura y health checks;
los módulos de negocio se agregan siguiendo [la guía de módulos](docs/module-guideline.md).

## Requisitos

- Python 3.12
- Poetry 2.x
- Docker Desktop con Compose v2

## Inicio rápido con Poetry

```powershell
Copy-Item .env.example .env
docker compose up -d
poetry install
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

La API local espera PostgreSQL, Redis y MinIO en sus puertos publicados. Para
levantarlos y ejecutar también la API en contenedores:

```powershell
docker compose --profile app up --build
```

La infraestructura sin la API se puede levantar con `docker compose up -d`.
Después se puede ejecutar FastAPI desde Poetry en el host.
Si alguno de los puertos publicados ya está ocupado, cambia los valores
`*_HOST_PORT` del `.env` antes de levantar Compose.

## Endpoints base

- `GET /api/health/live`: confirma que el proceso responde.
- `GET /api/health/ready`: comprueba PostgreSQL, Redis y el bucket S3.
- `GET /api/v1/auth/login`: inicia el SSO con Microsoft Entra ID mediante el
  flujo de código de autorización y PKCE.
- `GET /api/v1/auth/callback`: callback de Entra; la API registra el usuario en
  su primer acceso y administra los tokens exclusivamente en cookies `HttpOnly`.
- `GET /api/v1/auth/me`: devuelve el perfil local autenticado. El frontend debe
  usar `credentials: include`; no debe guardar tokens en `localStorage`.
- Los endpoints administrativos viven en `/api/v1/users`, `/api/v1/roles` y
  `/api/v1/areas`; requieren el rol local `admin`.
- El formulario público y el contrato de PQRSF están documentados en
  [`docs/pqrsf.md`](docs/pqrsf.md); su página visual debe usar `/pqrsf` fuera
  del layout autenticado.
- El monitoreo del datawarehouse (TI → Monitoreo → Datawarehouse) vive en
  `/api/v1/dwh-monitoring`; su carga y su contrato están en
  [`docs/dwh-monitoring.md`](docs/dwh-monitoring.md).
- `/api/docs`, `/api/redoc` y `/api/openapi.json`: disponibles en `dev` y `test`.
- En `ENVIRONMENT=prod` se deshabilitan OpenAPI, Swagger UI y ReDoc; también
  se fuerza `debug=False` aunque una variable externa intente activarlo.

## Variables de ambiente

`.env.example` contiene valores locales seguros para el stack de desarrollo.
Los secretos reales deben inyectarse mediante el entorno del proceso o el
gestor de secretos del despliegue. `ENVIRONMENT` acepta `dev`, `test` y `prod`.
Las variables `AUTH_MICROSOFT_ENTRA_ID_*` se cargan desde el `.env` local o
desde el gestor de secretos del entorno; el valor de
`AUTH_MICROSOFT_ENTRA_ID_SECRET` nunca debe versionarse.
El datawarehouse se configura con `DWH_*`; el proveedor inicial es PostgreSQL
y el usuario debe tener permisos de lectura sobre el esquema `consumo`. La
conexión se abre bajo demanda, se comprueba en readiness y se cierra con el
proceso. Cambiar `DWH_PROVIDER` queda preparado para incorporar otro adaptador
sin cambiar el contrato del endpoint.

## SSO y bypass de desarrollo

La API usa el flujo OIDC de Microsoft Entra ID. El navegador recibe únicamente
cookies `HttpOnly`; la cookie de acceso se renueva automáticamente usando la
cookie de renovación cuando expira. Configura `ENTRA_TENANT_ID`,
`ENTRA_CLIENT_ID`, `ENTRA_API_AUDIENCE` y la URL de callback registrada en Entra.
Para capturar cargo y foto mediante Microsoft Graph, la aplicación necesita
consentimiento delegado para `User.Read` y una credencial de cliente para el
flujo On-Behalf-Of.

En `ENVIRONMENT=dev` se puede activar `AUTH_DEV_BYPASS=true`. Este modo crea un
usuario administrador local determinista y no acepta ni genera tokens; se
rechaza automáticamente en `test` y `prod`. Para promover un usuario real al
primer administrador, ejecútalo después de su primer ingreso:

```powershell
poetry run python scripts/promote-user.py --tenant-id <tenant> --entra-object-id <oid>
```

## SSO y bypass de desarrollo

La API usa el flujo OIDC de Microsoft Entra ID. El navegador recibe únicamente
cookies `HttpOnly`; la cookie de acceso se renueva automáticamente usando la
cookie de renovación cuando expira. Configura `ENTRA_TENANT_ID`,
`ENTRA_CLIENT_ID`, `ENTRA_API_AUDIENCE` y la URL de callback registrada en Entra.
Para capturar cargo y foto mediante Microsoft Graph, la aplicación necesita
consentimiento delegado para `User.Read` y una credencial de cliente para el
flujo On-Behalf-Of.

En `ENVIRONMENT=dev` se puede activar `AUTH_DEV_BYPASS=true`. Este modo crea un
usuario administrador local determinista y no acepta ni genera tokens; se
rechaza automáticamente en `test` y `prod`. Para promover un usuario real al
primer administrador, ejecútalo después de su primer ingreso:

```powershell
poetry run python scripts/promote-user.py --tenant-id <tenant> --entra-object-id <oid>
```

## Calidad y migraciones

```powershell
poetry run ruff check .
poetry run ruff format --check .
poetry run pytest
poetry run alembic check
```

Alembic usa SQLAlchemy async y una revisión inicial vacía. Cada módulo debe
registrar sus modelos en el metadata antes de ejecutar `alembic revision
--autogenerate` y revisar el diff generado antes de aplicarlo.

## Scripts de desarrollo

Los comandos frecuentes están automatizados en [`scripts/`](scripts/README.md):

```bash
./scripts/run-backend.sh
./scripts/run-test.sh
./scripts/run-test.sh ventas
./scripts/run-test.sh ventas agregar_compra
./scripts/run-test.sh test tests/test_app.py::test_liveness_and_request_id
./scripts/run-tests.sh ventas agregar_compra
```

`run-backend.sh` usa reload en desarrollo y acepta `MIGRATE=true` para ejecutar
Alembic antes de levantar Uvicorn. `run-test.sh` acepta scope completo, módulo
o nodo individual.

## Diseño

Las reglas para el historial y para registrar dependencias están en
[`docs/conventional-commits.md`](docs/conventional-commits.md) y
[`docs/wiring-guideline.md`](docs/wiring-guideline.md). La guía de creación de
módulos se encuentra en [`docs/module-guideline.md`](docs/module-guideline.md),
y el formato de docstrings en
[`docs/documentation-guideline.md`](docs/documentation-guideline.md). El flujo
de ramas y Pull Requests está definido en
[`docs/git-workflow.md`](docs/git-workflow.md).

`app/core` contiene configuración, logging y middleware. El composition root
está en [`app/app_container.py`](app/app_container.py): allí se inicializan los
recursos globales y se conecta [`app/v1_0/v1_containers.py`](app/v1_0/v1_containers.py),
que contiene los servicios de la versión 1. El registro explícito de routers
vive en `app/v1_0/v1_router.py`. `app/infrastructure` contiene adaptadores
compartidos para PostgreSQL, Redis y S3; `app/modules` contiene módulos
verticales. Esta separación permite reemplazar un adaptador local por un
cliente remoto si un módulo se convierte en microservicio.
