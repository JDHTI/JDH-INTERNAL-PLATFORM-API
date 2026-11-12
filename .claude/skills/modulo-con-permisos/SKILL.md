---
name: modulo-con-permisos
description: Estándar para agregar un módulo nuevo al espacio de trabajo con sus permisos, en la API (JDH-INTERNAL-PLATFORM-API) y en la UI (JDH-INTERNAL-PLATFORM). Úsala siempre que se cree un módulo, se cambie quién lo gestiona o valida, o se reutilice el motor de otro módulo con un permiso propio.
---

# Módulo nuevo con permisos

Un módulo del espacio de trabajo es una **clave del catálogo** (`cotizador`,
`actualizacion-listas`, …). Esa clave enciende el menú y protege las lecturas y las escrituras.
Para que el módulo quede conectado a los permisos hay que pasar por **todos** estos puntos. Si
falta uno, el módulo aparece en el menú y responde 403, o lo contrario.

Contexto: [`docs/permissions.md`](../../../docs/permissions.md) (roles, `read` y `manage`,
módulos por área) y [`docs/module-guideline.md`](../../../docs/module-guideline.md) (estructura).
El ejemplo completo es `actualizacion-listas` (PR de la API y de la UI, 2026-11-12).

## API

1. **Catálogo.** `app/modules/areas/domain/catalog.py`: agrega
   `Module("<clave>", "<Nombre>", ("<área por defecto>", …), "<descripción>")`.
   - La clave va en kebab-case y en español, y es la misma en la UI.
   - Las áreas son donde se enciende por defecto. Un administrador la enciende en otras desde el
     panel, sin desplegar.
   - No necesita migración: un permiso guardado sobre un módulo que no está en el catálogo no da
     nada.
2. **Gate de lectura.** Usa `require_permission("<clave>")`
   (`app/modules/areas/presentation/dependencies.py`).
   - Va en el router, o por endpoint si el router expone un `GET /permissions`. Ese endpoint queda
     abierto y responde `false`, no 403.
   - Si el módulo comparte endpoints con otro, el gate acepta las dos claves:
     `require_permission("cotizador", "actualizacion-listas")`. Así quedó `/quotations`.
   - Si sus pantallas usan maestros compartidos (`/commercial/*`), suma la clave al
     `require_permission` de ese router.
3. **Políticas de escritura.** Defínelas en `app/v1_0/v1_containers.py` con
   `PermissionPolicy(areas, área, "<clave>")` y pásalas al servicio. No escribas una pertenencia
   a un área a mano.
   - `area_slug=None`: gestiona quien tenga `manage` en cualquier área donde el módulo esté
     encendido.
   - `area_slug="bi"` (desde un setting, como `QUOTATIONS_APPROVER_AREA_SLUG`): solo los roles de
     esa área. Así se define quién valida.
   - El servicio pregunta `is_manager` (manage) y `can_read` (read). Los administradores siempre
     pasan y el moderador solo lee.
4. **Reglas por registro.** Van en el servicio: lo propio contra lo de todos, o listas
   restringidas a quien valida (`LIST_UPDATES_RESTRICTED_LISTS`). Toda regla configurable va en
   `app/core/config.py` y en `.env.example`.
5. **Motor compartido.** Si el módulo reutiliza el motor de otro, distingue los registros con una
   columna (`kind`) y elige la política por tipo en cada operación: leer el registro primero y
   luego revisar el permiso de su tipo. Filtra los listados por tipo, de modo que el permiso de
   un módulo nunca abra los registros del otro.
6. **Límite de tráfico.** Corre `poetry run python -m app.core.rate_policy --write` para cada
   endpoint nuevo; el test de la política falla si falta.
7. **Pruebas.** Cubre lo siguiente:
   - quien solo tiene la clave nueva entra al módulo y no al otro;
   - lo restringido responde 403;
   - quien valida sí pasa.

   En las pruebas, los gates de lectura pasan por defecto (`tests/conftest.py`). Un módulo que
   prueba los gates declara `REAL_PERMISSION_GATES = True`.
8. **Documentación.** Actualiza tres cosas:
   - la fila del catálogo en `docs/permissions.md` (qué da `read` y qué da `manage`);
   - el documento del módulo en `docs/`;
   - la línea del módulo en `README.md`.

## UI

1. **Plantilla.** `src/shared/config/areas.config.ts`: agrega en `MODULE_TEMPLATES`
   `{ key, name, category, icon, available: true, permission: "<clave>" }`.
   - `key` es el segmento de la ruta, salvo que se defina `segment`.
   - El menú lo muestra solo si la clave está en `my_modules` del área.
2. **Páginas.** Van en `src/app/(app)/(workspace)/[area]/<segmento>/…/page.tsx`. Cada una llama a
   `await requireModule(area, "<segmento>")`, que da 404 a una ruta sin módulo y redirige si el
   módulo está apagado o sin permiso.
3. **Permisos dentro del módulo.** Léelos del `GET …/permissions` del API (`can_manage`,
   `can_validate`) y no los deduzcas del área. Con un gate en el cliente (como `QuotationsGate`)
   se evita una pantalla llena de 403.
4. **Motor compartido.** Reutiliza los componentes con un contexto de tipo
   (`QuotationKindProvider` en `src/features/quotations/lib/kind.tsx`). Las llaves de React Query
   llevan el tipo (`["quotations", "permissions", kind]`), para que los permisos de un módulo no
   se mezclen con los del otro.
5. **Documentación.** Agrega la fila en la tabla «Módulo de la UI → clave del API» de
   `docs/workspace-access.md` y el documento del módulo.

## Despliegue y acceso

- **Después de desplegar:** un administrador revisa en `/ti/administracion/modulos` que el módulo
  esté encendido donde corresponde.
- **Permisos de los roles:** en `/ti/administracion/permisos` le da a cada rol `read` o `manage`
  sobre la clave nueva. El líder y el responsable del área lo tienen sin hacer nada.
- **Áreas por defecto:** un módulo encendido por defecto en un área no le da permiso a los roles
  que no tienen `*`, así que hay que concederlo rol por rol.
