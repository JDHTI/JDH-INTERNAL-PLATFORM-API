# Plan de implementación UI: seguimiento de PQRSF

Acompaña el contrato de [`docs/pqrsf-seguimiento.md`](pqrsf-seguimiento.md).
Se implementa en `internal-platform-ui`, worktree `pqsrf-module`, sobre la ruta
existente `src/app/(app)/[area]/pqrsf/page.tsx` (hoy un estado vacío «Módulo sin
conectar»). Rige `docs/DESIGN.md` de la UI: densidad alta, color solo para
estado y acción, cuatro estados obligatorios, AA como piso.

## Objetivo

Que Calidad deje el Excel P.13.F.01 sin perder lo que el Excel hacía bien
(ver muchas filas a la vez, filtrar, ordenar, exportar) y ganando lo que no
hacía (responsable, vencimientos visibles, historial, permisos).

## Principios de la migración desde Excel

1. **La tabla es la pantalla principal.** Columnas por defecto en el orden del
   Excel: Consecutivo, Fecha de recepción, Cliente, Documentos (resumen «2 de
   3 respondidos»), Ejecutivo comercial, Responsable, Sucursal, Vence, Estado.
   Columnas opcionales (selector de columnas, recordado en `localStorage`):
   Proveedor, Dependencia, Usuario, Tipo, Origen.
2. **El vencimiento se ve sin leer.** Columna «Vence» con fecha y texto
   relativo («hoy», «en 2 días», «hace 3 días»). `is_overdue` usa el badge de
   error; vence hoy usa el de alerta. Nunca solo color: siempre texto.
3. **Filtros persistentes en la URL** (`?status_id=…&overdue=true&q=…`) para
   compartir vistas y volver atrás sin perderlas.
4. **Vistas rápidas** encima de la tabla, alimentadas por `/cases/summary`:
   Mis casos · Sin asignar · Vencidos · Vencen hoy · Abiertos · Todos. Cada
   una con su contador.
5. **Exportar** siempre visible: descarga `/cases/export` con los filtros
   actuales; el archivo abre directo en Excel.
6. **Escribir como en Excel**: en el alta, cliente, proveedor, ejecutivo y
   usuario son combobox con autocompletado (`/cases/suggestions`), y los
   documentos se agregan como filas de una mini-tabla editable (tipo de
   documento + cantidad), con el tiempo establecido visible al elegir el tipo.

## Pantallas

### 1. Bandeja (`/[area]/pqrsf`)

- Encabezado: título, contadores (`summary`), botón primario «Nuevo caso»,
  botón «Exportar».
- Barra de filtros: búsqueda `q` (debounce 300 ms), estado (multi),
  tipo, responsable (solo gestores), dependencia, sucursal, rango de fechas de
  recepción. Chips de filtros activos con «Limpiar».
- Tabla densa (`DESIGN.md` §7.3) con orden por columna usando `sort`,
  paginación por `page`/`size` (25 por defecto; 50 y 100 disponibles) y total.
- Fila → detalle. Casos importados sin responsable muestran «Responsable
  (histórico): nombre» tomado de `legacy.responsable`, en texto secundario.
- Colaborador sin rol de gestor: la vista por defecto es «Mis casos»; no ve el
  filtro de responsable ni la vista «Sin asignar».

### 2. Detalle (`/[area]/pqrsf/[caseId]`)

Al montar, llamar `POST /pqrsf/cases/{id}/read` una vez (la API decide si
aplica). Diseño en dos columnas en escritorio, una en móvil:

- **Cabecera**: consecutivo grande (copiable), tipo, badge de estado,
  vencimiento, origen. Acciones según `permissions`:
  - `can_work`: «Cambiar estado» (menú con los estados activos; si el destino
    es final y hay pendientes, mostrar el mensaje de la API), «Editar».
  - `can_assign`: «Asignar» (combobox con `/pqrsf/assignees?q=`; opción
    «Sin asignar»; nota opcional).
- **Datos**: cliente, NIT, ejecutivo, usuario solicitante, proveedor,
  dependencia, sucursal, fecha de recepción. Edición en línea o en panel con
  `PATCH` (solo campos cambiados).
- **Documentos solicitados**: tabla con tipo, cantidad, establecido, vence,
  fecha de respuesta, días de respuesta, diferencia (+/− con signo y texto,
  no solo color) y causal. Acción por fila «Registrar respuesta»: selector de
  fecha (por defecto hoy); si la API responde 422 por retraso, abrir en el
  mismo diálogo los campos de causal y detalle con el mensaje recibido.
  Agregar/eliminar documento si `can_work` y el caso no está cerrado.
- **Solicitud original** (si `request` existe): datos del formulario público,
  motivo, descripción y miniaturas de adjuntos descargables con
  `/pqrsf/cases/{id}/attachments/{attachment_id}/download`.
- **Línea de tiempo**: eventos en orden cronológico con autor y fecha
  relativa. Textos por `event_type`: `created` «Creó el caso», `assigned`
  «Asignó a X», `status_changed` «Cambió de A a B» (resolver ids con el
  catálogo), `read` «Leyó el caso», `comment` (texto completo),
  `item_added`/`item_updated`/`item_removed` con `data.document`. Autor nulo =
  «Sistema». Caja para comentar al final (cualquiera que ve el caso).
- Caso cerrado: aviso «Finalizado el …» y controles de edición ocultos; solo
  queda «Reabrir» (exige nota).

### 3. Nuevo caso (modal ancho o página `/[area]/pqrsf/nuevo`)

- Tipo de solicitud (por defecto Petición), fecha de recepción (por defecto
  hoy, no futura), sucursal, dependencia, cliente + NIT, ejecutivo, usuario
  solicitante, proveedor, asunto, descripción, responsable (solo gestores).
- Mini-tabla de documentos: al menos una fila vacía lista para escribir;
  Enter agrega otra fila. Mostrar el tiempo establecido y la fecha límite
  calculada en cliente como referencia (la API es la autoridad).
- Al crear: toast con el consecutivo y navegación al detalle.

### 4. Configuración (solo `is_manager`)

Pestaña o subruta `/[area]/pqrsf/configuracion` con tres tablas editables:
estados (nombre, orden, inicial, final, activo), dependencias y tipos de
documento (nombre, días hábiles, activo). Tipos de solicitud: prefijo, «con
seguimiento» y días por defecto. Sin borrar: solo desactivar.

## Estados y color

- Los estados sembrados se mapean por `code` a las variantes semánticas del
  badge (`DESIGN.md` §7.5): `recibida` neutro, `leida` informativo,
  `en_curso` alerta, `finalizada` éxito. Estados nuevos creados por
  gestores usan la variante neutra con su nombre. El `color` hex que devuelve
  la API **no** se aplica inline (regla de tokens); queda para reportes.
- Vencido: variante de error + icono + texto. Vence hoy: alerta + texto.

## Cliente HTTP y datos

- Todas las llamadas con `credentials: "include"` (sesión Entra).
- Tipos TypeScript generados o escritos a mano desde el contrato; un módulo
  `features/pqrsf/api.ts` con funciones por endpoint y claves de caché por
  recurso (catálogo, lista con filtros, resumen, detalle).
- Catálogo (`/pqrsf/tracking/catalogs`) se carga una vez y se cachea; trae
  `is_manager` para decidir qué mostrar.
- Tras cualquier mutación del detalle, reemplazar el caso con la respuesta
  (todas las mutaciones devuelven el caso completo) e invalidar lista y
  resumen.
- Errores: `403` → pantalla «sin permisos» con explicación; `404` → caso no
  encontrado; `409`/`422` → mensaje de la API junto al control que lo causó.

## Los cuatro estados en cada vista

Bandeja, detalle, alta y configuración deben tener: cargando (skeleton de
tabla con la misma altura de fila), vacío (texto que diga por qué y qué
hacer, por ejemplo «No tienes casos asignados» o «Ningún caso coincide con los
filtros» + limpiar), error con «Reintentar» y sin permisos.

## Accesibilidad y responsive

- Tabla con encabezados reales y orden anunciado (`aria-sort`); filas
  navegables con teclado; acciones con etiqueta accesible.
- Diálogos con foco atrapado y retorno de foco; errores asociados con
  `aria-describedby`.
- En móvil la tabla pasa a tarjetas con consecutivo, cliente, estado y vence;
  sin scroll horizontal de página.

## Verificación

- `pnpm lint` y `pnpm build`.
- Con la API local (`AUTH_DEV_BYPASS=true`) y el Excel importado: filtrar
  vencidos, buscar un cliente, abrir un caso importado, crear un caso, asignar,
  registrar una respuesta tardía (debe pedir causal), cerrar, reabrir y
  exportar.
- Light/dark, teclado y ancho móvil.
- Commits con Conventional Commits en inglés, scope de la feature.
