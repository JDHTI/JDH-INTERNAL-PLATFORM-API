# Estándar de documentación de código

Este proyecto usa docstrings nativos de Python con formato compatible con
herramientas de autodocumentación. No se requiere una librería adicional para
escribirlos. El estándar es obligatorio para los dos elementos que forman el
contrato de negocio:

1. servicios de aplicación;
2. repositorios (interfaces/puertos y sus implementaciones de persistencia).

Los routers no requieren un docstring por defecto. FastAPI ya publica la
información principal mediante `summary`, `description`, `response_model` y
las respuestas declaradas en la ruta. Documenta un router sólo cuando tenga
reglas, efectos secundarios o errores que no sean evidentes en esa metadata.

## Formato obligatorio

Usa comillas triples dobles y una descripción breve en la primera línea. Los
métodos públicos de servicios y repositorios deben declarar `Args` y
`Returns`; los constructores deben documentar también los argumentos que
reciben dependencias.

```python
async def create(
    self,
    command: CreateOrderCommand,
) -> Order:
    """Create an order and persist the resulting aggregate.

    Args:
        command (CreateOrderCommand): Validated data required to create the
            order.

    Returns:
        Order: The persisted order with its generated identifier.
    """
```

Reglas estrictas:

- El texto inicial debe explicar qué hace la operación, no repetir su nombre.
- `Args` debe incluir cada parámetro de entrada excepto `self` y `cls`.
- El nombre de cada argumento debe coincidir exactamente con la firma.
- `Returns` debe indicar el tipo de respuesta y el resultado observable.
- Usa `Returns: None` sólo cuando la operación no devuelve un valor.
- Mantén el docstring junto a la definición; no uses comentarios externos para
  describir contratos públicos.
- El docstring se escribe en inglés o español de forma consistente con el
  módulo, pero los nombres de las secciones son siempre `Args` y `Returns`.
- Los detalles de excepciones sólo se agregan cuando son parte relevante del
  contrato; no se documentan errores genéricos de implementación.

Los métodos privados pueden documentarse cuando su comportamiento no sea
obvio, pero no sustituyen el docstring del servicio o repositorio público.

En la base actual todavía no hay repositorios de dominio; `Database`,
`RedisClient` y `S3Storage` son adaptadores de infraestructura compartidos.
Cuando un módulo incorpore un repositorio, tanto su puerto como su implementación
deben aplicar esta guía.

## Servicios de aplicación

El docstring de la clase resume la responsabilidad y el límite del servicio.
Cada caso de uso público describe sus entradas y su respuesta:

```python
class OrderService:
    """Coordinate order use cases without exposing persistence details."""

    def __init__(self, repository: OrderRepository) -> None:
        """Create the service with its persistence port.

        Args:
            repository (OrderRepository): Port used to load and persist orders.
        """

    async def get_by_id(self, order_id: UUID) -> Order | None:
        """Find an order by its public identifier.

        Args:
            order_id (UUID): Identifier of the order to retrieve.

        Returns:
            Order | None: The order when it exists, otherwise ``None``.
        """
```

El servicio no debe describir detalles de FastAPI, SQLAlchemy, Redis o boto3.
Su documentación expresa la intención del caso de uso y su contrato de
aplicación.

## Repositorios

Un repositorio documenta el agregado que persiste y el significado de cada
operación. La interfaz/puerto y la implementación deben conservar el mismo
contrato:

```python
class OrderRepository(Protocol):
    """Define persistence operations for order aggregates."""

    async def save(self, order: Order) -> Order:
        """Persist an order and return its stored representation.

        Args:
            order (Order): Aggregate to persist.

        Returns:
            Order: Stored aggregate, including persistence-generated fields.
        """
```

La implementación puede agregar detalles de transacción o infraestructura en
su propio docstring, pero no debe cambiar el significado de `Args` o `Returns`.
Los repositorios no deben documentar consultas SQL línea por línea; documenta
la intención y el resultado del contrato.

## Routers (opcional)

Documenta un router sólo si la metadata de FastAPI no explica una regla
importante. Cuando sea necesario, conserva el mismo formato:

```python
@router.post("/", response_model=OrderResponse, summary="Create an order")
async def create_order(command: CreateOrderRequest) -> OrderResponse:
    """Create an order after validating the authenticated account."""
```

Para endpoints normales, prioriza `summary`, `description`, `response_model`,
`responses` y los esquemas Pydantic. No agregues docstrings ceremoniales a cada
router sólo para aumentar el volumen de documentación.

## Revisión y transferencia

Antes de integrar un servicio o repositorio nuevo:

- [ ] La clase explica su responsabilidad.
- [ ] Cada método público tiene una primera línea accionable.
- [ ] `Args` cubre todas las entradas, excepto `self`/`cls`.
- [ ] `Returns` explica el tipo y el resultado que recibe el consumidor.
- [ ] El contrato del puerto y el adaptador mantiene el mismo significado.
- [ ] El router sólo fue documentado si aporta contexto que FastAPI no puede
  inferir.

Esta guía se aplica junto con la [guía de módulos](module-guideline.md) y la
[guía de wiring](wiring-guideline.md). Cualquier excepción debe explicarse en
el mismo commit que introduce el servicio o repositorio.
