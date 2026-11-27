"""The team master of the rep master (2026-11-27): the template offers only its teams, an
upload with another team is refused and the class is always the master's."""

from __future__ import annotations

import io

import httpx
import pytest
from openpyxl import load_workbook

from app.core.errors import ConflictError, InvalidInputError
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


def service(history: History, sent: list | None = None) -> VendorMasterService:
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
