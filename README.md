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
- `/api/docs`, `/api/redoc` y `/api/openapi.json`: disponibles en `dev` y `test`.
- En `ENVIRONMENT=prod` se deshabilitan OpenAPI, Swagger UI y ReDoc; también
  se fuerza `debug=False` aunque una variable externa intente activarlo.

## Variables de ambiente

`.env.example` contiene valores locales seguros para el stack de desarrollo.
Los secretos reales deben inyectarse mediante el entorno del proceso o el
gestor de secretos del despliegue. `ENVIRONMENT` acepta `dev`, `test` y `prod`.

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

## Diseño

`app/core` contiene configuración, logging, middleware y el composition root.
`app/infrastructure` contiene adaptadores compartidos para PostgreSQL, Redis y
S3. `app/modules` contiene módulos verticales y `app/api` conecta routers por
versión. Esta separación permite reemplazar un adaptador local por un cliente
remoto si un módulo se convierte en microservicio.
