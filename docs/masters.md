# Maestros

## Estándar de maestros generales (2026-11-24)

El módulo `maestros` muestra en cada área los **maestros generales**: los cuatro que BI ve en su
desplegable, cada uno con su página. Es la lista centralizada; un maestro nuevo se agrega aquí y
no se ofrecen otras vistas del DWH.

| Maestro | Página | Datos |
| --- | --- | --- |
| `proveedores-comerciales` | Proveedores comerciales | `/commercial/suppliers` |
| `proveedores-legales` | Proveedores legales | `/commercial/legal-suppliers` |
| `clientes` | Clientes | `/commercial/customers` |
| `productos` | Productos (maestro, alzas y análisis) | `/commercial/products` |

- **Dónde está.** `GENERAL_MASTERS` en `app/modules/areas/domain/catalog.py`.
- **Qué ve cada área:**
  - Un administrador lo decide en el panel de módulos: todos o una lista
    (`PUT /areas/{slug}/modules/maestros` con `{"masters": [...]}`).
  - Un slug fuera del estándar da 422.
  - Un slug viejo guardado antes del estándar no da nada.
- **Qué lee la UI:**
  - El menú: `masters` de cada área en `GET /permissions/me` y en `GET /areas`, en el orden del
    menú; vacío sin el módulo.
  - El panel: el estándar en `general_masters` de `GET /permissions/modules`.
- **Producción al publicar:** BI y Abastecimiento tienen «todos», así que Abastecimiento pasa a ver
  los cuatro (antes la UI le mostraba solo Productos). Comercial tiene `["productos"]`.

## Vistas del DWH (`/masters`, administración de TI)

La plataforma lee cada vista declarada **en vivo** del DWH. No copia los registros. Módulo:
`app/modules/masters`. Desde el estándar estas vistas solo se consultan en Administración y ya no
se ofrecen a las áreas.

## Qué maestros hay

Cada maestro es una fila de `master_definitions` con sus campos en `master_fields` (migración
`0014_masters`). El campo `name` de `master_fields` es la columna real de la vista del DWH, y
`dwh_sequence` (migración `0048`) es la secuencia que la refresca. Agregar un maestro es una migración
que lo declara, no código nuevo.

| Maestro | Origen | Secuencia del DWH | Datos listos (mediana) |
| --- | --- | --- | --- |
| `clientes`, `proveedores`, `vendedores`, `supervisores`, `equipos`, `centros-operacion`, `centros-costo`, `unidades-negocio`, `regionales` | `consumo.*` | `identidad` | 03:42 |
| `productos` | `consumo.productos` | `reporte_ventas` | 07:56 |

**`terceros` quedó retirado** (migración `0048`, `is_active=false`). Ningún módulo lo necesita, porque
clientes y proveedores cubren el negocio, y es el maestro con más datos personales. Responde 404.

Ventas no es un maestro: es una tabla de hechos y queda fuera de este módulo.

## Lectura en vivo

- **Cómo lee.** Por cada consulta arma `SELECT <campos> FROM consumo.<vista>` con los identificadores
  validados. La búsqueda es `ILIKE` sobre código y nombre; el orden, código, nombre y clave. `is_active`
  filtra por `A`/`I` o por el booleano. No hay descarga: los maestros se consultan en la plataforma,
  y los cruces masivos se hacen en el DWH o en las herramientas de BI.
- **Qué devuelve.** `company_id`, `code`, `name` e `is_active` salen de los campos con ese rol; el resto
  va a `attributes`. El `id` de un registro se deriva del maestro y su clave, así que no cambia entre
  lecturas.
- **Caché por corrida.** Cada respuesta (conteo, página con sus filtros) se guarda en memoria con el
  `ultima_corrida_id` de su secuencia en `monitoreo.secuencias`, que se consulta como mucho cada 30
  segundos. Mientras la secuencia no corra otra vez, la respuesta sale de la caché. Una corrida nueva
  la deja vieja y la siguiente consulta va al DWH.
- **Mientras el DWH recarga.** Si la secuencia está `en_curso` (dbt borra y recrea las vistas) o el DWH
  no responde, se sirve la última respuesta guardada con `is_stale: true`. Sin nada guardado: 503 con
  un mensaje para reintentar en unos minutos, y en la lista el conteo queda en `null`.
- **Calentamiento.** Cuando una secuencia publica, los jobs `masters-warm-identity` y
  `masters-warm-products` leen el conteo y la primera página de sus maestros (`docs/jobs.md`). Así la
  primera consulta es rápida y hay copia para la siguiente recarga.
- **Cuidado del DWH.** El DWH tiene dos núcleos y lo comparte BI. Corren a lo sumo
  `MASTERS_DWH_MAX_CONCURRENT` consultas de maestros a la vez (3), cada una con
  `MASTERS_DWH_QUERY_TIMEOUT_SECONDS` (15 s). El pool del DWH de la API es de 5 conexiones.
- **Trazabilidad.** Cada consulta empieza con `/* plataforma:maestros:<slug> */`, visible en
  `pg_stat_activity`. Por ahora la plataforma usa el usuario compartido `svc_bi` (etapa de pruebas);
  después tendrá usuarios propios para que el DWH distinga quién consulta.
- **Una instancia.** La caché vive en el proceso, como los demás workers: asume una sola API.

Medido en el DWH el 2026-11-05: buscar por nombre en clientes, proveedores o productos toma entre 0,02
y 0,12 s.

`master_records` ya no se usa. La tabla se conserva hasta validar la lectura en vivo en producción.

Los maestros comerciales (`commercial_*`, de cotizador, validador y apoyos) siguen copiándose con su
sync tipado (`scripts/sync-dwh-masters.py`): los módulos los cruzan en SQL y guardan datos propios
sobre ellos (márgenes, apoyos).

## Quién ve qué

- **Activación por área.** `maestros` se activa por área en el panel de TI
  (`PUT /areas/{slug}/modules/maestros`). La lista elige entre los maestros generales (arriba);
  sin lista, todos. Por defecto solo BI lo tiene, con todos.
- **Permiso.** Hace falta `maestros` read en un rol de un área donde esté activo. Una persona ve la
  unión de los maestros de sus áreas.
- **Datos personales.** Los campos `is_pii` (teléfono, correo, dirección) solo los ve quien tiene
  `maestros` manage o es administrador. El gobierno de los datos es interno: más adelante se
  restringirán los campos de cada área según el rol.
- **Administración.** Owner, admin y moderador ven todos los maestros.

## Endpoints (`/masters`)

| Método y ruta | Uso |
| --- | --- |
| `GET /masters?area=` | Maestros que puedes ver, con `record_count` (o `null`), `last_synced_at`, `dwh_sequence` e `is_stale`; `area` limita a los de esa área |
| `GET /masters/{slug}` | Un maestro con sus campos (sin los personales si no gestionas) |
| `GET /masters/{slug}/records?q=&is_active=&offset=&limit=` | Registros, búsqueda por código o nombre; trae `last_synced_at` e `is_stale` |

`last_synced_at` es cuándo el DWH publicó esos datos (`ultima_datos_listos`). 403 si el maestro no está
entre los tuyos; 404 si no existe o está retirado; 503 si el DWH no responde y no hay copia.

## Datos de referencia (DWH, 2026-11-05)

| Maestro | Registros |
| --- | --- |
| clientes | 73 246 |
| proveedores | 22 092 |
| productos | 27 852 |
| centros-costo | 1 115 |
| vendedores | 661 |
| equipos | 29 |
| supervisores | 24 |
| unidades-negocio | 14 |
| centros-operacion | 13 |
| regionales | 13 |
