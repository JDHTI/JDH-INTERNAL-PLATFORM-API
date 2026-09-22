# Wiring y composición de dependencias

Este documento define cómo se inicializan recursos, servicios y routers. La
regla central es que la composición ocurre en un único punto de entrada y que
los módulos reciben dependencias mediante contratos y providers, nunca mediante
instanciación manual dentro de un endpoint.

## Responsabilidad de cada nivel

```text
app/
├── app_container.py       # composition root, configuración y ciclo de vida
├── api/router.py           # montaje del API global y de la versión
├── v1_0/
│   ├── v1_containers.py   # providers propios de la API v1
│   └── v1_router.py        # registro explícito de routers v1
└── modules/<name>/         # vertical slice de cada capacidad de negocio
```

- `app/app_container.py` es el único composition root. Construye
  `ApplicationContainer`, carga la configuración y hace el wiring de los
  módulos. También expone `initialize_global_resources()` y
  `shutdown_global_resources()` para el lifespan de FastAPI.
- Los providers globales (`Database`, `RedisClient` y `S3Storage`) viven en
  `ApplicationContainer` como `Singleton`. Se crean al arrancar la aplicación
  y se cierran en orden durante el shutdown.
- `app/v1_0/v1_containers.py` contiene exclusivamente dependencias y servicios
  de la versión 1. Sus providers reciben los recursos globales como
  `providers.Dependency`.
- `app/v1_0/v1_router.py` es un registro explícito. Cada router nuevo debe
  importarse y montarse allí; no se permite descubrimiento dinámico por
  reflexión.
- `app/api/router.py` monta el health check global y `v1_router` bajo el
  prefijo de API configurado.
- `app/core/container.py` solo conserva un re-export de compatibilidad. El
  código nuevo debe importar desde `app.app_container`.

## Secuencia de arranque

La fábrica `create_app()` sigue siempre esta secuencia:

1. Crear `Settings` y configurar el logger.
2. Ejecutar `build_application_container(settings)`.
3. Cargar `settings.container_config()` con `config.from_dict(...)`.
4. Hacer `container.wire(...)` para los módulos que usan `Provide`.
5. Instanciar los recursos globales con
   `initialize_global_resources(container)`.
6. Iniciar el lifespan y servir los routers ya registrados.
7. Liberar Redis, PostgreSQL y S3 con
   `shutdown_global_resources(resources)` al cerrar el proceso.

El wiring ocurre una sola vez por instancia de aplicación. Los health checks
pueden probar conectividad en sus endpoints, pero el constructor de un cliente
no debe hacer llamadas de red bloqueantes.

## Cómo agregar un módulo

Conserva las fronteras de Clean Architecture:

```text
app/modules/orders/
├── domain/          # entidades y puertos sin framework
├── application/     # casos de uso y DTOs
├── infrastructure/  # adaptadores, repositorios y modelos
└── presentation/    # router y esquemas HTTP
```

El servicio de aplicación depende de puertos. El router depende del servicio
por `Provide`; nunca importa una sesión SQLAlchemy, un cliente Redis o boto3.
Un ejemplo de provider en `V1Container`:

```python
class V1Container(containers.DeclarativeContainer):
    database = providers.Dependency(instance_of=Database)
    order_repository = providers.Factory(
        SqlAlchemyOrderRepository,
        database=database,
    )
    order_service = providers.Factory(
        OrderService,
        repository=order_repository,
    )
```

En el router se referencia el provider, no el objeto concreto:

```python
@router.post("/", response_model=OrderResponse)
@inject
async def create_order(
    command: CreateOrderRequest,
    service: OrderService = Depends(Provide[ApplicationContainer.v1_container.order_service]),
) -> OrderResponse:
    return await service.create(command)
```

Finalmente, registra el router de forma explícita:

```python
# app/v1_0/v1_router.py
from app.modules.orders.presentation.router import router as orders_router

v1_router.include_router(orders_router, prefix="/orders", tags=["orders"])
```

Si el módulo requiere wiring adicional, inclúyelo en la lista de módulos de
`build_application_container()`. Mantén ese registro centralizado para que sea
auditable y evita llamar `wire()` desde routers, servicios o pruebas de forma
implícita.

## Pruebas y overrides

Las pruebas deben crear su propio `ApplicationContainer` o usar la fábrica de
aplicación con settings de `test`. Sobrescribe providers en el contenedor de la
versión para aislar adaptadores externos y restaura el override al finalizar:

```python
with container.v1_container.order_service.override(fake_service):
    response = await client.post("/api/v1/orders", json=payload)
```

No compartas el contenedor global entre casos de prueba. Libera los recursos
creados por cada fixture con `shutdown_global_resources()`.

## Checklist de transferencia de un módulo

- [ ] Crear el vertical slice en `app/modules/<name>`.
- [ ] Declarar puertos en el dominio y adaptadores en infraestructura.
- [ ] Registrar providers en `app/v1_0/v1_containers.py`.
- [ ] Agregar el router a `app/v1_0/v1_router.py`.
- [ ] Incluir el módulo en el registro de wiring si usa `Provide`.
- [ ] Registrar modelos en el metadata controlado y crear la migración Alembic.
- [ ] Agregar pruebas de dominio, aplicación, router e integración necesaria.
- [ ] Actualizar [`docs/module-guideline.md`](module-guideline.md) si aparece un
  patrón nuevo.

## Preparación para microservicios

Los casos de uso no deben depender del transporte. Para extraer un módulo, se
conservan sus puertos y se reemplaza el adaptador local por un cliente HTTP,
mensajería u otro adaptador remoto. El router de la API y las reglas de dominio
no necesitan conocer ese cambio. No compartas tablas ni instancias del
contenedor entre módulos que puedan convertirse en servicios independientes.
