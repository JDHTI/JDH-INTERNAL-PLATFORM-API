"""Number PQRSF cases per site: P26AC0001 instead of P-2026-00001.

Sites get the abbreviation that goes into the consecutive. Each request type,
year and site keeps its own counter; cases without a site share the ``GN``
series. Every existing case is renumbered in order of reception, and the code
and number it had are kept in ``legacy.previous_code`` / ``previous_number`` so
the inbox search still finds it by the old code.
"""

import unicodedata
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_pqrsf_site_consecutives"
down_revision: str | None = "0010_pqrsf_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENERIC_SERIES = "GN"

_ABBREVIATIONS = {
    "aco": "AC",
    "apartado": "AP",
    "barranquilla": "BQ",
    "bogota": "BG",
    "bucaramanga": "BU",
    "cartagena": "CT",
    "jdh-market-apartado": "MA",
    "jdh-market-valledupar": "MV",
    "medellin": "MD",
    "pereira": "PE",
    "valledupar": "VA",
    "yumbo": "YU",
}


def _letters(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(ch for ch in plain.upper() if "A" <= ch <= "Z")


def _derive(name: str, taken: set[str]) -> str:
    """Two letters from the name for a site created outside the seed."""

    letters = _letters(name) or "SD"
    candidates = [letters[0] + other for other in letters[1:]] + [
        letters[:1] + chr(code) for code in range(ord("A"), ord("Z") + 1)
    ]
    for candidate in candidates:
        if len(candidate) == 2 and candidate not in taken and candidate != GENERIC_SERIES:
            return candidate
    raise RuntimeError(f"No se pudo asignar una sigla a la sede {name!r}")


def _sequences_table(with_series: bool) -> None:
    columns = [
        sa.Column(
            "request_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pqrsf_request_types.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
    ]
    key = ["request_type_id", "year"]
    if with_series:
        columns.append(sa.Column("series", sa.String(4), nullable=False))
        key.append("series")
    op.create_table(
        "pqrsf_case_sequences",
        *columns,
        sa.Column("last_value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(*key, name="pk_pqrsf_case_sequences"),
    )


def upgrade() -> None:
    """Add site abbreviations, renumber cases per site and rebuild the counters."""

    connection = op.get_bind()

    op.add_column("pqrsf_sites", sa.Column("abbreviation", sa.String(4)))
    sites = connection.execute(
        sa.text("SELECT id, code, name FROM pqrsf_sites ORDER BY code")
    ).all()
    taken = {value for code, value in _ABBREVIATIONS.items() if code in {s.code for s in sites}}
    for site in sites:
        abbreviation = _ABBREVIATIONS.get(site.code) or _derive(site.name, taken)
        taken.add(abbreviation)
        connection.execute(
            sa.text("UPDATE pqrsf_sites SET abbreviation = :value WHERE id = :id"),
            {"value": abbreviation, "id": site.id},
        )
    op.alter_column("pqrsf_sites", "abbreviation", nullable=False)
    op.create_unique_constraint("pqrsf_sites_abbreviation_key", "pqrsf_sites", ["abbreviation"])

    op.add_column("pqrsf_cases", sa.Column("series", sa.String(4)))
    connection.execute(
        sa.text(
            "UPDATE pqrsf_cases c SET series = COALESCE("
            "(SELECT s.abbreviation FROM pqrsf_sites s WHERE s.id = c.site_id), :generic)"
        ),
        {"generic": GENERIC_SERIES},
    )
    op.alter_column("pqrsf_cases", "series", nullable=False)
    op.drop_constraint("pqrsf_cases_type_year_number_key", "pqrsf_cases", type_="unique")

    cases = connection.execute(
        sa.text(
            "SELECT c.id, c.code, c.number, c.year, c.series, c.request_type_id, t.prefix "
            "FROM pqrsf_cases c JOIN pqrsf_request_types t ON t.id = c.request_type_id "
            "ORDER BY c.request_type_id, c.year, c.series, c.received_on, c.created_at, c.id"
        )
    ).all()
    counters: dict[tuple[UUID, int, str], int] = {}
    updates = []
    for case in cases:
        key = (case.request_type_id, case.year, case.series)
        counters[key] = counters.get(key, 0) + 1
        number = counters[key]
        updates.append(
            {
                "id": case.id,
                "number": number,
                "code": f"{case.prefix}{case.year % 100:02d}{case.series}{number:04d}",
                "previous": {"previous_code": case.code, "previous_number": case.number},
            }
        )
    if updates:
        # The new codes never look like the old ones (no dashes), so the
        # unique code constraint holds row by row.
        connection.execute(
            sa.text(
                "UPDATE pqrsf_cases SET number = :number, code = :code, "
                "legacy = CASE WHEN jsonb_typeof(legacy) = 'object' THEN legacy "
                "ELSE '{}'::jsonb END || CAST(:previous AS jsonb) "
                "WHERE id = :id"
            ).bindparams(sa.bindparam("previous", type_=postgresql.JSONB())),
            updates,
        )
    op.create_unique_constraint(
        "pqrsf_cases_type_year_series_number_key",
        "pqrsf_cases",
        ["request_type_id", "year", "series", "number"],
    )

    op.drop_table("pqrsf_case_sequences")
    _sequences_table(with_series=True)
    if counters:
        op.bulk_insert(
            sa.table(
                "pqrsf_case_sequences",
                sa.column("request_type_id", postgresql.UUID(as_uuid=True)),
                sa.column("year", sa.Integer()),
                sa.column("series", sa.String()),
                sa.column("last_value", sa.Integer()),
            ),
            [
                {"request_type_id": type_id, "year": year, "series": series, "last_value": value}
                for (type_id, year, series), value in counters.items()
            ],
        )


def downgrade() -> None:
    """Restore the previous codes; cases opened since get numbers after them."""

    connection = op.get_bind()
    op.drop_constraint("pqrsf_cases_type_year_series_number_key", "pqrsf_cases", type_="unique")

    cases = connection.execute(
        sa.text(
            "SELECT c.id, c.year, c.request_type_id, t.prefix, "
            "(c.legacy ->> 'previous_code') AS previous_code, "
            "(c.legacy ->> 'previous_number')::int AS previous_number "
            "FROM pqrsf_cases c JOIN pqrsf_request_types t ON t.id = c.request_type_id "
            "ORDER BY c.received_on, c.created_at, c.id"
        )
    ).all()
    counters: dict[tuple[UUID, int], int] = {}
    for case in cases:
        if case.previous_number is not None:
            key = (case.request_type_id, case.year)
            counters[key] = max(counters.get(key, 0), case.previous_number)
    updates = []
    for case in cases:
        if case.previous_number is not None:
            updates.append(
                {"id": case.id, "number": case.previous_number, "code": case.previous_code}
            )
            continue
        key = (case.request_type_id, case.year)
        counters[key] = counters.get(key, 0) + 1
        number = counters[key]
        updates.append(
            {"id": case.id, "number": number, "code": f"{case.prefix}-{case.year}-{number:05d}"}
        )
    if updates:
        connection.execute(
            sa.text(
                "UPDATE pqrsf_cases SET number = :number, code = :code, "
                "legacy = NULLIF(legacy - 'previous_code' - 'previous_number', '{}'::jsonb) "
                "WHERE id = :id"
            ),
            updates,
        )
    op.create_unique_constraint(
        "pqrsf_cases_type_year_number_key", "pqrsf_cases", ["request_type_id", "year", "number"]
    )
    op.drop_column("pqrsf_cases", "series")

    op.drop_table("pqrsf_case_sequences")
    _sequences_table(with_series=False)
    if counters:
        op.bulk_insert(
            sa.table(
                "pqrsf_case_sequences",
                sa.column("request_type_id", postgresql.UUID(as_uuid=True)),
                sa.column("year", sa.Integer()),
                sa.column("last_value", sa.Integer()),
            ),
            [
                {"request_type_id": type_id, "year": year, "last_value": value}
                for (type_id, year), value in counters.items()
            ],
        )

    op.drop_constraint("pqrsf_sites_abbreviation_key", "pqrsf_sites", type_="unique")
    op.drop_column("pqrsf_sites", "abbreviation")
