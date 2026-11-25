# Ajuste de costos

Las reglas de costo del cotizador, en un solo lugar y editables por BI sin desplegar
(2026-11-25). El módulo nace con dos tipos de regla y se puede ampliar con otras reglas de
negocio.

- **Módulo del catálogo:** `ajuste-costos`, activo por defecto en `bi`. Un administrador lo
  activa en otras áreas desde el panel.
  - `read`: ve los parámetros, las reglas y su historial.
  - `manage`: los cambia.
- **API:** `app/modules/cost_settings`, prefijo `/api/v1/cost-settings`.
- **Migración:** `0092_cost_settings`. Tablas:
  - `cost_parameters`: valores fijados por BI;
  - `supplier_branch_rules`: sucursal del proveedor por C.O.;
  - `cost_settings_changes`: historial.
- **Quién lo usa:**
  - el cotizador, Actualización de listas y Listas principales (costo, umbral y rangos);
  - el costo de los apoyos (factor y sucursal);
  - la página de Reglas del cotizador (muestra el factor vigente).
- **Fuera de alcance por ahora:**
  - la liquidación de apoyos;
  - el validador de pedidos, que tiene su propio umbral (`ORDER_VALIDATION_MAX_POINTS_BELOW_TARGET`);
  - la carga de alzas a SIESA del módulo de costos.

## Parámetros

| Clave | Qué es | Por defecto | Rango |
| --- | --- | --- | --- |
| `factor_costo_generales` | Factor sobre el costo de las listas Generales | 1,004 (0,4 % más) | 1 a 1,1 |
| `factor_costo_market` | Ídem, Market | 1,004 | 1 a 1,1 |
| `factor_costo_mayoristas` | Ídem, Mayoristas (también las listas Salsamentarias 10X, 10R y 10S) | 1 (sin factor) | 1 a 1,1 |
| `umbral_negociacion` | Puntos de margen bajo el objetivo que un precio puede bajar y pasar sin alerta (fracción: 0,01 = 1 punto) | `QUOTATIONS_MAX_POINTS_BELOW_TARGET` / 100 (1 punto) | 0 a 0,1 |

- **Valor por defecto:** mientras BI no fija un valor, el parámetro toma su valor por defecto. Por
  eso la migración no cambia nada.
- **Sin Salsamentarias (2026-11-25):** el margen Salsamentarias salió del maestro el 2026-11-23 y el
  canal usa Mayoristas, así que su factor (`factor_costo_salsamentarias`) se quitó el mismo día en
  que nació el módulo. Sus listas de SIESA siguen vivas y se costean como Mayoristas. Un valor
  guardado con esa clave, si lo hubiera, se ignora.
- **Desde cuándo aplica un cambio:** en la siguiente cotización que se arme o se refresque. Las
  guardadas conservan sus precios y su umbral (`max_points_below_target`) hasta refrescarse.

## Sucursal del proveedor por C.O.

En SIESA un proveedor puede tener una lista de precios por sucursal
(`consumo.cotizaciones_proveedores.proveedor_sucursal_id`). El C.O. de la cotización
(`centro_operacion_id`) está vacío en toda la tabla, así que no dice a qué regional va cada lista.
Una regla lo define:

| Proveedor | Sucursal general | C.O. → sucursal | Por qué |
| --- | --- | --- | --- |
| Team Foods (860000006) | 001 | 001 → 002 | Lista de Antioquia desde el 2026-11-19: 4 referencias entre 3,4 y 3,9 % más baratas (aceites Fry) |
| Lactalis (800245795) | 001 | 003 → 003 | Centro. Antes estaba fijo en el código |

**Cómo elige la cotización, referencia por referencia:**
1. La de la sucursal del C.O. del plan.
2. Si no la tiene, la de la sucursal general.
3. Si tampoco, la mayor de las demás.

Luego, como siempre, gana la mayor entre los proveedores de la referencia y se compara con el
inventario y el alza pendiente.

**Cuándo aplica:**
- **Sin regla:** los demás proveedores siguen igual, con la mayor cotización vigente.
- **El C.O. del plan:** es el que factura; el de la lista, si la lista tiene bodegas propias
  (001, 1M y 10X son el 001; 003, 10U y 10S, el 003); o el C.O. donde se marcaron bodegas.
- **Nacional, la lista 1 y Market:** no tienen C.O., así que no aplica regla y toman la mayor.
- **Con varios C.O.:** cada uno se costea por su lado y la línea se queda con el más costoso, como
  siempre.
- **Desactivar una regla** (`is_active: false`) devuelve al proveedor a la mayor cotización.

**Cambio frente a antes en Lactalis:** en Centro, sus referencias sin cotización en la 003 se
quedaban sin costo de cotización. Ahora toman la de la 001.

**Detección:** los proveedores que cotizan en más de una sucursal eran 7 de 288 el 2026-11-25
(consulta del DWH). Solo Team Foods y Lactalis tienen una lista regional reciente. Distrilac,
Familia y Unilever tienen sucursales con cotizaciones viejas sin cerrar, Majestic tiene el mismo
precio en ambas y Centurion compra a una sola. No se configuraron.

## Historial

Cada cambio guarda la entidad (`parametro` o `regla_sucursal`), la clave o el NIT, el antes y el
después, la nota, quién y cuándo. Eliminar una regla también queda en el historial.

## Endpoints

| Método y ruta | Uso |
| --- | --- |
| `GET /permissions` | `can_read`, `can_manage`. Abierto: responde `false`, no 403 |
| `GET ""` | Parámetros vigentes (con su valor por defecto y si BI lo fijó), reglas y los últimos 50 cambios |
| `GET /changes?limit=` | Historial (hasta 200) |
| `PUT /parameters` `{values: {clave: valor}, note?}` | `manage`. Solo los que cambian; 422 si una clave no existe, un valor está fuera de rango o nada cambia |
| `POST /branch-rules` `{supplier_nit, supplier_name?, general_branch?: "001", center_branches: {co: sucursal}, is_active?, note?}` | `manage`. 409 si el proveedor ya tiene regla; 422 si un código no es válido o un C.O. apunta a la sucursal general |
| `PATCH /branch-rules/{id}` | `manage`. Solo las llaves enviadas; `is_active: false` la apaga |
| `DELETE /branch-rules/{id}` | `manage`. 204 |

## Redondeo del precio a un margen

Va en el mismo cambio del 2026-11-25, aunque no es un parámetro: un precio que sale de un margen
(óptimo, a un margen) se redondea siempre hacia arriba. Ver
[cotizador.md](cotizador.md#fórmulas).
