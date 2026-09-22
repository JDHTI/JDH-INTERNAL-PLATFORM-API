#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

POETRY_BIN="${POETRY_BIN:-poetry}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-true}"
APP_MODULE="${APP_MODULE:-app.main:app}"
MIGRATE="${MIGRATE:-false}"

usage() {
    cat <<'EOF'
Usage: ./scripts/run-backend.sh [--no-reload|--reload] [uvicorn options]

Environment overrides:
  HOST=0.0.0.0       Bind address (default: 0.0.0.0)
  PORT=8000          Bind port (default: 8000)
  RELOAD=true        Enable reload (default: true)
  MIGRATE=false      Run `alembic upgrade head` before Uvicorn
  APP_MODULE=...     ASGI target (default: app.main:app)

Examples:
  ./scripts/run-backend.sh
  MIGRATE=true ./scripts/run-backend.sh --no-reload
  PORT=8010 ./scripts/run-backend.sh --reload
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

if ! command -v "${POETRY_BIN}" >/dev/null 2>&1; then
    printf 'Error: Poetry was not found. Install Poetry or set POETRY_BIN.\n' >&2
    exit 127
fi

if [[ "${1:-}" == "--no-reload" ]]; then
    RELOAD=false
    shift
elif [[ "${1:-}" == "--reload" ]]; then
    RELOAD=true
    shift
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

case "${RELOAD,,}" in
    true|1|yes|y)
        reload_args=(--reload)
        ;;
    false|0|no|n)
        reload_args=()
        ;;
    *)
        printf 'Error: RELOAD must be true or false.\n' >&2
        exit 2
        ;;
esac

case "${MIGRATE,,}" in
    true|1|yes|y)
        "${POETRY_BIN}" run alembic upgrade head
        ;;
    false|0|no|n)
        ;;
    *)
        printf 'Error: MIGRATE must be true or false.\n' >&2
        exit 2
        ;;
esac

exec "${POETRY_BIN}" run uvicorn "${APP_MODULE}" \
    --host "${HOST}" \
    --port "${PORT}" \
    "${reload_args[@]}" \
    "$@"
