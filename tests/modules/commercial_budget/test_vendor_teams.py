"""The team master of the rep master (2026-11-27): the template offers only its teams, an
upload with another team is refused and the class is always the master's."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager

import httpx
import pytest
from openpyxl import load_workbook

from app.core.errors import ConflictError, DependencyUnavailableError, InvalidInputError
from app.modules.commercial_budget.application.vendor_master import VendorMasterService
from app.modules.commercial_budget.domain.vendor_master import (
    DWH,
    PLATFORM,
    CurrentCoordinator,
    CurrentRep,
    TeamOverride,
    allowed_teams,
    team_catalog,
)
from app.modules.commercial_budget.infrastructure.dwh_platform import DwhPlatformClient
from app.modules.commercial_budget.infrastructure.externo_teams import ORIGIN, ExternoTeamWriter
from app.modules.commercial_budget.infrastructure.vendor_file import (
    read_vendor_master,
    standardize_classes,
)
from app.modules.commercial_budget.infrastructure.vendor_state import (
    TemplateData,
    TemplateRep,
    TemplateSupervisor,
    WarehouseMaster,
)
from app.modules.commercial_budget.infrastructure.vendor_template import vendor_master_template
from app.modules.dwh_publish.domain.ports import DwhRejected, DwhUnavailable
from tests.modules.commercial_budget.fakes import Policy
from tests.modules.commercial_budget.test_vendor_master import History
from tests.modules.commercial_budget.test_vendor_preview import workbook
from tests.modules.fakes import principal

MANAGER = principal("analista.bi@jdh.com.co")
WAREHOUSE_TEAMS = {
    "JUNIOR MEDELLIN": ("JUNIOR", 6),
    "CALL CENTER MEDELLIN": ("CALL CENTER", 4),
    "JUNIOR/CALL MEDELLIN": ("CALL CENTER", 11),
    "ORIENTE": ("ORIENTE", 9),
}
REPS = [
    CurrentRep("0101", "ANA", ("1001",), "JUNIOR/CALL MEDELLIN", "CALL CENTER", "ANTIOQUIA",
               "FOOD SERVICE"),
    CurrentRep("0104", "TOM", ("1004",), "ORIENTE", "ORIENTE", "ORIENTE", "MASIVO"),
]  # fmt: skip


class State:
    async def current(self):
        return WarehouseMaster(
            REPS,
            [CurrentCoordinator("900", "PEREZ COORD", "JUNIOR/CALL MEDELLIN")],
            {},
            {"ANTIOQUIA", "ORIENTE"},
            WAREHOUSE_TEAMS,
        )

    async def teams(self):
        return WAREHOUSE_TEAMS

    async def template(self):
        return TemplateData(
            [TemplateRep("1001", "Ana", "JUNIOR MEDELLIN", "ANTIOQUIA", "FOOD SERVICE",
                         "ana@jdh.co", "300", "21031")],
            [TemplateSupervisor("900", "Perez Coord", "JUNIOR MEDELLIN", "ANTIOQUIA", None, None,
                                True, False),
             TemplateSupervisor("700", "Gomez Director", None, None, None, None, False, True)],
        )  # fmt: skip


class Writer:
    """``externo.equipo`` in memory."""

    def __init__(self, configured: bool = True, down: bool = False) -> None:
        self.configured = configured
        self.down = down
        self.rows = {name: team_class for name, (team_class, _) in WAREHOUSE_TEAMS.items()}
        self.writes: list[tuple] = []

    def _check(self) -> None:
        if self.down:
            raise DependencyUnavailableError("No se pudo escribir en el maestro de equipos")

    async def teams(self):
        self._check()
        return dict(self.rows)

    async def create(self, name, team_class):
        self._check()
        if name in self.rows:
            raise ConflictError(name)
        self.rows[name] = team_class
        self.writes.append(("create", name, team_class))

    async def set_class(self, name, team_class):
        self._check()
        if name not in self.rows:
            return False
        self.rows[name] = team_class
        self.writes.append(("class", name, team_class))
        return True


def service(
    history: History, sent: list | None = None, writer: Writer | None = None
) -> VendorMasterService:
    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request.content)
        return httpx.Response(200, json={"vendedores_procesados": 1})

    return VendorMasterService(
        DwhPlatformClient("http://dwh", "k", 5, transport=httpx.MockTransport(handler)),
        history,
        Policy(managers={MANAGER.email}),
        vendor_master_template,
        reader=read_vendor_master,
        state=State(),
        standardizer=standardize_classes,
        team_writer=writer,
    )


def test_the_catalog_joins_the_warehouse_and_the_platform() -> None:
    catalog = team_catalog(
        WAREHOUSE_TEAMS,
        [
            TeamOverride("JUNIOR/CALL MEDELLIN", None, False),  # hidden
            TeamOverride("RETAIL BOGOTA", "RETAIL", True),  # created here
            TeamOverride("ORIENTE", "REGIONAL", True),  # its class changes with the next load
        ],
    )

    by_name = {item.name: item for item in catalog}
    assert by_name["RETAIL BOGOTA"].source == PLATFORM and by_name["ORIENTE"].source == DWH
    assert by_name["ORIENTE"].team_class == "REGIONAL" and by_name["ORIENTE"].reps == 9
    allowed = allowed_teams(catalog)
    assert "JUNIOR/CALL MEDELLIN" not in allowed and allowed["RETAIL BOGOTA"] == "RETAIL"


async def test_the_template_offers_only_the_masters_teams_and_comes_filled() -> None:
    content = await service(History()).template(MANAGER)

    book = load_workbook(io.BytesIO(content))
    reps = book["COMERCIALES JDH"]
    header = [cell.value for cell in reps[1]]
    first = {name: reps.cell(2, index + 1).value for index, name in enumerate(header)}
    assert first["N° DOCUMENTO"] == "1001" and first["EQUIPO"] == "JUNIOR MEDELLIN"
    assert first["COORDINADOR"] == "Perez Coord" and first["C. COSTO"] == "21031"
    assert first["CLASE"].startswith("=IF(")
    rules = {str(rule.sqref): rule for rule in reps.data_validations.dataValidation}
    team = next(rule for ref, rule in rules.items() if ref.startswith("C2"))
    assert team.errorStyle == "stop" and team.formula1 == "=Listas!$C$1:$C$4"
    lists = book["Listas"]
    assert [lists[f"C{row}"].value for row in range(1, 5)] == sorted(WAREHOUSE_TEAMS)
    assert book["Coordinadores"]["A2"].value == "900"
    assert book["Directores"]["B2"].value == "Gomez Director"
    empty = load_workbook(io.BytesIO(await service(History()).template(MANAGER, empty=True)))
    assert empty["COMERCIALES JDH"]["A2"].value is None


async def test_a_team_outside_the_master_is_rejected_and_stops_the_upload() -> None:
    content = workbook(
        [
            ["1001", "Ana", "JUNIOR MEDELLIN", "OTRA", "FOOD SERVICE", "ANTIOQUIA", "", "", ""],
            ["1004", "Tom", "ESPECIALISTAS", "", "MASIVO", "ORIENTE", "", "", ""],
        ],
        coordinators=[["900", "Perez Coord", "ESPECIALISTAS", "ANTIOQUIA"]],
    )
    history, sent = History(), []
    tool = service(history, sent)

    result = await tool.preview(MANAGER, "m.xlsx", content)

    reasons = [(item.sheet, item.row) for item in result.rejected]
    assert reasons == [("COMERCIALES JDH", 3), ("Coordinadores", 2)]
    # The class is the master's (JUNIOR), never the file's ("OTRA").
    change = next(item for item in result.changes if item.field == "team_class")
    assert change.new == "JUNIOR"
    with pytest.raises(InvalidInputError, match="no está en el maestro"):
        await tool.upload(MANAGER, "m.xlsx", content)
    assert sent == []


async def test_the_upload_writes_the_masters_class_in_the_file() -> None:
    content = workbook(
        [["1001", "Ana", "JUNIOR MEDELLIN", "OTRA", "FOOD SERVICE", "ANTIOQUIA", "", "", ""]],
        coordinators=[["900", "Perez Coord", "JUNIOR MEDELLIN", "ANTIOQUIA"]],
    )
    sent: list = []

    await service(History(), sent).upload(MANAGER, "m.xlsx", content)

    body = sent[0]
    start = body.index(b"PK")
    book = load_workbook(io.BytesIO(body[start : body.rindex(b"\r\n--")]))
    assert book["COMERCIALES JDH"]["D2"].value == "JUNIOR"


async def test_a_team_is_created_in_the_platform_once() -> None:
    history = History()
    tool = service(history)

    created = await tool.create_team(MANAGER, "retail bogota", "Retail", "Reorganización")

    assert (created.name, created.team_class, created.source) == (
        "RETAIL BOGOTA",
        "RETAIL",
        PLATFORM,
    )
    assert any(item.name == "RETAIL BOGOTA" for item in await tool.teams(MANAGER))
    with pytest.raises(ConflictError):
        await tool.create_team(MANAGER, "Oriente", "ORIENTE", None)
    hidden = await tool.update_team(MANAGER, "JUNIOR/CALL MEDELLIN", is_active=False)
    assert hidden.is_active is False and hidden.source == DWH
    template = load_workbook(io.BytesIO(await tool.template(MANAGER, empty=True)))
    names = [cell.value for cell in template["Listas"]["C"] if cell.value]
    assert "RETAIL BOGOTA" in names and "JUNIOR/CALL MEDELLIN" not in names


# --- The platform writes externo.equipo (2026-11-27) ---


async def test_with_the_writer_a_new_team_goes_straight_to_the_warehouse() -> None:
    history, writer = History(), Writer()
    tool = service(history, writer=writer)

    created = await tool.create_team(MANAGER, "retail bogota", "Retail", None)

    assert writer.writes == [("create", "RETAIL BOGOTA", "RETAIL")]
    assert (created.source, created.team_class, created.reps) == (DWH, "RETAIL", 0)
    # Nothing kept in the platform: the warehouse has it (no note was given).
    assert await history.team_overrides() == []
    listed = {item.name: item for item in await tool.teams(MANAGER)}
    assert listed["RETAIL BOGOTA"].source == DWH
    with pytest.raises(ConflictError):
        await tool.create_team(MANAGER, "Retail Bogota", "RETAIL", None)
    status = await tool.status(MANAGER)
    assert status.writes_teams is True


async def test_with_the_writer_the_note_stays_in_the_platform_and_the_class_does_not() -> None:
    history, writer = History(), Writer()

    await service(history, writer=writer).create_team(MANAGER, "Retail Bogota", "RETAIL", "Nuevo")

    [stored] = await history.team_overrides()
    assert (stored.name, stored.team_class, stored.note) == ("RETAIL BOGOTA", None, "Nuevo")


async def test_the_catalog_reads_externo_with_the_reps_of_consumo() -> None:
    writer = Writer()
    # dbt has not run yet: externo already has the new team and the new class.
    writer.rows["RETAIL BOGOTA"] = "RETAIL"
    writer.rows["ORIENTE"] = "REGIONAL"
    del writer.rows["JUNIOR/CALL MEDELLIN"]

    listed = {item.name: item for item in await service(History(), writer=writer).teams(MANAGER)}

    assert listed["ORIENTE"].team_class == "REGIONAL" and listed["ORIENTE"].reps == 9
    assert listed["RETAIL BOGOTA"].reps == 0 and listed["RETAIL BOGOTA"].source == DWH
    assert "JUNIOR/CALL MEDELLIN" not in listed


async def test_with_the_writer_a_class_change_is_written_and_hiding_is_not() -> None:
    history, writer = History(), Writer()
    tool = service(history, writer=writer)

    changed = await tool.update_team(MANAGER, "Oriente", team_class="regional")
    hidden = await tool.update_team(MANAGER, "JUNIOR/CALL MEDELLIN", is_active=False)

    assert writer.writes == [("class", "ORIENTE", "REGIONAL")]
    assert changed.team_class == "REGIONAL" and changed.source == DWH
    assert writer.rows["JUNIOR/CALL MEDELLIN"] == "CALL CENTER"  # never deleted
    assert hidden.is_active is False
    stored = {item.name: item for item in await history.team_overrides()}
    assert stored["ORIENTE"].team_class is None
    assert stored["JUNIOR/CALL MEDELLIN"].team_class is None
    # The same class again writes nothing.
    await tool.update_team(MANAGER, "Oriente", team_class="REGIONAL")
    assert len(writer.writes) == 1


async def test_a_class_kept_in_the_platform_is_written_on_the_next_change() -> None:
    history, writer = History(), Writer()
    await history.save_team(
        "ORIENTE", team_class="REGIONAL", is_active=True, note=None, actor="x", now=None
    )

    await service(history, writer=writer).update_team(MANAGER, "Oriente", note="Revisado")

    assert writer.writes == [("class", "ORIENTE", "REGIONAL")]
    [stored] = await history.team_overrides()
    assert stored.team_class is None and stored.note == "Revisado"


async def test_an_unset_writer_keeps_the_upload_flow() -> None:
    history, writer = History(), Writer(configured=False)
    tool = service(history, writer=writer)

    created = await tool.create_team(MANAGER, "Retail Bogota", "RETAIL", None)

    assert created.source == PLATFORM and writer.writes == []
    assert (await tool.status(MANAGER)).writes_teams is False


async def test_a_warehouse_that_fails_saves_nothing() -> None:
    history, writer = History(), Writer()
    tool = service(history, writer=writer)
    writer.down = True

    with pytest.raises(DependencyUnavailableError):
        await tool.create_team(MANAGER, "Retail Bogota", "RETAIL", "Nuevo")

    assert await history.team_overrides() == []


async def test_the_preview_uses_the_teams_of_externo() -> None:
    writer = Writer()
    writer.rows["RETAIL BOGOTA"] = "RETAIL"
    content = workbook(
        [["1004", "Tom", "RETAIL BOGOTA", "", "MASIVO", "ORIENTE", "", "", ""]],
        coordinators=[["900", "Perez Coord", "RETAIL BOGOTA", "ANTIOQUIA"]],
    )

    result = await service(History(), writer=writer).preview(MANAGER, "m.xlsx", content)

    assert not [item for item in result.rejected if "maestro de equipos" in item.reason]


# --- ExternoTeamWriter against a fake connection ---


class Connection:
    def __init__(self, returning=None, status="UPDATE 1", exists=True) -> None:
        self.returning = returning
        self.status = status
        self.exists = exists
        self.calls: list[tuple] = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return [{"nombre": " junior  medellin ", "clase": "junior"}, {"nombre": "", "clase": None}]

    async def fetchval(self, query, *args):
        self.calls.append((query, args))
        if query.startswith("insert"):
            return self.returning
        return True if self.exists else None

    async def execute(self, query, *args):
        self.calls.append((query, args))
        return self.status


class Pool:
    """``WarehouseWriterConnection`` with one connection and no network."""

    def __init__(self, connection: Connection | None = None, error: Exception | None = None):
        self.connection = connection
        self.error = error
        self.configured = True

    @asynccontextmanager
    async def transaction(self):
        if self.error is not None:
            raise self.error
        yield self.connection


async def test_the_writer_inserts_with_the_platforms_origin_and_company() -> None:
    connection = Connection(returning="uuid")
    writer = ExternoTeamWriter(Pool(connection), company_id=1)

    await writer.create("RETAIL BOGOTA", "RETAIL")

    query, args = connection.calls[0]
    assert "on conflict (nombre) do nothing" in query and "delete" not in query
    assert args == ("RETAIL BOGOTA", "RETAIL", 1, ORIGIN)
    with pytest.raises(ConflictError):
        await ExternoTeamWriter(Pool(Connection(returning=None))).create("ORIENTE", "ORIENTE")


async def test_the_writer_reads_and_updates_the_class() -> None:
    connection = Connection()
    writer = ExternoTeamWriter(Pool(connection))

    assert await writer.teams() == {"JUNIOR MEDELLIN": "JUNIOR"}
    assert await writer.set_class("ORIENTE", "REGIONAL") is True
    assert await ExternoTeamWriter(Pool(Connection(status="UPDATE 0"))).set_class("X", "Y") is True
    gone = Connection(status="UPDATE 0", exists=False)
    assert await ExternoTeamWriter(Pool(gone)).set_class("X", "Y") is False


@pytest.mark.parametrize("error", [DwhUnavailable("down"), DwhRejected("bad")])
async def test_the_writer_turns_warehouse_failures_into_503(error: Exception) -> None:
    with pytest.raises(DependencyUnavailableError, match="maestro de equipos del DWH"):
        await ExternoTeamWriter(Pool(error=error)).teams()


def test_the_writer_refuses_a_bad_schema() -> None:
    with pytest.raises(ValueError):
        ExternoTeamWriter(Pool(), schema="externo; drop")
