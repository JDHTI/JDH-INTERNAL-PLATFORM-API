"""The roster reads coordinators and teams from the commercial master, never from SIESA's
``cod_supervisor`` (2026-11-24)."""

from __future__ import annotations

from app.modules.salespeople.infrastructure.dwh_roster import WarehouseSalesRoster


class Connection:
    """Answers with fixed rows and keeps every query."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args, timeout=None):
        self.queries.append((query, args))
        return self.rows


async def test_a_coordinators_team_comes_from_the_master() -> None:
    connection = Connection([{"code": "0080"}, {"code": "0109"}])

    codes = await WarehouseSalesRoster(connection).team(["8104570"])

    query, args = connection.queries[0]
    assert codes == ["0080", "0109"] and args == (["8104570"],)
    assert '"consumo"."supervisores"' in query and '"consumo"."equipos"' in query
    assert 'v."equipo" = e."nombre"' in query and "cod_supervisor" not in query
    assert "'coordinador y director'" in query


async def test_coordinators_are_listed_from_the_master_with_their_team() -> None:
    connection = Connection(
        [
            {
                "code": "8104570",
                "name": "ROJAS CHAVARRIA JUAN CARLOS",
                "email": "juan.rojas@jdh.com.co",
                "team": "SENIOR MEDELLIN",
            },
            {"code": "1", "name": "SIN EQUIPO", "email": None, "team": None},
        ]
    )

    found = await WarehouseSalesRoster(connection).supervisors()

    assert [(e.code, e.email, e.team, e.is_active) for e in found] == [
        ("8104570", "juan.rojas@jdh.com.co", "SENIOR MEDELLIN", True),
        ("1", None, None, False),
    ]
    assert "cod_supervisor" not in connection.queries[0][0]


async def test_a_coordinator_is_found_by_the_masters_email() -> None:
    connection = Connection([{"code": "8104570"}])

    codes = await WarehouseSalesRoster(connection).supervisors_by_email(" Juan.Rojas@jdh.com.co ")

    query, args = connection.queries[0]
    assert codes == ["8104570"] and args == ("juan.rojas@jdh.com.co",)
    assert 'lower(btrim(s."correo")) = $1' in query


async def test_a_salespersons_coordinator_is_the_one_of_their_team() -> None:
    connection = Connection(
        [
            {
                "codigo": "0080 ",
                "nombre": "ARISMENDY",
                "correo": None,
                "estado": "A",
                "equipo": "SENIOR MEDELLIN",
                "coordinator": "8104570",
            }
        ]
    )

    (seller,) = await WarehouseSalesRoster(connection).sellers()

    assert (seller.code, seller.team, seller.supervisor_code) == (
        "0080",
        "SENIOR MEDELLIN",
        "8104570",
    )
    assert "cod_supervisor" not in connection.queries[0][0]
