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
