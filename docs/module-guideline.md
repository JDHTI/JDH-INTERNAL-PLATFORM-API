# Guía para crear módulos

Cada módulo representa una capacidad de negocio. Mantén sus dependencias hacia
afuera: el dominio no importa FastAPI, SQLAlchemy, Redis ni boto3.

## Estructura recomendada

```text
app/modules/<module_name>/
├── domain/
│   ├── entities.py          # Entidades y reglas puras
│   └── ports.py             # Contratos que necesita el caso de uso
├── application/
│   ├── dto.py               # Comandos y resultados
│   └── service.py           # Casos de uso
├── infrastructure/
│   ├── models.py            # Modelos SQLAlchemy del módulo
│   └── repository.py        # Implementación de los puertos
└── presentation/
    └── router.py            # DTO HTTP y endpoints FastAPI
```

La composición transversal sigue esta estructura fija:

```text
app/
├── app_container.py          # Recursos globales y wiring de la aplicación
└── v1_0/
    ├── v1_containers.py      # Servicios y repositorios de la API v1
    └── v1_router.py          # Registro explícito de routers v1
```

Un módulo pequeño puede comenzar con menos archivos, pero conserva estas
fronteras. El servicio de aplicación recibe puertos, no sesiones ni clientes
concretos. Los repositorios delimitan la transacción y no deben ser usados
directamente desde un router. Los servicios y repositorios deben seguir el
[estándar de docstrings](documentation-guideline.md), con `Args` y `Returns` en
cada operación pública.

## Contenedor y wiring

Declara los proveedores propios del módulo en un contenedor dedicado:

```python
from dependency_injector import containers, providers


class OrdersContainer(containers.DeclarativeContainer):
    database = providers.Dependency()
    repository = providers.Factory(SqlAlchemyOrderRepository, database=database)
    service = providers.Factory(OrderService, repository=repository)
```

Agrega los proveedores globales en `app/app_container.py` y los proveedores del
módulo en `app/v1_0/v1_containers.py`. El router se conecta explícitamente en
`app/v1_0/v1_router.py`:

```python
from app.modules.orders.presentation.router import router as orders_router

v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
```

En el router usa `Provide` y `@inject` para que las pruebas puedan sobrescribir
el servicio. El provider debe venir del contenedor de la versión, nunca de una
instanciación manual dentro del endpoint:

```python
@router.get("/{order_id}")
@inject
async def get_order(
    order_id: UUID,
    service: OrderService = Depends(Provide[ApplicationContainer.v1_container.order_service]),
):
    return await service.get(order_id)
```

`build_application_container()` debe hacer `config.from_dict(...)` y
`container.wire(...)` una sola vez por instancia de aplicación. El lifespan usa
`initialize_global_resources()` y `shutdown_global_resources()` para controlar
el ciclo de vida. No crees contenedores ni clientes globales dentro de routers.

`app/core/container.py` únicamente conserva un import de compatibilidad; el
nuevo código debe importar desde `app.app_container`.

## Modelos y Alembic

Importa los modelos del módulo desde un registro controlado antes de generar
migraciones. Una revisión debe contener únicamente cambios del módulo que la
originó y debe poder revertirse con `alembic downgrade -1`. No ejecutes
`Base.metadata.create_all()` en el arranque.

## Preparación para microservicios

Los casos de uso consumen puertos propios. Para extraer un módulo, conserva el
contrato de aplicación y reemplaza el adaptador local por un cliente HTTP o de
mensajería; el router y las reglas de dominio no deben conocer el transporte.
No compartas tablas entre módulos como API interna.
