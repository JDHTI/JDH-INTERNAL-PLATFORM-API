"""Which rate-limit level each endpoint has.

Three levels, each a ``(requests, window)`` budget per client address, set in the
settings (``RATE_LIMIT_<LEVEL>_REQUESTS`` / ``_WINDOW_SECONDS``):

* ``read``: queries (``GET``). The most generous: a screen fires several at once.
* ``relaxed``: changes (``POST``, ``PUT``, ``PATCH``, ``DELETE``).
* ``strict``: what is costly or open to the public. A handful of requests per window.

The level follows the HTTP method unless ``RULES`` says otherwise, so an endpoint added
tomorrow is limited from its first day without anyone remembering to. ``RULES`` is the one
place to read (and change) which endpoints are exceptions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

READ = "read"
RELAXED = "relaxed"
STRICT = "strict"
LEVELS = (READ, RELAXED, STRICT)

_READ_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class Rule:
    """An exception to the default of the method: one endpoint and its level.

    ``level=None`` leaves the endpoint out of the limiter altogether.
    """

    method: str
    # Path under the API prefix, with ``{name}`` for each variable segment.
    path: str
    level: str | None
    why: str


RULES: tuple[Rule, ...] = (
    Rule("GET", "/health/live", None, "Las sondas del orquestador siempre deben recibir respuesta"),
    Rule(
        "GET", "/health/ready", None, "Las sondas del orquestador siempre deben recibir respuesta"
    ),
    Rule(
        "POST",
        "/v1/public/pqrsf/requests",
        STRICT,
        "Abierto a cualquiera: además tamaño, tope global y CAPTCHA (public_guard)",
    ),
    Rule("GET", "/v1/pqrsf/cases/export", STRICT, "Arma una hoja de cálculo de hasta 20.000 filas"),
    Rule("POST", "/v1/auth/me/photo/refresh", STRICT, "Llama a Microsoft Graph en cada solicitud"),
)


def _pattern(path: str) -> str:
    parts = (r"[^/]+" if part.startswith("{") else re.escape(part) for part in path.split("/"))
    return "/".join(parts)


class RatePolicy:
    """Resolve the level of a request from its method and path."""

    def __init__(self, prefix: str, rules: tuple[Rule, ...] = RULES) -> None:
        """Create the policy.

        Args:
            prefix (str): The API prefix the paths of the rules hang from (``/api``).
            rules (tuple[Rule, ...]): The exceptions to the default.
        """

        self.prefix = prefix.rstrip("/")
        self._rules = [
            (rule, re.compile(re.escape(self.prefix) + _pattern(rule.path) + r"/?$"))
            for rule in rules
        ]

    def level_for(self, method: str, path: str) -> str | None:
        """The level of a request, or ``None`` when it is not limited."""

        method = method.upper()
        if method == "OPTIONS":
            return None  # CORS preflight: answered before the application, carries no data
        for rule, pattern in self._rules:
            if rule.method == method and pattern.match(path):
                return rule.level
        return READ if method in _READ_METHODS else RELAXED

    def rule_for(self, method: str, path: str) -> Rule | None:
        """The exception that applies to a request, if any."""

        method = method.upper()
        return next(
            (r for r, p in self._rules if r.method == method and p.match(path)),
            None,
        )


@lru_cache
def get_policy(prefix: str) -> RatePolicy:
    """One policy per API prefix."""

    return RatePolicy(prefix)


def describe(prefix: str, operations: list[tuple[str, str]]) -> str:
    """Markdown table of the level of every endpoint, for ``docs/rate-limiting.md``.

    Args:
        prefix (str): The API prefix.
        operations (list[tuple[str, str]]): ``(method, path)`` of every endpoint, with the
            prefix, as the OpenAPI document lists them.
    """

    policy = get_policy(prefix)
    rows = []
    for method, path in sorted(operations, key=lambda item: (item[1], item[0])):
        level = policy.level_for(method, path)
        rule = policy.rule_for(method, path)
        shown = level if level else "sin límite"
        why = rule.why if rule else ""
        rows.append(f"| `{method}` | `{path.removeprefix(prefix)}` | `{shown}` | {why} |")
    header = "| Método | Ruta | Nivel | Motivo |\n|---|---|---|---|"
    return header + "\n" + "\n".join(rows)


def _table_for_current_api() -> str:
    from app.core.config import settings
    from app.main import create_app

    spec = create_app(settings).openapi()
    operations = [(method.upper(), path) for path, ops in spec["paths"].items() for method in ops]
    return describe(settings.API_PREFIX, operations)


def _main(argv: list[str]) -> None:
    """Print the table of ``docs/rate-limiting.md``, or with ``--write`` update the file."""

    table = _table_for_current_api()
    if "--write" not in argv:
        print(table)
        return
    from pathlib import Path

    start, end = "<!-- niveles:inicio -->", "<!-- niveles:fin -->"
    path = Path(__file__).resolve().parents[2] / "docs" / "rate-limiting.md"
    text = path.read_text(encoding="utf-8")
    head, _, rest = text.partition(start)
    _, _, tail = rest.partition(end)
    path.write_text(f"{head}{start}\n{table}\n{end}{tail}", encoding="utf-8", newline="\n")
    print(f"Actualizado {path}")


if __name__ == "__main__":
    import sys

    _main(sys.argv[1:])


__all__ = [
    "LEVELS",
    "READ",
    "RELAXED",
    "RULES",
    "STRICT",
    "RatePolicy",
    "Rule",
    "describe",
    "get_policy",
]
