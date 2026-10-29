# feat(module-supplier-supports): commercial JDH requests and BI approvals

## Resumen

Comercial consulta el maestro y solicita apoyo JDH sin alterar el acuerdo vigente. BI o un administrador aprueba o rechaza; aprobar actualiza únicamente el registro seleccionado y guarda la decisión con actor, fecha y valores anteriores/nuevos en una transacción.

- Solicitudes separadas del maestro, seguimiento propio (`mine=true`) y bandeja BI con filtros.
- Porcentaje JDH para los métodos existentes; costo pactado JDH independiente para `costo_neto`, conservando el pacto del proveedor.
- Cancelación determinista exclusiva de JDH, sin borrar el acuerdo ni el historial.
- Control de versiones y bloqueos para decisiones concurrentes; 409 exige revisión del valor vigente.
- Auditoría también de cambios administrativos, protegida contra edición/borrado en PostgreSQL.
- Frontend hermano: `JDH-INTERNAL-PLATFORM`, rama `feat/supplier-supports-requests`, commit `177184a`.

## Notas de despliegue

- Migración `0036_support_requests`, tras `0035_full_margin_reason`: `alembic upgrade head`.
- Si el entorno fija `SUPPLIER_SUPPORTS_MANAGER_AREA_SLUG=comercial`, cambiarlo a `bi`. Los defaults de aplicación y Compose ya usan BI. Comercial conserva consulta y solicitudes.
- Línea base de auditoría para registros existentes, identificada como migración; no inventa solicitudes históricas.
- El dinero del proveedor permanece separado del aporte JDH. El validador sigue usando exclusivamente el valor del proveedor; los archivos previos conservan su fotografía.
- Contrato: [solicitudes-apoyos.md](solicitudes-apoyos.md). Rutas nuevas incluidas en documentación de límites de tráfico.
- `alembic check` señala siete índices preexistentes ajenos a este cambio; no detecta diferencias en las tablas nuevas.

## Pruebas

- [x] Suite completa: 974 pruebas aprobadas, incluidas seis de PostgreSQL.
- [x] Ruff, comprobación de formato y `git diff --check`.
- [x] Migración PostgreSQL: subir, bajar y volver a subir; una sola cabeza.
- [x] HTTP y PostgreSQL: permisos, aislamiento, aprobación/rechazo, cancelación, conflictos, concurrencia, rollback e historial inmutable.
- [x] UI: TypeScript, ESLint, build y nueve pruebas (validado en el worktree hermano).
- [ ] Recorrido visual integrado en navegador, móvil y ambos temas.
