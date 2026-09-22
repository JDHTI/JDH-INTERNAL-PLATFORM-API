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

Un módulo pequeño puede comenzar con menos archivos, pero conserva estas
fronteras. El servicio de aplicación recibe puertos, no sesiones ni clientes
concretos. Los repositorios delimitan la transacción y no deben ser usados
directamente desde un router.

## Contenedor y wiring

Declara los proveedores propios del módulo en un contenedor dedicado:

```python
from dependency_injector import containers, providers


class OrdersContainer(containers.DeclarativeContainer):
    database = providers.Dependency()
    repository = providers.Factory(SqlAlchemyOrderRepository, database=database)
    service = providers.Factory(OrderService, repository=repository)
```

Agrega el contenedor como proveedor en `app/core/container.py` y conecta el
router explícitamente en `app/api/v1/router.py`:

```python
from app.modules.orders.presentation.router import router as orders_router

v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
```

En el router usa `Provide` y `@inject` para que las pruebas puedan sobrescribir
el repositorio:

```python
@router.get("/{order_id}")
@inject
async def get_order(
    order_id: UUID,
    service: Annotated[OrderService, Depends(Provide[ApplicationContainer.order_service])],
):
    return await service.get(order_id)
```

El composition root debe hacer `container.config.from_dict(...)` y
`container.wire(...)` una sola vez por instancia de aplicación. No crees
contenedores ni clientes globales dentro de routers.

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
