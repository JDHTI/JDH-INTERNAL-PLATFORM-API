# Flujo de trabajo con Git

Después de estabilizar el workflow de CI, `master` queda como rama de
integración estable. Las funcionalidades, correcciones y cambios de
configuración se desarrollan en ramas de trabajo y se integran mediante Pull
Request.

## Convención de ramas

Usa nombres en minúsculas, cortos y descriptivos:

```text
feat/<scope>-<descripcion>
fix/<scope>-<descripcion>
refactor/<scope>-<descripcion>
docs/<scope>-<descripcion>
ci/<descripcion>
chore/<scope>-<descripcion>
```

Ejemplos:

```text
feat/module-ventas-agregar-compra
fix/redis-readiness-timeout
docs/standards-docstrings
ci/update-poetry-workflow
```

El nombre de la rama indica la intención y el commit conserva el estándar de
[Conventional Commits](conventional-commits.md). No uses ramas permanentes por
desarrollador ni nombres genéricos como `test` o `changes`.

## Flujo obligatorio

1. Actualiza la rama base y crea una rama de trabajo:

   ```bash
   git switch master
   git pull --ff-only origin master
   git switch -c feat/module-ventas-agregar-compra
   ```

2. Implementa un cambio acotado, agrega pruebas y ejecuta las validaciones
   locales descritas en el README.
3. Crea commits con formato `type(scope): description`. No mezcles cambios
   funcionales, documentación y mantenimiento sin una razón explícita.
4. Publica la rama con upstream y abre el Pull Request contra `master`:

   ```bash
   git push -u origin feat/module-ventas-agregar-compra
   gh pr create --base master --head feat/module-ventas-agregar-compra
   ```

5. Espera el workflow `CI` y atiende cualquier fallo antes de solicitar el
   merge. El Pull Request debe explicar el cambio, su impacto y las pruebas
   ejecutadas.
6. Después del merge, elimina la rama remota y actualiza tu copia local:

   ```bash
   git switch master
   git pull --ff-only origin master
   git branch -d feat/module-ventas-agregar-compra
   git push origin --delete feat/module-ventas-agregar-compra
   ```

No hagas nuevos cambios directamente sobre `master`. Las excepciones deben ser
una acción operativa urgente y quedar documentadas en el Pull Request posterior.

## Qué valida CI

El workflow `.github/workflows/ci.yml` se ejecuta en cada push de rama y en
cada Pull Request hacia `master`. En un entorno limpio valida, en este orden:

- existencia y consistencia de `poetry.lock`;
- instalación reproducible con Poetry 2.4.1 y Python 3.12;
- lint con Ruff;
- formato con Ruff;
- suite de pruebas con Pytest.

Poetry se instala antes de habilitar la caché de `setup-python`, porque esa
caché necesita invocar el ejecutable `poetry` durante su configuración.

## Transferencia y revisión

Para entregar trabajo a otro equipo, comparte el Pull Request y sus commits,
no una copia manual de archivos. El revisor debe poder reproducir el cambio
desde `master`, leer la documentación del módulo y ejecutar la misma suite de
CI. Los contratos de wiring y docstrings se mantienen según
[`wiring-guideline.md`](wiring-guideline.md) y
[`documentation-guideline.md`](documentation-guideline.md).
