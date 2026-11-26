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
  `/api/v1/areas`; requieren un rol global `owner` o `admin`.
- Cada endpoint del espacio de trabajo exige un permiso de módulo: roles globales
  `owner`, `admin`, `moderator` y `user`, y roles de área con permisos `read` o `manage`
  por módulo. El panel vive en `/api/v1/permissions`. El owner es `PLATFORM_OWNER_EMAIL`
  y se mueve con `scripts/set-owner.py`. Ver [`docs/permissions.md`](docs/permissions.md).
- Todo el módulo PQRSF (contrato de la API, interfaz, estado y hoja de ruta) está
  en un solo documento: [`docs/pqrsf.md`](docs/pqrsf.md). Su formulario público
  vive en `/pqrsf`, fuera del layout autenticado.
- Los planes de acción del SIG (P.04.F.02: acciones correctivas, preventivas y de
  mejora, alimentadas desde los casos PQRSF) están en
  [`docs/action-plans.md`](docs/action-plans.md).
- El monitoreo del datawarehouse (TI → Monitoreo → Datawarehouse) vive en
  `/api/v1/dwh-monitoring`; su carga y su contrato están en
  [`docs/dwh-monitoring.md`](docs/dwh-monitoring.md).
- El monitoreo de aplicativos (TI → Monitoreo → Aplicativos) vive en
  `/api/v1/app-monitoring`, y el tablero general de TI en `/api/v1/monitoring/board`;
  ver [`docs/app-monitoring.md`](docs/app-monitoring.md).
- El cotizador del área comercial (Comercial → Cotizador) vive en `/api/v1/quotations`:
  arma la lista de precios de un cliente desde el DWH, la cruza con el maestro de márgenes y
  de apoyos y genera el Excel y el plano de SIESA; ver [`docs/cotizador.md`](docs/cotizador.md).
  Con el mismo motor, BI actualiza una lista vigente de SIESA (BI → Actualización de listas);
  ver [`docs/actualizacion-listas.md`](docs/actualizacion-listas.md). Las listas principales y el
  impuesto licor de todas las listas van por un módulo bloqueado a BI (BI → Listas principales);
  ver [`docs/listas-principales.md`](docs/listas-principales.md). Desde una cotización de cliente
  se piden apoyos internos (margen apoyado) que BI o un aprobador designado decide; ver
  [`docs/apoyos-internos.md`](docs/apoyos-internos.md).
- El maestro de apoyos de proveedores vive en `/api/v1/supplier-supports`: tres clases, un
  cálculo por unidad de inventario compartido con el cotizador y el validador, sin cruces, carga
  masiva y por grupo empresarial, y publicación al DWH; BI ve costos y Comercial no. Ver
  [`docs/apoyos-proveedores.md`](docs/apoyos-proveedores.md) (reglas v2) y
  [`docs/apoyos-dwh.md`](docs/apoyos-dwh.md).
- La liquidación de apoyos (BI) vive en `/api/v1/support-settlements`: cuánto reconoce cada
  proveedor por las ventas apoyadas del mes (cotización del proveedor, descuento real, copias
  una vez), margen recompuesto, cierre del mes vencido y Excel por proveedor. Ver
  [`docs/liquidacion-apoyos.md`](docs/liquidacion-apoyos.md).
- Gestión comercial (BI y Comercial): el presupuesto comercial vive en
  `/api/v1/commercial-budget` (venta, margen y clientes por vendedor del mes, el promedio de los
  tres meses anteriores y el mismo mes del año anterior, presupuesto por campos y cierre que
  congela la asignación) y la carga del maestro de vendedores al DWH en `/api/v1/vendor-master`.
  Ver [`docs/presupuesto-comercial.md`](docs/presupuesto-comercial.md).
- El ajuste de costos (BI) vive en `/api/v1/cost-settings`: el factor de costo por familia, el
  umbral de negociación y la sucursal del proveedor por C.O. (Team Foods en Antioquia, Lactalis
  en Centro), con su historial. Ver [`docs/ajuste-costos.md`](docs/ajuste-costos.md).
- El mapa comercial (Comercial → Mapa comercial) vive en `/api/v1/territory`: prospectos del
  archivo mensual sobre el mapa (PostGIS), zonas comerciales trazadas y rutas de visita por
  calles con openrouteservice; ver [`docs/mapa-comercial.md`](docs/mapa-comercial.md).
- Costos (Abastecimiento) vive en `/api/v1/costs`: las entradas de compra del DWH contra la
  cotización vigente en su fecha, sus novedades con un motivo y la nota crédito del proveedor,
  y las alzas que envían los proveedores; reemplaza el aplicativo «Control de Precios JDH». Ver
  [`docs/costos.md`](docs/costos.md).
- El directorio de empleados vive en `/api/v1/directory` (lectura para toda persona
  autenticada) y `/api/v1/directory/admin` (administradores): copia nocturna de Entra
  que además crea los usuarios de la plataforma; ver [`docs/directory.md`](docs/directory.md).
- `/api/docs`, `/api/redoc` y `/api/openapi.json`: disponibles en `dev` y `test`.
- En `ENVIRONMENT=prod` se deshabilitan OpenAPI, Swagger UI y ReDoc; también
  se fuerza `debug=False` aunque una variable externa intente activarlo.

## Variables de ambiente

`.env.example` contiene valores locales seguros para el stack de desarrollo.
Los secretos reales deben inyectarse mediante el entorno del proceso o el
gestor de secretos del despliegue. `ENVIRONMENT` acepta `dev`, `test` y `prod`.
Las variables `DIRECTORY_GRAPH_*` (registro de aplicación con `User.Read.All`
para el directorio de empleados) se cargan desde el `.env` local o desde el
gestor de secretos del entorno; el valor de `DIRECTORY_GRAPH_CLIENT_SECRET`
nunca debe versionarse.
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
