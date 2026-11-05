# Maestros

Los maestros son un módulo centralizado: la plataforma copia del DWH cada maestro declarado y lo
publica a las áreas donde TI lo active. Módulo: `app/modules/masters`.

## Qué maestros hay

Cada maestro es una fila de `master_definitions` con sus campos en `master_fields` (migración
`0014_masters`). El campo `name` de `master_fields` es la columna real de la vista del DWH, así que
agregar un maestro es una migración que lo declara, no código nuevo.

| Maestro | Origen | Secuencia del DWH | Datos listos (mediana) |
| --- | --- | --- | --- |
| `terceros` | `mart_maestro.dim_tercero` | `identidad` | 03:42 |
| `clientes`, `proveedores`, `vendedores`, `supervisores`, `equipos`, `centros-operacion`, `centros-costo`, `unidades-negocio`, `regionales` | `consumo.*` | `identidad` | 03:42 |
| `productos` | `consumo.productos` | `reporte_ventas` | 07:56 |

**`terceros` no carga todavía.** El usuario del DWH de la plataforma no tiene permiso sobre
`mart_maestro.dim_tercero`. El sync lo reporta como error y sigue con los demás. Falta que el DWH
publique `consumo.terceros` o dé el permiso.

Ventas no es un maestro: es una tabla de hechos y queda fuera de este módulo.

## Sincronización (`MasterCatalogSync`)

- **Cómo lee.** Por cada definición activa arma `SELECT <campos> FROM <origen>` con los
  identificadores validados, y pagina por el campo con rol `key` (`cliente_key`, `producto_key`…).
- **Cómo guarda.** Upsert en `master_records` por `(master_id, source_key)`. `company_id`, `code`,
  `name` e `is_active` salen de los campos con ese rol; el resto va a `attributes`. `is_active` acepta
  `A`/`I` o booleano; sin campo de estado queda vacío.
- **Lo que desaparece.** Tras una lectura no vacía, lo que el sync no vio queda inactivo, nunca se
  borra. Una lectura vacía no toca nada.
- **Errores.** Un maestro que falla se reporta y los demás siguen. Si una vista no existe porque dbt
  la está recreando, reintenta tres veces cada 20 segundos.
- **Cuándo corre.** Los jobs lo disparan cuando el DWH publica datos frescos (`docs/jobs.md`). A mano:

```
poetry run python scripts/sync-master-catalog.py            # simulación
poetry run python scripts/sync-master-catalog.py --apply
poetry run python scripts/sync-master-catalog.py --apply --only clientes
```

Los maestros comerciales (`commercial_*`, de cotizador, validador y apoyos) siguen con su propio sync
tipado (`scripts/sync-dwh-masters.py`). Clientes, proveedores y productos quedan en los dos lados; los
dispara la misma secuencia.

## Quién ve qué

- **Activación por área.** `maestros` se activa por área en el panel de TI
  (`PUT /areas/{slug}/modules/maestros`). Con `{"masters": ["clientes", "proveedores"]}` el área solo ve
  esos; sin lista, todos. Por defecto solo BI lo tiene, con todos los maestros.
- **Permiso.** Hace falta `maestros` read en un rol de un área donde esté activo. Una persona ve la
  unión de los maestros de sus áreas.
- **Datos personales.** Los campos `is_pii` (teléfono, correo, dirección) solo los ve quien tiene
  `maestros` manage o es administrador.
- **Administración.** Owner, admin y moderador ven todos los maestros.

## Endpoints (`/masters`)

| Método y ruta | Uso |
| --- | --- |
| `GET /masters?area=` | Maestros que puedes ver, con `record_count` y `last_synced_at`; `area` limita a los de esa área |
| `GET /masters/{slug}` | Un maestro con sus campos (sin los personales si no gestionas) |
| `GET /masters/{slug}/records?q=&is_active=&offset=&limit=` | Registros, búsqueda por código o nombre |
| `GET /masters/{slug}/records.xlsx?is_active=` | Descarga en Excel, hasta 100.000 filas |

403 si el maestro no está entre los tuyos; 404 si no existe.

## Datos de referencia (local, 2026-11-05)

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
