# Monitoreo de aplicativos y tablero general

Módulo del área de TI: **Monitoreo → Aplicativos**, al lado de **Monitoreo →
Datawarehouse**. Revisa que cada plataforma responda en su API (`/api/health`) y en
su UI (la raíz del servidor), guarda el historial y arma un **tablero general** con
los aplicativos y el datawarehouse.

## Qué se monitorea

La tabla `app_mon_aplicativos` (migración `0024_app_monitoring`) se edita desde las
pantallas: se agregan, cambian o apagan aplicativos sin desplegar. La migración
registra tres:

| Slug | Aplicativo | API | UI |
|---|---|---|---|
| `app-proveedores` | App Proveedores | `https://app-proveedores.jdh.local/api/health` | `https://app-proveedores.jdh.local/` |
| `catalogo` | Catálogo (Tienda) | `https://tienda.jdh.local/api/health` | `https://tienda.jdh.local/` |
| `intranet` | Intranet | `https://intranet.jdh.local/api/health` | `https://intranet.jdh.local/` |

Cada aplicativo tiene `url_base` (solo el servidor), `ruta_health`, `ruta_ui`,
`verificar_ui`, `habilitado`, `verificar_tls`, `timeout_s` (10) y
`umbral_lento_ms` (2000).

## Cómo se clasifica un chequeo

| Objetivo | `up` | `degraded` | `down` |
|---|---|---|---|
| API | 2xx, y si el cuerpo JSON trae `status`, que sea `ok`, `up`, `healthy`, `pass`, `ready` o `available` | Más lento que `umbral_lento_ms`, `status` en `degraded`/`warn`, o un componente de `checks`/`components` que no está sano | Otro código, otro `status`, timeout o error de red |
| UI | Cualquier código menor a 400, sin seguir redirecciones: un 302 al login cuenta como que responde (se guarda en `detalle.redirige_a`) | Más lento que `umbral_lento_ms` | 4xx, 5xx, timeout o error de red |

El estado de un aplicativo es el peor entre su API y su UI. Si está deshabilitado o
nunca se ha chequeado, el estado es `null`. `disponibilidad_24h` es el % de
chequeos que no salieron `down` en las últimas 24 horas.

Cada ciclo hace todos los chequeos en paralelo y los guarda en `app_mon_chequeos`.
También borra los chequeos más viejos que la retención.

### Red y certificados

Los chequeos salen del contenedor de la API, así que ese contenedor tiene que
resolver los `*.jdh.local`, ya sea por el DNS interno o con `extra_hosts` en
`compose.yaml`. Si el certificado lo firma una CA interna que el contenedor no
conoce, hay dos salidas: instalar la CA en la imagen, o poner `verificar_tls=false`
en ese aplicativo. Mientras tanto el error queda en `error`, por ejemplo
`ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED]`.

## Configuración

| Variable | Uso |
|---|---|
| `APP_MONITORING_ENABLED` | Enciende los chequeos en segundo plano (`true` en compose y en `.env.example`). |
| `APP_MONITORING_INTERVAL_SECONDS` | Segundos entre ciclos (60). |
| `APP_MONITORING_RETENTION_DAYS` | Días de chequeos guardados (30). |

## Acceso

Miembros del área `ti` y administradores de la plataforma, igual que el monitoreo
del datawarehouse. Los demás reciben 403.

## Endpoints

Prefijo `/api/v1/app-monitoring`:

| Endpoint | Uso |
|---|---|
| `GET /overview` | Conteos `arriba`, `degradados`, `caidos`, `sin_datos`, el peor `estado`, `ultimo_chequeo`, el chequeo automático y todos los aplicativos |
| `GET /applications` | Aplicativos con `estado`, y en `api` y `ui`: `url`, `ultimo` chequeo, `disponibilidad_24h`, `chequeos_24h` y `latencia_media_24h_ms` |
| `POST /applications` | Registra uno: `{nombre, url_base}` y opcionales. 201; 409 si el slug ya existe; 422 si la URL trae ruta |
| `GET /applications/{id}` | Uno con su estado |
| `PATCH /applications/{id}` | Cambia solo los campos enviados |
| `DELETE /applications/{id}` | Lo borra con su historial (204) |
| `POST /applications/{id}/check` | Lo chequea ya, aunque esté deshabilitado, y devuelve el resultado. Sirve para validar uno recién agregado |
| `GET /applications/{id}/checks?objetivo&desde&hasta&page&size` | Historial del más reciente al más antiguo; hoy por defecto |
| `POST /check` | Pide un ciclo ya (202; 409 si el chequeo automático está apagado) |

Tablero general: `GET /api/v1/monitoring/board` devuelve
`{generado_en, estado_general, aplicativos, datawarehouse}`. `aplicativos` es el
mismo `overview`. `datawarehouse` trae su `estado`, `pct_fresca_programadas`,
`atrasadas`, `nunca_extraidas`, las secuencias activas, en curso y fallidas, y la
última carga. El semáforo del DWH queda así:

- `down`: la última carga falló, o la copia lleva más de 3 intervalos sin una carga exitosa.
- `degraded`: hay tablas atrasadas o nunca extraídas, la carga fue parcial, o la última corrida de una secuencia falló.
- `null`: todavía no hay ninguna carga.

`estado_general` es el peor de todos.
