#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

POETRY_BIN="${POETRY_BIN:-poetry}"

usage() {
    cat <<'EOF'
Usage:
  ./scripts/run-test.sh                         Run the complete test suite
  ./scripts/run-test.sh all [pytest options]    Run the complete test suite
  ./scripts/run-test.sh <module>                Run every test in a module
  ./scripts/run-test.sh <module> <test>         Filter a module by test name
  ./scripts/run-test.sh module <module> [test]  Explicit module scope
  ./scripts/run-test.sh test <node>             Run one test node directly

Examples:
  ./scripts/run-test.sh
  ./scripts/run-test.sh ventas
  ./scripts/run-test.sh ventas agregar_compra
  ./scripts/run-test.sh module ventas agregar_compra
  ./scripts/run-test.sh test tests/test_app.py::test_liveness_and_request_id

Module directories are resolved in this order:
  tests/<module>, tests/modules/<module>, tests/v1_0/modules/<module>
EOF
}

if ! command -v "${POETRY_BIN}" >/dev/null 2>&1; then
    printf 'Error: Poetry was not found. Install Poetry or set POETRY_BIN.\n' >&2
    exit 127
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

find_module_path() {
    local module="$1"
    local candidate
    local candidates=(
        "tests/${module}"
        "tests/modules/${module}"
        "tests/v1_0/modules/${module}"
        "tests/v1_0/${module}"
        "tests/${module}.py"
        "tests/test_${module}.py"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -e "${candidate}" ]]; then
            printf '%s' "${candidate}"
            return 0
        fi
    done

    candidate="$(find tests -type d -iname "${module}" -print -quit 2>/dev/null || true)"
    if [[ -n "${candidate}" ]]; then
        printf '%s' "${candidate}"
        return 0
    fi

    return 1
}

run_all() {
    exec "${POETRY_BIN}" run pytest "$@"
}

run_node() {
    local node="$1"
    shift
    exec "${POETRY_BIN}" run pytest "${node}" "$@"
}

run_module() {
    local module="$1"
    shift
    local test_filter="${1:-}"
    if [[ $# -gt 0 ]]; then
        shift
    fi

    local module_path
    if ! module_path="$(find_module_path "${module}")"; then
        printf 'Error: no tests were found for module "%s".\n' "${module}" >&2
        printf 'Use --help for the supported test layouts.\n' >&2
        exit 2
    fi

    if [[ -n "${test_filter}" ]]; then
        exec "${POETRY_BIN}" run pytest "${module_path}" -k "${test_filter}" "$@"
    fi
    exec "${POETRY_BIN}" run pytest "${module_path}" "$@"
}

scope="${1:-all}"
if [[ $# -gt 0 ]]; then
    shift
fi

case "${scope}" in
    all)
        run_all "$@"
        ;;
    module)
        if [[ $# -lt 1 ]]; then
            printf 'Error: module scope requires a module name.\n' >&2
            usage >&2
            exit 2
        fi
        module="$1"
        shift
        run_module "${module}" "$@"
        ;;
    test)
        if [[ $# -lt 1 ]]; then
            printf 'Error: test scope requires a pytest node.\n' >&2
            usage >&2
            exit 2
        fi
        node="$1"
        shift
        run_node "${node}" "$@"
        ;;
    *)
        if [[ "${scope}" == *.py || "${scope}" == *::* || "${scope}" == tests/* ]]; then
            run_node "${scope}" "$@"
        fi
        run_module "${scope}" "$@"
        ;;
esac
