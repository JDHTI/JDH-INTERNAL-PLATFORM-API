# Estándar para vistas de consumo

Este documento define cómo incorporar nuevas vistas de solo lectura del
datawarehouse al aplicativo. El primer caso implementado es
`consumo.clientes`; el mismo patrón se utilizará para `consumo.proveedores`,
`consumo.supervisores`, ventas y las demás vistas que el proyecto necesite.

## Separación de responsabilidades

Hay dos conceptos distintos que no deben mezclarse:

- El **proveedor de datos** es la conexión física configurada por `DWH_*`. Hoy
  es PostgreSQL y se administra mediante `DataProviderConnection`.
- La **vista de consumo** es un recurso de negocio dentro del esquema
  `consumo`. Cada servicio declara qué vista consulta y qué campos expone.

El endpoint nunca recibe el nombre de la tabla o vista desde el cliente. Los
identificadores se mantienen en código/configuración confiable, se validan y se
usan siempre con consultas parametrizadas para los valores.

## Contrato común de una vista

Cada vista incorporada debe cumplir estas reglas:

1. Es de solo lectura y vive bajo el esquema `consumo`.
2. Tiene una clave estable, no nula y única para ordenar la paginación. En
   `clientes` es `cliente_key`; para otras vistas se espera, por ejemplo,
   `proveedor_key` o `supervisor_key`.
3. El repositorio selecciona una lista explícita de columnas. No se permite
   `SELECT *`, porque una modificación del datawarehouse no debe cambiar el
   contrato HTTP sin revisión.
4. El modelo interno conserva la clave técnica para generar el cursor, pero la
   respuesta HTTP solo incluye los campos funcionales definidos para la vista.
5. Las columnas opcionales se representan como `str | None` cuando el origen
   las entrega como texto nullable. El adaptador debe convertir o validar
   cualquier tipo distinto antes de construir la entidad.

La paginación usa el contrato:

```text
GET /api/v1/{recurso}?limit=50&cursor={cursor}
```

La respuesta siempre tiene esta forma:

```json
{
  "items": [],
  "next_cursor": "...",
  "has_more": true
}
```

`limit` acepta de 1 a 200 registros. La consulta ordena por la clave estable,
aplica `clave > cursor_key`, solicita una fila adicional para calcular
`has_more` y nunca ejecuta `COUNT(*)` ni usa `OFFSET`. El cursor es opaco,
versionado, asociado al proveedor y firmado por la API. Un cursor inválido
responde `422`.

## Estructura de implementación

Una nueva vista debe conservar estas fronteras del módulo
`app/modules/consumption`:

| Capa | Responsabilidad | Ejemplo para proveedores |
| --- | --- | --- |
| `domain/entities.py` | Entidad sin FastAPI ni SQLAlchemy | `Supplier` con `supplier_key` |
| `domain/ports.py` | Contrato de lectura paginada | `SupplierDataSource` |
| `infrastructure/repository.py` | SQL parametrizado y mapeo de la vista | `WarehouseSupplierRepository` |
| `application/service.py` | Caso de uso y cursor común | `list_suppliers()` |
| `presentation/router.py` | DTO HTTP, autenticación y errores | `GET /api/v1/proveedores` |
| `v1_containers.py` | Wiring del repositorio y servicio | providers de la vista |

Para una vista pequeña se pueden mantener varias entidades y repositorios en
los archivos existentes. Si el módulo crece, se pueden separar por recurso en
subpaquetes, manteniendo las mismas interfaces y responsabilidades.

## Procedimiento para agregar una vista

### 1. Confirmar el origen

Antes de programar, consultar los metadatos del datawarehouse y registrar en
la documentación del recurso:

- nombre exacto: `consumo.<vista>`;
- clave de paginación y evidencia de que es estable, no nula y única;
- columnas, tipos y nulabilidad;
- volumen aproximado y cualquier filtro obligatorio del negocio.

La cuenta configurada debe tener permisos de lectura únicamente sobre
`consumo`. Si la vista no tiene una clave apta para cursor, se debe resolver
esa deficiencia en el origen o acordar una estrategia explícita antes de crear
el endpoint.

### 2. Definir la entidad y el puerto

La entidad contiene la clave técnica y únicamente los campos que el caso de
uso necesita. El puerto no conoce SQL, FastAPI ni el cliente PostgreSQL:

```python
class SupplierDataSource(Protocol):
    async def fetch_suppliers(
        self, after_key: str | None, limit: int
    ) -> tuple[list[Supplier], bool]: ...
```

El método debe devolver como máximo `limit` elementos y un indicador de que
existe otra página.

### 3. Crear el adaptador de la vista

El repositorio debe declarar sus columnas y construir solo dos variantes de la
consulta: primera página y continuación. El nombre de la vista no se recibe
del request. El patrón para supervisores es el mismo:

```python
class WarehouseSupervisorRepository:
    relation = '"consumo"."supervisores"'
    columns = ("supervisor_key", "nit", "nombre", "correo", "celular")

    async def fetch_supervisors(
        self, after_key: str | None, limit: int
    ) -> tuple[list[Supervisor], bool]:
        # SELECT <columns> FROM relation
        # WHERE supervisor_key IS NOT NULL [AND supervisor_key > $1]
        # ORDER BY supervisor_key ASC LIMIT $n
        ...
```

El adaptador reutiliza `DataProviderConnection.fetch()`, que abre el pool bajo
demanda, ejecuta la consulta en modo de solo lectura y traduce fallos de
conectividad a `503` en la capa HTTP.

### 4. Reutilizar el servicio de aplicación

El servicio debe delegar la validación y generación del cursor en
`ClientCursorCodec` o en un codec equivalente parametrizado por el nombre del
recurso/proveedor. No debe exponer el repositorio al router ni implementar SQL.

Cuando varios recursos compartan exactamente el comportamiento de cursor, se
debe extraer un servicio genérico antes de duplicar la lógica; el contrato
HTTP seguirá siendo independiente de PostgreSQL.

### 5. Exponer el endpoint

El router debe:

- exigir el usuario autenticado mediante el SSO existente;
- validar `limit` y `cursor` con FastAPI/Pydantic;
- convertir un cursor inválido en `422`;
- convertir un proveedor no disponible en `503`;
- mapear solo los campos públicos del recurso;
- evitar datos sensibles en logs y respuestas de error.

Rutas recomendadas:

```text
GET /api/v1/clientes
GET /api/v1/proveedores
GET /api/v1/supervisores
```

### 6. Registrar el wiring

Cada nueva vista debe registrarse explícitamente en `V1Container` y en
`v1_router.py`. No se permite crear repositorios, pools o contenedores dentro
de un endpoint. El pool físico sigue siendo único para el proveedor
configurado y se cierra junto con la aplicación.

## Pruebas obligatorias

Cada recurso debe probar:

- primera página, continuación y última página;
- cursor inválido, cursor de otro proveedor y límite fuera de rango;
- ausencia de `COUNT(*)`, `OFFSET` y `SELECT *` en la consulta;
- mapeo de valores nullable y rechazo de una clave técnica inválida;
- respuesta `401` sin sesión y `503` cuando el proveedor está deshabilitado o
  no disponible;
- que los registros no se repiten ni se omiten cuando el origen permanece
  estable.

La validación de integración debe consultar una página pequeña del
datawarehouse sin imprimir valores de clientes, proveedores o supervisores.
Los cambios de vistas no crean tablas locales ni migraciones Alembic: si en el
futuro se decide extraer los datos a una base transaccional, se implementará un
nuevo adaptador detrás del mismo puerto de aplicación.
