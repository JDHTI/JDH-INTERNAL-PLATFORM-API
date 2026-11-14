# Mapa comercial

Prospectos de clientes sobre el mapa de Colombia, zonas comerciales trazadas y rutas de visita
por calles. Más adelante, los clientes actuales del DWH sobre el mismo mapa.

Módulo `territory` en la API, clave de permiso `mapa-comercial`, prefijo `/api/v1/territory`.
Migración `0063_territory`. Requiere PostGIS.

## Piezas y costo

| Pieza | Qué se usa | Costo |
| --- | --- | --- |
| Datos geográficos | PostGIS en el mismo Postgres (imagen `postgis/postgis:16-3.5-alpine`) | 0 |
| Mapa | MapLibre GL JS en la UI | 0 |
| Mapa base | Un archivo `colombia.pmtiles` (Protomaps, datos OSM) servido como estático | 0, sin llave |
| Ruteo por calles | openrouteservice (HeiGIT), solo desde la API | 0: 2.000 rutas/día, sin tarjeta |
| Zonas | Polígonos propios dibujados en la UI y capas oficiales cargadas una vez | 0 |

Nada se consulta en tiempo real. Una ruta se calcula una vez y se guarda; abrirla no gasta cuota.
Si el ruteo no responde o se agota la cuota, la ruta se guarda en línea recta y se puede
recalcular. El ruteo está detrás de un puerto (`RoutingProvider`): cambiarlo por Valhalla u OSRM
propios es cambiar un adaptador.

## Permisos

| Acción | Permiso |
| --- | --- |
| Ver mapa, prospectos, zonas y rutas | `read` de `mapa-comercial` |
| Importar prospectos, corregir ubicación, crear y editar zonas y rutas | `manage` de `mapa-comercial` |
| Marcar el resultado de la visita de una parada | `manage`, o ser el responsable de la ruta |

Viene encendido en `comercial`. `GET /territory/permissions` está abierto: responde `false` en
vez de 403.

## Archivo mensual de prospectos

Excel (`.xlsx`) con una hoja por zona. Se leen todas las hojas que tengan el encabezado esperado;
las demás (`Consolidado`, `Descartados`) se ignoran. El nombre de la hoja es la zona de origen.

| Columna | Obligatoria | Uso |
| --- | --- | --- |
| `ID` | sí | Clave de reimportación. Única en todo el archivo |
| `Establecimiento` | sí | Nombre comercial |
| `Categoría` | no | `Restaurante / comida`, `Bar`… |
| `Cliente antiguo` | no | `Sí` marca un ex-cliente |
| `Última compra` | no | Mes de la última compra |
| `Dirección`, `Sector` | no | Texto libre |
| `Ubicación` | no | `Dentro del área`, `Fuera del área`, `Por ubicar` |
| `Teléfono / WhatsApp`, `Correo`, `Página web` | no | Texto libre; puede haber varios por celda |
| `Latitud`, `Longitud` | no | Recomendadas. Punto decimal |
| `Mapa` | no | Si no hay `Latitud`/`Longitud`, la coordenada se toma del hipervínculo `…maps?q=lat,lon` |
| `Observación` | no | Texto libre |

Reglas de la carga:

- Simula salvo `apply=true`. Es todo o nada: un error bloquea la carga.
- Crea o actualiza por `ID`. **No desactiva** prospectos que el archivo ya no trae: cada corte
  mensual puede cubrir zonas distintas.
- Conserva lo que se hizo a mano: una ubicación corregida (`manual`) y el estado de visita no se
  pisan.
- Una coordenada fuera de Colombia es error de fila.
- Si el mismo punto aparece en 3 o más filas, esas filas quedan con ubicación `approximate`
  (geocodificación a un centroide; en el corte de 2026-10 pasa en Itagüí y Centro).
- Teléfonos y correos se guardan como vienen (`contact_raw`) y como lista limpia.

**Para el agente que genera el archivo:** emitir `Latitud` y `Longitud` como columnas numéricas
evita depender del hipervínculo.

## Contrato HTTP

Todo bajo `/api/v1/territory`. Fechas ISO 8601. Coordenadas WGS84: en GeoJSON `[lon, lat]`; en
JSON plano `latitude` y `longitude`.

### Vocabulario

| Campo | Valores |
| --- | --- |
| `location_quality` | `exact`, `approximate` (punto compartido), `manual` (corregido a mano), `missing` |
| `area_status` | `inside`, `outside`, `unlocated`, `null` |
| `visit_status` | `pending`, `visited`, `interested`, `discarded` |
| `zone.layer` | `official` (referencia), `commercial` (de JDH; segmenta los prospectos) |
| `zone.level` | `department`, `municipality`, `commune`, `neighborhood`, `custom` |
| `zone.color` | `gray`, `brown`, `orange`, `yellow`, `green`, `blue`, `purple`, `pink`, `red` (paleta `--project-*` de la UI) |
| `route.geometry_status` | `road` (por calles), `straight` (sin ruteo, líneas rectas), `pending` (sin calcular) |

### Permisos

`GET /permissions` → `{ "can_read": bool, "can_manage": bool }`

### Prospectos (fase 1)

`GET /prospects/map` → GeoJSON `FeatureCollection` de los prospectos con ubicación.

- Filtros: `source_zone`, `category`, `zone_id`, `former` (bool), `visit_status`, `q` (nombre,
  dirección o `ID`). `zone_id` acepta una zona comercial (los asignados a ella) o una oficial
  (los que caen dentro, p. ej. todos los de la comuna El Poblado).
- Cada `Feature`: `id` = id del prospecto, `geometry` `Point`, `properties`:
  `{ id, external_id, name, category, source_zone, sector, is_former_customer, location_quality,
  commercial_zone_id, visit_status }`.

`GET /prospects` → `{ items: ProspectSummary[], total, page, size }`

- Mismos filtros, más `located` (bool; `false` = los "por ubicar"), `page` (1…), `size` (≤ 200).
- Orden: nombre.
- `ProspectSummary`: `{ id, external_id, name, category, source_zone, sector, address,
  is_former_customer, location_quality, latitude, longitude, commercial_zone, visit_status }`.

`GET /prospects/facets` → valores para los filtros, con conteo:
`{ source_zones: [{value, count}], categories: [{value, count}], total, located, unlocated }`.

`GET /prospects/{id}` → `ProspectDetail`:

```json
{
  "id": "uuid", "external_id": "GUA-003", "name": "Mikaela Punto Planta Guayabal",
  "category": "Restaurante / comida", "source_zone": "Guayabal",
  "sector": "Barrio Guayabal (1510), Comuna 15", "address": "Calle 10 Sur # 50FF-120",
  "area_status": "inside", "is_former_customer": false, "last_purchase_month": null,
  "phones": [{ "number": "3137768650", "display": "313 776 8650", "label": "WhatsApp · central", "is_mobile": true }],
  "emails": ["mikaela@mikaela.com.co"], "website": "https://www.mikaela.com.co/pages/tiendas",
  "observation": null,
  "contact_raw": { "phone": "313 776 8650 (WhatsApp · central)", "email": "mikaela@mikaela.com.co" },
  "latitude": 6.21, "longitude": -75.58, "location_quality": "exact",
  "commercial_zone": { "id": "uuid", "name": "Guayabal", "color": "blue" },
  "visit_status": "pending", "visit_note": null, "visited_at": null,
  "load_id": "uuid", "created_at": "…", "updated_at": "…"
}
```

`is_mobile` = celular colombiano (10 dígitos que empiezan por 3): la UI ofrece WhatsApp
(`https://wa.me/57<number>`). `last_purchase_month` es una fecha `YYYY-MM-01`.

`PATCH /prospects/{id}` (manage) → `ProspectDetail`. Cuerpo, todo opcional:
`{ latitude, longitude, visit_status, visit_note }`. Latitud y longitud van juntas; mover el punto
deja `location_quality = manual` y recalcula la zona comercial.

`POST /prospects/import?apply=false|true` (manage), `multipart/form-data` con `file` →

```json
{
  "applied": false, "blocked": false, "load_id": null,
  "rows_read": 2155, "with_location": 2012, "without_location": 143, "approximate": 264,
  "to_create": 2155, "to_update": 0, "unchanged": 0,
  "sheets": [{ "name": "Aranjuez", "rows": 53, "with_location": 51, "without_location": 2 }],
  "errors": [{ "sheet": "Centro", "row_number": 14, "field": "ID", "message": "…" }],
  "errors_truncated": false
}
```

`GET /prospects/loads` (manage) → últimas cargas:
`[{ id, file_name, rows_read, created, updated, unchanged, loaded_by, loaded_at }]`.

### Zonas (fase 2)

`GET /zones?layer=commercial|official&level=…&simplify=true` → `FeatureCollection`.

- `properties`: `{ id, layer, level, name, code, parent_id, color, source, prospect_count }`.
- `simplify` (por defecto `true`) devuelve geometría simplificada para pintar.
- `prospect_count`: en una zona comercial, los prospectos asignados a ella (si dos se solapan,
  cada prospecto cuenta en la más pequeña); en una oficial, los prospectos que caen dentro del
  polígono (un prospecto cuenta en su comuna y en su barrio).

`GET /zones/{id}` → `Feature` con geometría completa.

`POST /zones` (manage) → `Feature`. Crea una zona comercial:
`{ name, color, geometry }`, con `geometry` GeoJSON `Polygon` o `MultiPolygon`.

`POST /zones/adopt` (manage) → `Feature`. Crea una zona comercial uniendo zonas oficiales:
`{ name, color, official_zone_ids: [uuid, …] }`.

`PUT /zones/{id}` (manage, solo capa comercial) → `Feature`. `{ name?, color?, geometry? }`.

`DELETE /zones/{id}` (manage, solo capa comercial) → 204.

Crear, mover o borrar una zona comercial reasigna los prospectos. Si dos zonas se solapan, gana
la más pequeña. La geometría se repara con `ST_MakeValid`; una geometría vacía o inválida tras
repararla es 422.

Las zonas oficiales se cargan con `scripts/import-geo-zones.py` (ver más abajo).

### Rutas (fase 3)

`GET /routes?page&size&mine=true|false` → `{ items: RouteSummary[], total, page, size }`.
Orden: fecha planeada más reciente primero (sin fecha al final), luego la más nueva.
`RouteSummary`: `{ id, name, planned_for, assignee, stop_count, visited_count, distance_m,
duration_s, geometry_status, created_by, created_at, updated_at }`, con
`assignee` y `created_by` = `{ id, email, display_name }` o `null`. `duration_s` es `null` en
una ruta en línea recta.

`POST /routes` (manage) → `RouteDetail`, `201`. Crea la ruta y la calcula:

```json
{ "name": "Belén martes", "planned_for": "2026-11-18", "assignee_email": "vendedor@jdh.com.co",
  "origin": { "latitude": 6.25, "longitude": -75.6, "label": "Bodega" },
  "prospect_ids": ["uuid", "…"], "optimize": true }
```

- `origin` es opcional; sin él, la ruta empieza en la primera parada.
- Entre 2 y 48 paradas, todas con ubicación (422 si no).
- `optimize: true` reordena las paradas para la menor duración; `false` respeta el orden dado.

`GET /routes/{id}` → `RouteDetail`: lo del resumen más `origin` (`{ latitude, longitude, label }`
o `null`), `geometry` (GeoJSON `LineString` o `null`), `routing_note` (por qué no va por calles,
o `null`) y `stops`:
`[{ id, position, prospect_id, name, address, latitude, longitude, visit_status, visit_note,
visited_at, leg_distance_m, leg_duration_s }]`. `position` empieza en 1. El tramo de una parada
es el que llega a ella; la primera parada de una ruta sin origen no tiene tramo (`null`). En
línea recta solo hay `leg_distance_m`.

`PUT /routes/{id}` (manage) → `RouteDetail`. `{ name?, planned_for?, assignee_email?, origin?,
prospect_ids? }`. Solo cambia lo enviado; `null` borra la fecha, el responsable o el origen.
Cambiar paradas u origen deja `geometry_status = pending` hasta recalcular. Una parada que
sigue en la ruta conserva el resultado de su visita.

`POST /routes/{id}/compute` (manage) → `RouteDetail`. `{ optimize: bool }` (por defecto
`false`). Gasta cuota de ruteo.

Crear y recalcular rutas, y la carga del archivo, tienen límite `strict`
(`docs/rate-limiting.md`).

`PATCH /routes/{id}/stops/{stop_id}` (manage o responsable) → la parada.
`{ visit_status?, visit_note? }`. También actualiza el estado del prospecto.

`DELETE /routes/{id}` (manage) → 204.

Una parada guarda copia del nombre y del punto del prospecto: la ruta sobrevive a
reimportaciones.

## Zonas oficiales

`scripts/import-geo-zones.py <archivo.geojson> --level municipality --name-property NOMBRE
[--code-property CODIGO] [--source "DANE MGN 2023"] [--apply]`. Simula salvo `--apply`. Reemplaza
las zonas oficiales del mismo nivel y código (sin `--code-property`, el código es el nombre).
El GeoJSON debe venir en WGS84; un shapefile se convierte antes con
`ogr2ogr -f GeoJSON -t_srs EPSG:4326 salida.geojson entrada.shp`. Omite, y lo dice, lo que
quede fuera de Colombia o no sea un polígono.

En el servidor corre dentro del contenedor:
`docker compose -f deploy/compose.prod.yaml --env-file ~/apps/platform/api.env run --rm api poetry run python scripts/import-geo-zones.py …`
(con el archivo copiado a `scripts/`).

Fuentes abiertas:

- **Comunas y corregimientos de Medellín, y municipios del Valle de Aburrá: OpenStreetMap.**
  `scripts/fetch-osm-zones.py <carpeta>` los baja de Nominatim y deja dos GeoJSON listos para
  el comando de arriba. Su encabezado trae los comandos de carga (`--level commune` y
  `--level municipality`, con `--code-property osm_id`). Son 16 comunas, 5 corregimientos y
  10 municipios.
- Barrios de Medellín: GeoMedellín (Alcaldía). OpenStreetMap no los tiene completos. El
  servidor de mapas de la Alcaldía (`medellin.gov.co/servidormapas`) respondía 503 el
  2026-11-14; la capa es `Limite Barrio_Vereda Catastral`.
- Municipios de todo el país: DANE, Marco Geoestadístico Nacional (geoportal.dane.gov.co).
- No hay fuente abierta confirmada para barrios de Envigado, Sabaneta, Bello e Itagüí: se dibujan
  como zonas comerciales o quedan a nivel municipio.

Una zona oficial es de referencia: se pinta su contorno, cuenta los prospectos que caen dentro
y filtra el mapa (`zone_id`), pero no asigna prospectos. Para segmentar con ella se adopta como
zona comercial (`POST /zones/adopt`).

## Configuración

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `ROUTING_PROVIDER` | `openrouteservice` | `none` desactiva el ruteo: las rutas quedan en línea recta |
| `ROUTING_API_KEY` | vacío | Llave gratuita de account.heigit.org. Vacía = línea recta |
| `ROUTING_DIRECTIONS_URL` | `https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson` | Trazado por calles |
| `ROUTING_OPTIMIZATION_URL` | `https://api.heigit.org/vroom/v0` | Orden de visita (VROOM) |
| `ROUTING_TIMEOUT_SECONDS` | `20` | Por llamada |
| `ROUTING_MAX_STOPS` | `48` | Paradas por ruta, entre 2 y 48 (el plan gratuito admite 50 puntos con el origen) |
| `TERRITORY_MAX_UPLOAD_MB` | `10` | Tamaño máximo del Excel |

HeiGIT retiró `api.openrouteservice.org` en septiembre de 2026; las URL nuevas siguen el
patrón `api.heigit.org/<servicio>/<versión>` y la optimización pasó a `vroom/v0`. La llave va
en el encabezado `Authorization`. **Pendiente:** probar con una llave real desde el servidor
(crear una ruta y ver `geometry_status = road`); si HeiGIT cambia una ruta, se corrige en estas
variables sin tocar código.

Si el ruteo falla (sin llave, cuota agotada, límite por minuto, servicio caído o una parada
lejos de una vía), la ruta se guarda igual: orden por vecino más cercano, líneas rectas,
`geometry_status = straight` y el motivo en `routing_note`.

## Despliegue

La imagen de Postgres pasa de `postgres:16-alpine` a `postgis/postgis:16-3.5-alpine`: misma
versión mayor y misma base Alpine, así que el volumen existente abre tal cual. Pasos en el
servidor, antes de fusionar a `master` (cada fusión despliega):

1. Respaldo: `docker compose -p internal-platform-api exec postgres pg_dump -U platform -Fc internal_platform > respaldo-0063.dump`.
2. Desplegar normal (`deploy/deploy.sh`): recrea `postgres` con la imagen nueva y `migrate`
   crea la extensión `postgis` y las tablas.
3. Comprobar: `docker compose -p internal-platform-api exec postgres psql -U platform -d internal_platform -c "SELECT postgis_version()"`.
4. Poner `ROUTING_API_KEY` en `~/apps/platform/api.env` y reiniciar la API.

`deploy/postgres/10_postgis.sh` reemplaza el script de arranque de la imagen: en una base
nueva solo se crea `postgis` (por la migración), no `topology` ni el geocodificador TIGER, que
Alembic leería como tablas ajenas. En el volumen existente no corre.

Desarrollo: `docker compose up -d postgres` con el `compose.yaml` nuevo cambia la imagen del
Postgres local. Ojo: es la base compartida de todos los worktrees; uno sin la migración `0063`
no reconoce la revisión. Para probar aparte, un contenedor propio:
`docker run -d --name territory-postgis -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=internal_platform -v ./deploy/postgres/10_postgis.sh:/docker-entrypoint-initdb.d/10_postgis.sh:ro postgis/postgis:16-3.5-alpine`.

Pruebas contra PostGIS real: `TERRITORY_TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/internal_platform poetry run pytest tests/modules/territory/test_postgres.py`
(sobre una base migrada propia: vacía las tablas del módulo).

## Mapa base (UI)

Un solo archivo `colombia.pmtiles` recortado del build diario de Protomaps, más los glifos de
Centrale Sans. En desarrollo va en `public/mapas/` de la UI (fuera de git). En producción lo
sirve Caddy del stack edge en `/mapas/*` (paso manual en el servidor). Atribución
"© OpenStreetMap" visible.

## Fase 4: clientes actuales

`consumo.clientes` no trae coordenadas ni barrio. Pintar clientes exige geocodificar
`direccion` + `ciudad` una vez (o que el ERP/DWH traiga coordenadas). Queda pendiente esa
decisión; el modelo lo admite con una tabla `customer_locations` por `cliente_key`.
