"""Links between platform users and SIESA salespeople, and the sales scope they give.

A user is linked to one or more salesperson codes (``consumo.vendedores.codigo``) or
supervisor codes (``consumo.vendedores.cod_supervisor``, a separate catalog). A link comes
from the corporate e-mail (``auto``) or from an administrator (``manual``); an unlinked
code is kept as ``excluded`` so the e-mail never links it again on its own.

The scope says which customers, quotations and sales a person sees and works on:

- ``all``: administrators, moderators, BI and the leader or responsible of Comercial.
- ``team``: a linked supervisor; their sellers plus their own codes.
- ``mine``: a linked salesperson; only their codes.
- ``none``: anyone else, until an administrator links them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

SELLER = "seller"
SUPERVISOR = "supervisor"
KINDS = (SELLER, SUPERVISOR)

AUTO = "auto"
MANUAL = "manual"
EXCLUDED = "excluded"
SOURCES = (AUTO, MANUAL, EXCLUDED)

ALL = "all"
TEAM = "team"
MINE = "mine"
NONE = "none"


@dataclass(frozen=True)
class SalespersonLink:
    """One code of the SIESA sales force and who holds it (``None`` when excluded)."""

    id: UUID
    kind: str
    code: str
    user_id: UUID | None
    source: str
    created_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RosterEntry:
    """A salesperson or a supervisor as the warehouse lists them."""

    kind: str
    code: str
    name: str | None
    email: str | None = None
    # A salesperson's coordinator: the code of the commercial master that holds their team.
    supervisor_code: str | None = None
    is_active: bool = True
    # The team's name (``vendedores.equipo``, ``equipos.nombre``).
    team: str | None = None


@dataclass(frozen=True)
class SalesScope:
    """What a person sees of the sales force's customers.

    ``codes`` are the salesperson codes whose customers the person works on; ``None``
    means every customer (scope ``all``).
    """

    scope: str
    seller_codes: tuple[str, ...] = ()
    supervisor_codes: tuple[str, ...] = ()
    codes: tuple[str, ...] | None = field(default=())

    @property
    def is_all(self) -> bool:
        """Whether the person sees every customer."""

        return self.codes is None

    def allows(self, salesperson_code: str | None) -> bool:
        """Whether a customer of this salesperson is in the person's scope."""

        if self.codes is None:
            return True
        return salesperson_code is not None and salesperson_code.strip() in self.codes


EVERYTHING = SalesScope(ALL, codes=None)
NOTHING = SalesScope(NONE)


def clean_code(value: str) -> str:
    """A SIESA code as the warehouse stores it, without surrounding spaces."""

    return value.strip()
