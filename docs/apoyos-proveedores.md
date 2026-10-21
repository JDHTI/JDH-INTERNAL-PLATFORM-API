# Maestro de apoyos de proveedores

Reemplaza el libro **«1. MAESTRO APOYOS PROVEEDORES.xlsx»** (22 hojas, 41 tablas,
un formato distinto por proveedor) por un maestro único, enlazado con los
maestros reales del datawarehouse (proveedores, clientes y productos), con
filtrado dinámico y CRUD. Esta primera entrega es solo backend, para validar
resultados.

Módulos: `commercial_masters` (espejo de los maestros del DWH) y
`supplier_supports` (los apoyos). Migraciones `0017` a `0019`.

## Maestros comerciales (`commercial_masters`)

Se copian desde las vistas `consumo.*` del DWH (PostgreSQL, solo lectura) a tablas
propias con columnas reales:

| Tabla | Origen | Llave | Notas |
| --- | --- | --- | --- |
| `commercial_suppliers` | `consumo.productos` (`cod_proveedor`, `proveedor`) | `code` | Es el **«Cod. Proveedor»** del Excel (649 códigos). No es una identidad tributaria. Guarda el margen lista 1 y sus excepciones. |
| `commercial_supplier_nits` | `consumo.producto_proveedor` | `(proveedor comercial, nit)` | NITs probables por historial de compras; el de más productos es el principal. Se confirman a mano (`PATCH /commercial/suppliers/{code}`). |
| `suppliers` | `consumo.proveedores` | `proveedor_key` | Proveedor legal por sucursal. Sin datos bancarios ni de contacto. |
| `customers` | `consumo.clientes` | `cliente_key` | Cliente por sucursal. Se omite el registro `cia = -1` («desconocido»). El NIT **no** es único. |
| `products` | `consumo.productos` | `producto_key`, `sku` | `sku` es la **referencia JDH**. |
| `product_suppliers` | `consumo.producto_proveedor` | par de llaves | Relación real producto–proveedor legal. |
| `supplier_product_codes` | hojas `Maestro_Sku_*` | proveedor + SKU proveedor + producto | Código del proveedor y factor de empaque. |

Reglas de importación: el NIT se guarda **sin dígito de verificación** (como el DWH);
`is_active` = `estado = 'A'` y `estado_sucursal = 'A'`; las filas que dejan de venir
del DWH se **desactivan** (nunca se borran), y una lectura vacía jamás desactiva nada.
No hay sincronización incremental: las marcas de tiempo de SIESA no indican
modificaciones reales, así que cada corrida es completa.

## Formato estándar de apoyo

Una fila de `supplier_supports` es **una regla**: alcance + beneficio + vigencia.
Los márgenes y precios apoyados **no se guardan**: se calculan (ver abajo). Lo que sí
se guarda es el costo y precio lista de referencia con su fecha (*snapshot*), para que
un apoyo no cambie en silencio cuando cambia el costo vigente.

| Grupo | Campos |
| --- | --- |
| Identidad | `commercial_supplier_id` (por `supplier_code`), `kind`, `funding`, `program`, `external_ref` |
| Alcance (vacío = todos) | `customer_nit`, `customer_branch_id`, `customer_name`, `customer_group`, `product_sku`/`product_id`, `supplier_sku`, `brand`, `regionals[]`, `business_units[]`, `channels[]`, `warehouse` |
| Beneficio | `calc_method`, `supplier_value`, `jdh_pct`, `value_unit`, `target_margin_pct`, `min_margin_pct`, `list_margin_pct`, `pack_factor` |
| Referencia | `reference_cost`, `reference_list_price`, `reference_date` |
| Condiciones | `min_quantity`, `committed_volume`, `budget_amount`, `budget_units`, `units_used`, `stock_quantity`, `conditions`, escalas en `tiers[]` |
| Vigencia y estado | `valid_from`, `valid_to` (vacío = indefinido / UFN), `end_condition` («HASTA NIVELAR COSTO»), `status` |
| Liquidación | `settlement_method`, `settlement_frequency` |
| Trazabilidad | `authorized_by`, `supplier_contact`, `sales_rep`, `notes`, `source`, `source_key`, `issues[]`, `created_by`, `updated_by`, `version` |

Valores cerrados (`GET /supplier-supports/catalogs`):

- `kind`: `tactico_cliente`, `actividad_temporal`, `lista_especial`, `rebate`,
  `recuperacion_cliente`, `liquidacion_inventario`, `presupuesto`, `excepcion_margen`.
- `funding`: `proveedor`, `jdh`, `mixto`. `settlement_method`: `nota_credito`,
  `pie_factura`, `costo_neto_facturado`, `rebate_periodico`.
- **Estado visible (`state`)**: un solo valor para las personas: `vigente`, `vencido`,
  `programado` (calculados por las fechas a la fecha consultada, zona America/Bogotá),
  `inactivo` (desactivado a mano con el interruptor `is_enabled`, reversible: se ve
  atenuado y queda fuera de lo vigente y del apoyo efectivo) o `anulado` (irreversible,
  gana sobre todo lo demás). La revisión de datos es un indicador aparte, `needs_review`
  (verdadero cuando el apoyo quedó `pendiente_revision` por inconsistencias); nunca es
  un estado. Por eso ya no existe la combinación «activo + vencido».
- `status` (`activo`, `pendiente_revision`, `anulado`) es la columna interna del
  registro y sigue en la respuesta por compatibilidad; las pantallas deben mostrar
  `state` y `needs_review`. Un apoyo se anula con motivo (endpoint `void`): no se borra
  nada, queda quién y por qué, y se oculta de las búsquedas salvo `state: "anulado"`.

### Cómo se aplica `supplier_value` (`calc_method`)

| `calc_method` | `supplier_value` es… | Dinero del proveedor |
| --- | --- | --- |
| `pct_costo` | fracción del costo (0.048 = 4,8 %) | `costo × valor` |
| `pct_precio_lista` | fracción del precio lista | `lista × valor` |
| `valor_unidad` | monto fijo por unidad (o caja) | `valor` |
| `costo_neto` | costo apoyado pactado | `costo − valor` |
| `precio_neto` | precio neto pactado; `target_margin_pct` es el margen garantizado | `costo − precio × (1 − margen)` |
| `puntos_margen` | puntos de margen que se restan al margen lista | — (margen mínimo = `list_margin_pct − valor`) |
| `pct_ventas` | rebate sobre ventas | — (se liquida por periodo) |
| `margen_minimo` | no usa `supplier_value`; usa `min_margin_pct` | — |

Derivados que devuelve la API (`derived`): `supplier_amount`, `jdh_amount`, `net_cost`,
`pct_on_cost`, `pct_on_list`, `suggested_price`, `margin_order`, `margin_recomposed`,
`min_margin_pct`. Fórmulas de la hoja `Formulas` del Excel: `margen = 1 − costo/precio`,
`precio = costo/(1 − margen)`, `margen recompuesto = 1 − (costo − apoyo)/precio`.

### Precio por unidad y por caja

La unidad mínima de venta (unitario) es la base; la caja es `pack_factor` unidades. Cada
apoyo dice en qué unidad está su costo (`value_unit`: `caja` o `unidad`) y de dónde lo
sabemos (`unit_basis_source`):

- `hoja`: la hoja lo declara (Familia, Kimberly, Team, Ohra: «COSTO CAJA»; Diversey y
  Aderezos: «COSTO UND»).
- `inferido_dwh`: la hoja no lo dice. Se compara el costo del Excel con el costo
  promedio unitario del DWH (`products.avg_unit_cost`, de `consumo.inventario_fecha`,
  sin IVA): parecido a 1× es `unidad`, parecido a N× (el empaque) es `caja`; si no se
  parece a ninguno queda sin decidir.
- `manual`: lo fijó una persona al crear o editar. `desconocido`: no se pudo decidir.

`derived` trae los importes en la unidad original y, cuando se conocen la unidad y el
empaque, también por caja y por unidad: `cost`, `list_price`, `supplier_amount_per`,
`net_cost_per` y `suggested_price_per`, cada uno con `{box, unit}`. Sin empaque no hay
precio unitario (ni de caja si el costo es unitario); el empaque que falta se completa
con el del producto en el DWH. El nombre del producto no forma parte de la estructura
del reporte: se identifica por referencia JDH.

### Porcentaje de apoyo JDH y la regla «Npp»

`jdh_pct` es el descuento que pone JDH, aparte del del proveedor. Es un campo extra de
algunos apoyos (McCain lo muestra con más claridad), editable desde la estructura.

En Diversey el apoyo es el campo `% APOYO DIVERSEY`, un porcentaje sobre el costo
unitario. Un registro que venga como `Npp` (por ejemplo `5pp`) en esa columna, en
`$ APOYO DIVERSEY` o en `SKU JDH` significa N % sobre el costo para **todo el portafolio
del proveedor**: se guarda como `pct_costo` sin producto, sin costo ni unidad y con el
programa `DESCUENTO PORTAFOLIO DIVERSEY`. En el libro actual no hay ninguno.

### Listas de costo apoyado por cliente (Rubbermaid)

En `Lista_Summar_RCP` y `Lista_SodexoCedi_RCP` el apoyo es el **nuevo costo apoyado**
(`costo_neto`: `supplier_value` = «COSTO APOYADO», `reference_cost` = «COSTO REGULAR») y
vale solo para ese cliente. Summar tiene NIT (800125313). Sodexo CEDI no: queda
`pendiente_revision` con la inconsistencia `cliente_por_definir`. Ambas listas abren un
**caso especial**.

## Casos especiales

Peticiones sobre clientes puntuales que el libro no alcanza a describir: sirven para
(1) llevar el seguimiento de lo que se hace con ellos y (2) programar la lógica de negocio
en código. Cada caso tiene título, petición, regla por programar, proveedor y cliente
(opcionales), estado (`pendiente`, `en_analisis`, `programado`, `implementado`,
`descartado`), prioridad, responsable y resolución. Los apoyos que nacieron de un caso
apuntan a él (`special_case_id`). Nacen dos: Summar y Sodexo CEDI.

| Método y ruta | Uso |
| --- | --- |
| `GET /supplier-special-cases` | Lista (`q`, `status`, `customer_nit`, `page`, `size`) con `support_count` |
| `POST /supplier-special-cases` | Abrir un caso (gestores); código `CE-0001`… |
| `GET /supplier-special-cases/{id}` | Detalle |
| `PATCH /supplier-special-cases/{id}` | Solo los campos enviados; exige `version` |

## API (`/api/v1`)

Lectura: cualquier usuario autenticado. Escritura: administradores y miembros del
área `SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG` (por defecto `comercial`).

| Método y ruta | Uso |
| --- | --- |
| `POST /supplier-supports/search` | Filtrado dinámico (abajo) |
| `GET /supplier-supports/search/fields` | Campos, operadores y valores permitidos |
| `GET /supplier-supports/catalogs` | Vocabularios y regionales, unidades, canales y programas en uso |
| `POST /supplier-supports` | Crear (ligado a proveedor, producto y cliente reales) |
| `POST /supplier-supports/batch` | Hasta 500; se validan todos antes de guardar alguno |
| `GET /supplier-supports/{id}` | Detalle con nombres, vigencia, conflictos y derivados |
| `PATCH /supplier-supports/{id}` | Solo los campos enviados (`null` limpia); exige `version` (409 si cambió). `is_enabled: false` desactiva (estado `inactivo`, reversible); también edita la estructura (`jdh_pct`, `supplier_value`, `value_unit`, `pack_factor`…) |
| `POST /supplier-supports/{id}/void` | Anular con motivo; no se borra nada |
| `GET /supplier-supports/{id}/conflicts` | Apoyos que se solapan con este |
| `GET /commercial/suppliers`, `/suppliers/{code}`, `PATCH /suppliers/{code}` | Proveedores comerciales, margen lista y NITs confirmados |
| `GET /commercial/legal-suppliers`, `/customers`, `/products` | Pickers de los maestros |

### Filtrado dinámico

```json
POST /api/v1/supplier-supports/search
{
  "match": "all",
  "conditions": [
    {"field": "supplier.code", "op": "in", "value": ["233", "399"]},
    {"field": "product.brand", "op": "eq", "value": "FAMILIA"},
    {"field": "customer.channel", "op": "in", "value": ["RESTAURANTES"]},
    {"field": "supplier_value", "op": "between", "value": [0.05, 0.2]},
    {"match": "any", "conditions": [
      {"field": "regionals", "op": "overlaps", "value": ["ANTIOQUIA"]},
      {"field": "regionals", "op": "is_empty"}]}
  ],
  "q": "sodexo",
  "state": "vigente",
  "needs_review": false,
  "active_on": "2026-10-09",
  "has_issues": false,
  "effective_only": true,
  "sort": ["-valid_from", "supplier.name"],
  "page": 1, "size": 50
}
```

- Campos: propios (`kind`, `program`, `customer_nit`, `product_sku`, `regionals`,
  `valid_to`, `supplier_value`…), del proveedor (`supplier.*`), del producto
  (`product.*`: marca, categoría, línea…), del cliente (`customer.*`: canal, unidad de
  negocio, grupo, ciudad, vendedor; se consultan en el maestro por NIT) e inconsistencias
  (`issues.code`).
- Operadores por tipo: texto (`eq`, `neq`, `in`, `nin`, `contains`, `starts_with`,
  `is_null`, `not_null`; sin distinguir mayúsculas), número y fecha (`eq`, `neq`, `gt`,
  `gte`, `lt`, `lte`, `between`, `in`), arreglos (`overlaps`, `contains_all`,
  `is_empty`, `not_empty`).
- Grupos anidados (3 niveles) con `match: all | any`. Un campo u operador desconocido
  es 422; nada se interpola en el SQL.
- **Búsqueda base**: `customer_nits`, `supplier_codes` y `product_skus` (listas) filtran
  por cliente, proveedor y referencia. Son independientes y opcionales (puede enviarse
  uno, dos o los tres) y se combinan con AND entre sí y con `conditions`. Estado y tipo
  se filtran libremente aparte.
- `state` (`vigente`, `vencido`, `programado`, `inactivo`, `anulado`) y `needs_review`: los
  filtros rápidos (`inactivo` = desactivado a mano, reversible). `active_on` fija la fecha
  de la vigencia (`validity` sigue aceptándose). `effective_only`: deja el apoyo
  vigente efectivo por proveedor × cliente × SKU (el de inicio más reciente, p. ej. un
  «reemplazo temporal»). `has_conflicts`: apoyos que se solapan en la misma regla y
  periodo (mismo proveedor, tipo, programa, cliente, SKU, SKU del proveedor, bodega y
  regional); **se permiten y se advierten**, no se bloquean. No cuentan como conflicto
  dos filas sin ninguna fecha (son actualizaciones sucesivas de costo) ni filas sin
  cliente, producto ni bodega (programas de todo el proveedor).

## Migración del Excel

```bash
poetry run python scripts/sync-dwh-masters.py            # simulación
poetry run python scripts/sync-dwh-masters.py --apply
poetry run python scripts/import-supplier-supports.py "1. MAESTRO APOYOS PROVEEDORES.xlsx" \
    --report inconsistencias.csv                          # simulación
poetry run python scripts/import-supplier-supports.py "1. MAESTRO APOYOS PROVEEDORES.xlsx" \
    --report inconsistencias.csv --apply
```

Necesita `DWH_*` (usuario de solo lectura limitado al esquema `consumo`). Ambos scripts
son simulaciones salvo `--apply` y se pueden repetir: cada apoyo recuerda su hoja y
fila (`source_key`). Cada tabla del libro tiene su adaptador en
`app/modules/supplier_supports/infrastructure/workbook.py`; los apoyos por proveedor
sin código en el Excel (Aderezos, Azteca, Red Bull) se cruzan por nombre y se imprime
el resultado para revisarlo (`--supplier NOMBRE=CODIGO` lo fuerza).

Nada se descarta por tener datos malos: la fila se importa como `pendiente_revision`
con sus `issues` y el CSV las lista. Pasar un apoyo a `activo` (`PATCH`) limpia sus
inconsistencias.

| `issues.code` | Qué es |
| --- | --- |
| `fecha_invalida` | Fecha imposible (31/09), mal escrita o `1900-…` de una fórmula vacía; los días que no existen se ajustan al fin de mes |
| `fecha_texto` | Fecha leída de texto (`30/10(2026`) |
| `nit_sin_cruce`, `sku_sin_cruce`, `proveedor_sin_cruce` | No existe en el maestro |
| `nit_secuencial_sospechoso` | NIT consecutivo bajo el mismo nombre (error de arrastre) |
| `duplicado` | Renglón repetido en la misma hoja |
| `base_ambigua` | El % se aplica a precio y a costo en la misma hoja (Dodeka/Ohra) |
| `valor_texto`, `valor_faltante` | Porcentaje escrito como texto o no escrito (rebates de Azteca y tercerizados) |

Decisiones tomadas al normalizar (revisables): `TODOS`/`NACIONAL` = sin restricción;
`ANTIQOUIA` → `ANTIOQUIA`; `FOOD` → unidad `FOOD SERVICE`; `MAYORISTAS` → canal
`MAYORISTAS Y DISTRIBUIDORES`; fechas `INDEFINIDO`/`UFN`/vacías = sin fecha final;
`Margenes Lista1` → margen lista del proveedor comercial; `Condiciones Especiales`
(márgenes mínimos autorizados, sin dinero del proveedor) → `excepcion_margen`,
expandidas por proveedor × referencia × cliente; Unilever se carga con el código 399
(ACO 1126 y Professional 1111 quedan por separar); Kimberly con el de Colpapel (1098)
porque no tiene código propio.

## Regional y unidad de negocio por hoja

Cada apoyo trae `regionals`, `business_units` y `channels`. Solo se llenan donde la hoja
los trae (no se deducen del cliente); donde no, quedan vacíos y las pantallas ocultan la
columna si la página no tiene ningún valor. Cobertura del libro actual (% de apoyos):

| Hoja | Regional | Unidad de negocio | Canal |
| --- | --- | --- | --- |
| Mccain, Ohra, Team Panadería, Griffith, Red Bull | 100 % | — | — |
| Azteca | 97 % | 97 % | — |
| Diversey | 97 % | — | — |
| Aderezos | 88 % | 88 % | — |
| Team (desactualizada) | 69 % | — | 1 % |
| Condiciones especiales | 82 % | 93 % | 4 % |
| Familia | — | 70 % | 30 % |
| Rubbermaid | — | — | 41 % |
| Unilever, Kimberly, Estra, GRB | — | — | — |

## Pendiente de definir con el negocio

1. Base del % por proveedor (costo, lista 1, PVP o mayorista): hoy Ohra y Griffith
   mezclan bases.
2. Margen lista autoritativo (la hoja `Margenes Lista1` contradice las hojas de
   Unilever y Diversey).
3. Si `puntos_margen` (Rubbermaid, Upfield) son realmente puntos o un % sobre precio.
4. Si el costo apoyado se factura neto o se reclama por nota crédito.
5. Qué apoyo gana cuando se solapan, y si los de cliente, grupo, canal y regional se
   acumulan.
6. Porcentajes y escalas de los rebates (Azteca, Juniper, «rebate por escalas»).
7. Qué se hace con lo vencido (Azteca, Sodexo CEDI, Team Panadería, hoja Team
   «desactualizada»): hoy se importa como histórico `vencido`.
8. Semántica de regional (Barranquilla, Urabá) y códigos de Kimberly, Aderezos, Azteca
   y Red Bull.
