# Scripts de desarrollo

Los scripts se ejecutan desde cualquier directorio porque resuelven la raíz
del repositorio antes de invocar Poetry.

## Backend

```bash
./scripts/run-backend.sh
MIGRATE=true ./scripts/run-backend.sh --no-reload
PORT=8010 ./scripts/run-backend.sh
```

El script usa `app.main:app`, recarga por defecto en desarrollo y permite pasar
opciones adicionales de Uvicorn al final del comando.

## Pruebas

```bash
./scripts/run-test.sh
./scripts/run-test.sh ventas
./scripts/run-test.sh ventas agregar_compra
./scripts/run-test.sh module ventas agregar_compra
./scripts/run-test.sh test tests/test_app.py::test_liveness_and_request_id
# Alias plural compatible con el uso habitual del equipo
./scripts/run-tests.sh ventas agregar_compra
```

El primer argumento sin una palabra reservada se interpreta como módulo. El
segundo argumento se convierte en un filtro `pytest -k`; esto permite ejecutar
una prueba concreta dentro de un módulo sin acoplar el script a nombres de
archivos. Los scopes `all`, `module` y `test` hacen explícita la intención en
CI o en scripts superiores.

Para promover un usuario de Entra ya registrado al rol inicial de administrador:

```bash
poetry run python scripts/promote-user.py --tenant-id <tenant> --entra-object-id <oid>
```

Para poblar una base local de desarrollo con el administrador
`analista.ti@jdh.com.co` y cuatro usuarios mock activos y deterministas:

```bash
poetry run alembic upgrade head
poetry run python scripts/seed-dev-users.py
```

El seed solo acepta `ENVIRONMENT=dev` y puede ejecutarse varias veces sin
duplicar usuarios. Usa `AUTH_DEV_USER_OBJECT_ID` para la identidad del analista
y el tenant configurado (o `development`) para las identidades mock.

Para migrar el Excel P.13.F.01 «Seguimiento Peticiones» (exportado a CSV) a
casos de PQRSF. Sin `--apply` solo muestra el reporte; con `--apply` es
repetible y omite las filas ya cargadas:

```bash
poetry run python scripts/import-pqrsf-tracking.py P.13.F.csv --year 2026 \
  --site "Copacabana=medellin" --site "Funza=bogota" \
  --assignee "Nombre Responsable=correo@jdh.com.co" --apply
```

Detalles y decisiones de la importación en
[`docs/pqrsf-seguimiento.md`](../docs/pqrsf-seguimiento.md#migración-del-excel).

## Apoyos de proveedores

Sincroniza los maestros del datawarehouse y migra el libro de apoyos. Ambos son
simulaciones salvo `--apply` y se pueden repetir. Detalle en
[`docs/apoyos-proveedores.md`](../docs/apoyos-proveedores.md).

```bash
poetry run python scripts/sync-dwh-masters.py --apply
poetry run python scripts/import-supplier-supports.py "1. MAESTRO APOYOS PROVEEDORES.xlsx" \
    --report inconsistencias.csv --apply
```
