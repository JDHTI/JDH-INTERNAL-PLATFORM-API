# Gestión comercial: presupuesto comercial y maestro de vendedores

Lo que el ERP no guarda de la fuerza de ventas, en la plataforma (2026-11-26). Reemplaza el
Excel que BI arma cada mes (`PLANTILLA PRESUPUESTO`, la carga `Ppto <mes>`) y la carga del
maestro de vendedores que vivía en la plataforma de proveedores.

- **Módulo:** `app/modules/commercial_budget`.
- **Prefijos:** `/api/v1/commercial-budget` y `/api/v1/vendor-master`.
- **Migración:** `0093_commercial_budget`.
- **Sección de la UI:** «Gestión comercial», con «Presupuesto comercial» y «Maestro de
  vendedores».

## Permisos

| Clave | Áreas por defecto | `read` | `manage` |
| --- | --- | --- | --- |
| `presupuesto-comercial` | BI y Comercial | la vista, el historial, la plantilla y el Excel | diligenciar y cargar el presupuesto; sincronizar las ventas |
| `maestro-vendedores` | BI (se enciende en otras áreas) | la plantilla y el historial de cargas | subir el maestro al DWH |

- **Cerrar y reabrir un mes:**
  - lo hacen los líderes o responsables de BI y de Comercial (`COMMERCIAL_BUDGET_LEADER_AREA_SLUGS`) y los administradores;
  - ningún permiso de módulo lo da.
- **Alcance:** cada persona ve los vendedores de su alcance de ventas (`salespeople`):
  - BI, los directores y los líderes de Comercial ven todo;
  - un coordinador ve su equipo;
  - un vendedor se ve a sí mismo.
  - El presupuesto de un vendedor fuera del alcance responde 403.
  - «Sin vendedor asignado» solo lo ve quien ve todo.

## Reglas

Comprobadas contra la plantilla de BI de septiembre de 2026 (piloto en el DWH, 2026-11-26). Los totales de 2025-09, 2026-06 y 2026-08 cuadran al peso; 2026-07 difiere en 0,005 %.

| Cifra | Regla |
| --- | --- |
| Venta | `sum(subtotal)` de `consumo.ventas`: neta antes de IVA, las devoluciones restan, sin el SKU `NC530535` de descuentos comerciales (comparado con `is distinct from`, porque las filas de 2025 del C.O. 100 no traen SKU). Sin filtro de `concepto` ni de `estado` |
| Margen | `(venta − costo) / venta`, sin recomponer |
| Clientes | sucursales de cliente (`cliente_key`) con venta neta positiva en el mes. Cuadró con la plantilla en los 361 vendedor-mes comparables; el NIT cuadró en 31 |
| Promedio 3 meses | los tres meses anteriores (septiembre: junio, julio y agosto). Es el promedio simple de venta, margen y clientes de los meses en que el vendedor vendió, como el `PROMEDIO` de Excel, que salta las celdas vacías |
| Mismo mes año anterior | la venta, el margen y los clientes de ese mes |
| % var. venta LY | presupuesto de venta / venta del mismo mes del año anterior − 1 |
| Cumplimiento | venta / presupuesto de venta |
| Margen contra ppto | margen − presupuesto de margen (fracción: 0,01 = un punto) |
| Totales (equipo, regional, unidad, compañía) | venta, clientes y presupuestos se suman. El margen real se pondera por venta y el presupuestado por presupuesto (`SUMAPRODUCTO`), como la plantilla |

- **De quién es una venta:** del **vendedor actual** de la sucursal del cliente (`consumo.clientes.cod_vendedor`, copiado a diario a `customers.salesperson_code`). El equipo, la regional y la unidad de negocio salen del maestro de vendedores (`consumo.vendedores`).
  - La venta de un cliente sin vendedor, o que no está en el maestro de clientes, va a «Sin vendedor asignado». Se muestra aparte, nunca se esconde.
- **Mes abierto:** se recalcula con la asignación de hoy, también sus comparaciones. Así compara igual con igual.
- **Cierre:** solo un mes terminado y con sus ventas copiadas. Congela la foto:
  - cada fila, con sus tres cortes y su presupuesto;
  - el equipo, la regional y la unidad de cada vendedor;
  - la asignación cliente → vendedor de cada sucursal que vendió ese mes, con su venta.

  Una reasignación posterior de clientes o de equipos ya no lo cambia. Reabrir descarta la foto y el mes vuelve a calcularse en vivo.
- **Rotación:** el piloto mostró por qué hace falta el cierre. Con el vendedor actual de hoy, la plantilla de septiembre cuadra en 47 de 113 vendedores. Con la asignación del 2026-09-04 cuadra en 81, y el resto es rotación anterior a esa fecha. Desde ahora, cada mes cerrado guarda su propia asignación.
- **Mantener el maestro de clientes al día** (el vendedor de cada sucursal) queda fuera de este módulo.

## Presupuesto

- Por vendedor y mes: venta, margen (fracción), clientes y recaudo. **Cada campo es independiente**: se puede diligenciar uno y dejar los otros vacíos hasta el cierre.
- **Editar a mano** (`PUT /{periodo}/budgets`):
  - cambia solo los campos enviados, y `null` borra uno;
  - la `version` leída evita pisar un cambio ajeno (409);
  - un mes cerrado responde 409.
- **Cargar con Excel** (`GET /{periodo}/budget-template`, luego `POST /{periodo}/budget-upload?apply=`):
  - Primero simula y después aplica.
  - Las columnas se encuentran por encabezado: CÓDIGO VENDEDOR (o el nombre del vendedor), PPTO VENTA, PPTO MARGEN, PPTO # CLIENTES y PPTO RECAUDO.
  - Una celda vacía deja el campo como está, y las filas `TOTAL …` se saltan.
  - El margen va como 0,18, 18 % o 18.
  - También lee el archivo plano de BI (`Ppto Septiembre 2026.xlsx`), que trae el nombre: se busca sin tildes ni mayúsculas y queda una advertencia con el código encontrado.
- **Historial** (`GET /{periodo}/changes`): antes y después de cada campo, el origen (`manual` o `archivo`), la nota, quién y cuándo.
- **Meses:** desde `COMMERCIAL_BUDGET_START_PERIOD` (2026-01) hasta el mes siguiente, para preparar su presupuesto por adelantado.

## Ventas copiadas del DWH

La vista nunca espera al DWH: lee tablas propias.
- `sales_month_customers` guarda la venta, el costo y las líneas por mes y sucursal.
- `sales_month_loads` dice qué meses se copiaron y cuándo.
- `sales_reps` es la copia del maestro de vendedores.

**Tarea diaria `commercial-budget-sales`** (después de `reporte_ventas`, lunes a sábado). Copia:
- el maestro de vendedores;
- el mes en curso;
- el anterior, si no está cerrado;
- hasta `COMMERCIAL_BUDGET_BACKFILL_MONTHS` (24) meses de historia que falten, desde un año antes del inicio (las comparaciones con el año anterior).

Un mes tarda de 2 a 6 s en frío y cerca de 1 s en caliente.

**Sincronizar a pedido** (`POST /sync`, `manage`):
- Encola la misma tarea y responde de inmediato (202). Se sigue con `GET /sync`, que da el estado, los meses copiados y los pendientes.
- Antes corría dentro de la petición. El 2026-11-26 tardó 46 s y el proxy de infraestructura corta a los 30 s, así que mostró un 504 aunque terminó y guardó los meses.
- Sirve para la primera carga o para no esperar a la tarea del día siguiente. Los meses ya copiados no se vuelven a leer, salvo el mes en curso y el anterior.

**Meses sin copiar:** la vista los lista en `missing_months`.

## Maestro de vendedores (`/vendor-master`)

- **Quién es dueño:** dwh-platform, no la plataforma.
  - Escribe en `externo.vendedor_info`, `externo.supervisor` y `externo.unidad_negocio`. `externo.equipo` es de la plataforma desde 2026-11-27 (ver más abajo).
  - Resuelve cada documento contra el ERP.
  - Hace upsert: los vendedores que no vienen en el archivo no cambian.
  - Corre dbt para publicarlo en `consumo.vendedores`.
- **Qué hace la plataforma:**
  - sirve la plantilla «Maestro Vendedores JDH» v4 (`GET /template`), con las hojas COMERCIALES JDH, Coordinadores y Directores, el encabezado en la fila 1 y columnas de texto;
  - reenvía el archivo (`POST /upload`) a `POST {DWH_PLATFORM_URL}/maestro-vendedores/cargar` con `X-API-Key`;
  - devuelve la respuesta tal cual: los conteos, `filas_rechazadas`, `advertencias` y `transformacion`.
- **Respuestas de error:**
  - 400 con el motivo cuando dwh-platform rechaza el archivo;
  - 503 cuando no está configurado o no responde.
- **Historial** (`GET /uploads`): cada carga, con quién, cuándo, el archivo, el tamaño y lo que respondió dwh-platform. **El archivo no se guarda**, porque trae datos personales (documentos, correos, celulares).
- **Configuración:** `DWH_PLATFORM_URL`, `DWH_PLATFORM_API_KEY` y `DWH_PLATFORM_TIMEOUT_SECONDS`. Con la URL vacía la carga queda apagada y `GET /status` lo dice.
- **Plataforma de proveedores:** cuando la carga funcione aquí en producción, se quitan su `/admin/maestro-vendedores`, su cliente de dwh-platform y la tarjeta de su panel. Su copia diaria de `sellers` desde `consumo.vendedores` se queda.

### Equipos desde el maestro externo (2026-11-27)

El Excel es la fuente de verdad, pero los equipos se toman del **maestro de equipos**. Así no se crean nombres nuevos por un error de escritura, como pasó con JUNIOR/CALL MEDELLIN y RETAIL BOGOTA.

- **El maestro de equipos** (`GET /vendor-master/teams`) junta dos cosas:
  - `consumo.equipos`, que es lo que dwh-platform guardó en `externo.equipo`;
  - los equipos creados en la plataforma (`vendor_master_teams`, migración `0096`).

  Cada equipo trae su clase, de dónde viene (`dwh` o `plataforma`) y cuántos vendedores activos tiene.
- **Un equipo nuevo** se crea primero en la plataforma (`POST /teams`, `manage`), con su clase. La plantilla lo ofrece desde ese momento, y la primera carga con un vendedor en él lo crea en el DWH.
- **Cambiar un equipo** (`PATCH /teams/{nombre}`):
  - su clase, que llega al DWH con la siguiente carga;
  - ocultarlo de la plantilla (`is_active: false`). Sirve, por ejemplo, para un equipo que se fusionó.
- **La plantilla** (`GET /template`) viene **llena con el maestro de hoy**: vendedores con su documento, equipo, regional, unidad, coordinador del equipo, correo, celular y C. COSTO, más coordinadores y directores. Con `?empty=true` viene vacía.
  - EQUIPO, en las hojas de vendedores y de coordinadores, es una lista que **Excel no deja salir del maestro**.
  - La CLASE sale sola del equipo, con una fórmula.
- **La simulación** (`POST /preview`) rechaza la fila de un vendedor o de un coordinador cuyo equipo no está en el maestro. La clase que muestra es la del maestro, nunca la del archivo.
- **La carga** se niega si alguna fila tiene un equipo fuera del maestro. Si no, la plataforma escribe la clase del maestro en la columna CLASE de cada fila antes de enviar el archivo a dwh-platform.

### La plataforma escribe `externo.equipo` (2026-11-27)

El maestro de equipos del DWH pasa a ser de la plataforma. Los vendedores (`externo.vendedor_info`) y los coordinadores (`externo.supervisor`) siguen llegando con la carga del Excel por dwh-platform.

- **Rol:** `svc_plataforma_externo`, creado por el equipo del DWH.
  - Solo lee y escribe el esquema `externo`: sin DDL y sin `consumo`.
  - Admite 5 conexiones, con `statement_timeout` de 1 min y `lock_timeout` de 10 s.
  - Entra solo por `hostssl` desde el servidor de la plataforma, directo a Postgres (5432) y no por PgBouncer.
  - La plataforma abre un pool propio de 2 conexiones como máximo (`application_name` `plataforma-maestro-vendedores`). Lo abre con el primer cambio de equipo y lo cierra al apagarse.
- **Configuración:** `DWH_EXTERNO_WRITER_HOST`, `_PORT`, `_DATABASE` (vacío: `DWH_DATABASE`), `_USER`, `_PASSWORD`, `_SSLMODE` y `_POOL_SIZE` (de 1 a 5). `VENDOR_MASTER_COMPANY_ID` es el `id_cia` de un equipo nuevo (1).
  - Sin host o sin usuario todo sigue como en la sección anterior: el equipo nuevo o la clase esperan en la plataforma a la siguiente carga.
  - `GET /status` lo dice con `writes_teams`.
- **Con el rol configurado:**
  - **Maestro:** los nombres y las clases salen de `externo.equipo`, y un cambio se ve de inmediato, sin esperar a dbt. Los vendedores activos salen de `consumo`.
  - **Un equipo nuevo** (`POST /teams`) se inserta en `externo.equipo` con `origen = 'plataforma'` y queda con `source: dwh`. La plataforma solo guarda la nota, si la hay. Si otra carga o persona ya lo creó, responde 409.
  - **Cambiar la clase** (`PATCH /teams/{nombre}`) actualiza `externo.equipo`. Una clase que la plataforma tenía guardada para ese equipo se escribe con el siguiente cambio, y la plataforma deja de guardar la clase.
  - **Ocultar** un equipo solo lo saca de la plantilla. En el DWH no cambia nada, porque `externo.equipo` no tiene columna de activo.
  - **Si el DWH no responde:** 503 y no se guarda nada.
- **Reglas acordadas con el DWH:**
  - nunca se borra un equipo, porque las llaves foráneas son `ON DELETE SET NULL` y dejarían a sus vendedores sin equipo;
  - nunca se renombra un equipo ni se reescribe su `uuid`;
  - `consumo.equipos` y `consumo.vendedores` cambian con la siguiente corrida de dbt.
- **Pendiente en dwh-platform:** su carga del Excel (`_upsert_equipos`) todavía hace `insert … on conflict (nombre) do update set clase`. El equipo del DWH la está dejando como solo lectura del maestro.
  - Mientras tanto, una carga directa a dwh-platform, sin pasar por la plataforma, puede crear equipos o pisar clases.
  - Por la plataforma no pasa: la carga se niega con equipos fuera del maestro y envía la clase del maestro.

## Endpoints

| Método y ruta | Uso |
| --- | --- |
| `GET /commercial-budget/permissions` | `can_read`, `can_manage`, `can_close` y `scope`. Abierto: responde `false`, no 403 |
| `GET /commercial-budget/periods` | Meses desde el inicio hasta el siguiente, con su estado |
| `GET /commercial-budget/{periodo}?regional=&business_unit=&team=&salesperson=&q=` | La vista: KPIs (`totals`), filas por vendedor con `current`, `average`, `last_year`, `budget` y `derived`, grupos por regional, unidad y equipo, subtotales por regional y equipo (`team_groups`, para la tabla regional → equipo → vendedor), opciones de filtro y `missing_months`. Los filtros aceptan varios valores |
| `PUT /commercial-budget/{periodo}/budgets` | `{items: [{salesperson_code, version?, budget_sales?, budget_margin?, budget_customers?, budget_collection?}], note?}` |
| `GET /commercial-budget/{periodo}/changes?salesperson_code=&limit=` | Historial del presupuesto |
| `GET /commercial-budget/{periodo}/budget-template` | Plantilla del mes con los vendedores del alcance |
| `POST /commercial-budget/{periodo}/budget-upload?apply=` | Carga del presupuesto (multipart `file`, `note`) |
| `POST /commercial-budget/{periodo}/close` y `/reopen` | Cierre y reapertura (líderes) |
| `GET /commercial-budget/{periodo}/export` | Excel con la forma de la plantilla de BI: grupos por regional y equipo con sus totales |
| `POST /commercial-budget/sync` | Encola la copia de las ventas y el maestro (`manage`); 202 con la corrida, 409 si ya hay una en cola o en curso |
| `GET /commercial-budget/sync` | La última copia: estado, cuándo, meses copiados (`months`) y pendientes (`pending`) |
| `GET /vendor-master/status` | `configured`, `can_read`, `can_manage` y `writes_teams` (la plataforma escribe `externo.equipo`). Abierto |
| `GET /vendor-master/template?empty=` | Plantilla llena con el maestro de hoy (o vacía), con la lista de equipos del maestro |
| `GET /vendor-master/teams` | Maestro de equipos: `{name, team_class, source, is_active, reps, note}` |
| `POST /vendor-master/teams` | Crear un equipo `{name, team_class, note?}` (`manage`); con `writes_teams`, en `externo.equipo`. 409 si ya existe, 503 si el DWH no responde |
| `PATCH /vendor-master/teams/{nombre}` | Cambiar `team_class` (con `writes_teams`, en `externo.equipo`), `is_active` o `note` (`manage`) |
| `POST /vendor-master/preview` | Simulación de la carga contra el DWH de hoy, sin escribir: `changes`, `rejected`, `warnings`, `new_teams`, `emptied_teams`, `teams`, `missing` y `coordinators` |
| `POST /vendor-master/upload` | Reenvío a dwh-platform (multipart `file`, .xlsx de hasta 10 MB). Se niega si un equipo no está en el maestro, y escribe la clase del maestro |
| `GET /vendor-master/uploads?limit=` | Historial de cargas |
