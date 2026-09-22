# Estándar de Conventional Commits

Este repositorio usa [Conventional Commits](https://www.conventionalcommits.org/)
para que el historial sea legible, automatizable y fácil de transferir entre
equipos. Cada commit debe representar un propósito único y seguir esta forma:

```text
<type>(<scope>): <descripción imperativa>
```

El `scope` es obligatorio en este proyecto. Cuando el cambio afecta varias
áreas, usa el componente que recibe la decisión principal o divide el trabajo
en commits independientes.

## Tipos permitidos

| Tipo | Uso |
| --- | --- |
| `feat` | Nueva capacidad visible para consumidores o usuarios. |
| `fix` | Corrección de un comportamiento incorrecto. |
| `refactor` | Reestructuración sin cambio funcional intencional. |
| `perf` | Mejora de rendimiento sin cambiar el contrato funcional. |
| `test` | Creación o ajuste de pruebas. |
| `docs` | Documentación, guías o ejemplos. |
| `build` | Dependencias, empaquetado o imagen de construcción. |
| `ci` | Workflows y automatización del pipeline. |
| `chore` | Mantenimiento que no pertenece a las categorías anteriores. |
| `revert` | Reversión explícita de un commit anterior. |

## Scopes del repositorio

Usa uno de estos scopes base, siempre en minúsculas:

`platform`, `architecture`, `api`, `core`, `v1`, `module-<nombre>`, `database`,
`redis`, `s3`, `config`, `logging`, `scripts`, `tests`, `infra`, `ci`, `docs` y
`standards`.

Los módulos de negocio deben usar `module-<nombre>`; por ejemplo,
`module-ventas` o `module-orders`. No inventes abreviaturas para un scope ya
existente. Si un cambio es transversal, `architecture` o `platform` debe ser
preferido sobre un scope ambiguo.

## Reglas del asunto

- Escribe el asunto en imperativo y en una sola línea.
- Mantén el asunto en 72 caracteres o menos cuando sea posible.
- No termines el asunto con punto.
- No mezcles cambios no relacionados en el mismo commit.
- Usa el mismo idioma que el resto del equipo; el repositorio acepta español o
  inglés, pero el commit debe ser claro y consistente.

Ejemplos válidos:

```text
chore(platform): initialize modular FastAPI foundation
refactor(architecture): formalize containers and development scripts
feat(module-ventas): add purchase creation use case
fix(redis): handle connection timeout during readiness checks
docs(standards): document commit and wiring conventions
test(module-orders): cover duplicate purchase validation
```

## Cuerpo, referencias y breaking changes

Usa el cuerpo para explicar el motivo o el impacto cuando el asunto no sea
suficiente. Deja una línea en blanco después del asunto. Las referencias a
issues deben ir en el pie del commit:

```text
feat(module-orders): expose purchase history

Expose the paginated history required by the operations dashboard.

Refs: #42
```

Un cambio incompatible se marca con `!` en el encabezado o con un pie
`BREAKING CHANGE:`. Debe describirse la migración necesaria:

```text
feat(api)!: replace the legacy health response

BREAKING CHANGE: clients must read `checks` from the new response envelope.
```

## Flujo recomendado

1. Revisa `git diff` y separa el cambio por intención.
2. Ejecuta `poetry run ruff check .`, `poetry run ruff format --check .` y
   `poetry run pytest -q`.
3. Crea el commit con `git commit -m "type(scope): description"`.
4. Confirma con `git log -1 --format=%s` que el asunto cumple esta guía.
5. Para corregir el último commit local usa `git commit --amend`; no reescribas
   una rama compartida sin coordinación.

El historial inicial del proyecto sigue este estándar y cada cambio posterior
debe conservarlo. Las guías funcionales y de wiring se documentan por separado
en [`docs/wiring-guideline.md`](wiring-guideline.md) y
[`docs/module-guideline.md`](module-guideline.md).
