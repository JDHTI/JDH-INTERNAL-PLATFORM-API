# Plan de implementación UI: formulario PQRSF público

Este plan acompaña el contrato de [`docs/pqrsf.md`](pqrsf.md). La UI se
implementa en `internal-platform-adaptiosn` y debe quedar en una rama nueva,
sin mezclar los cambios locales preexistentes del worktree.

## Alcance

Construir únicamente la página pública `/pqrsf`. La página debe estar fuera
del grupo `(app)` y no puede montar `AppLayoutFrame`, sidebar, header de sesión
ni guardas de Entra. Debe conservar la fuente, los tokens semánticos y los dos
temas de la plataforma para que siga perteneciendo al producto sin heredar el
chrome privado.

Quedan fuera de esta entrega el listado, detalle, CRUD de sedes y descarga de
adjuntos para administradores; esos endpoints quedan listos en la API para una
fase posterior.

## Fases

1. **Ruta y boundary público**

   Crear el grupo top-level `(public)` y la página `/pqrsf`. Revisar el layout
   raíz para conservar providers globales estrictamente necesarios, pero no
   importar el layout autenticado. Confirmar que una visita directa a
   `/pqrsf` no redirige a login.

2. **Cliente HTTP público**

   Extender el cliente compartido con una opción explícita para llamadas
   públicas que use `credentials: "omit"`; mantener `credentials: "include"`
   para el área autenticada. Tipar el catálogo, el recibo y los errores sin
   duplicar el cliente ni guardar tokens en el navegador.

3. **Carga del catálogo**

   Consultar `GET /api/v1/public/pqrsf/catalogs` al montar la página. Mostrar
   estados de carga y error con una acción de reintento. Poblar los selects de
   tipo y sede usando los UUID devueltos por la API. Las propiedades
   `allows_reason` y `allows_attachments` controlan la UI dinámica.

4. **Formulario y validación**

   Implementar campos visibles y etiquetados para nombre completo, razón
   social, NIT, dirección, departamento, ciudad, correo, tipo, sede,
   descripción y motivo condicional. Validar obligatorios, formato de correo,
   límites razonables de texto y la regla de que sugerencia/felicitación no
   envían motivo ni archivos.

   Cuando `allows_attachments` sea verdadero, mostrar un input múltiple de
   imágenes con `accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"`.
   Validar en cliente cantidad, tamaño individual/total y extensión/MIME antes
   de construir `FormData`; el servidor sigue siendo la autoridad de seguridad.

5. **Envío, idempotencia y estados**

   Crear un UUID para `Idempotency-Key` al iniciar un intento lógico y enviar
   los campos como `FormData`, agregando cada archivo con la clave `images`.
   No fijar manualmente el header `Content-Type`. Deshabilitar el submit
   mientras se envía, mostrar progreso textual y traducir `422`, `413`, `409`,
   `429` y `503` a mensajes accionables. En `201`, mostrar el número de
   referencia y una acción para iniciar una nueva solicitud.

6. **Accesibilidad y pulido visual**

   Usar labels persistentes, descripción/error asociados con `aria-describedby`,
   `aria-invalid`, foco visible, orden de tabulación natural y un resumen de
   errores que no dependa sólo del color. Mantener el formulario dentro del
   ancho definido por `docs/DESIGN.md`, usar grid responsive sin scroll
   horizontal y verificar light/dark mode, móvil y teclado.

7. **Verificación y entrega**

   Ejecutar las reglas de antislop durante el desarrollo. Validar al menos:

   - `pnpm lint`
   - `pnpm build`
   - prueba manual de `/pqrsf` sin cookies de auth
   - catálogo fallido y reintento
   - cada tipo con sus campos condicionales
   - rechazo local de archivo no permitido y manejo de errores del servidor
   - envío exitoso y recibo visible

   Commitear sólo los archivos de esta funcionalidad con Conventional Commit
   en inglés. No modificar, resetear ni incluir los cambios preexistentes de
   `layout.tsx`, `admin-manager.tsx`, `app-header.tsx` o
   `app-layout-frame.tsx`.

## Criterios de aceptación

- `/pqrsf` funciona con una sesión anónima y no muestra el layout privado.
- El formulario nunca envía cookies de Entra ni guarda tokens.
- Los cinco tipos y las doce sedes provienen del catálogo de la API.
- Motivo y adjuntos aparecen sólo cuando el catálogo los permite.
- El POST usa `multipart/form-data`, `images` repetido e `Idempotency-Key`.
- Los errores son accesibles, comprensibles y no dejan el formulario en un
  estado de envío infinito.
- La rama UI queda limpia respecto a cambios no relacionados y pasa lint/build.
