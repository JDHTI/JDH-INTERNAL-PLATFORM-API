"""The rep master upload: forwarded to dwh-platform, its answer back and the history."""

from __future__ import annotations

import io

import httpx
import pytest
from openpyxl import load_workbook

from app.core.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    PayloadTooLargeError,
)
from app.modules.commercial_budget.application.vendor_master import (
    FAILED,
    LOADED,
    REJECTED,
    VendorMasterService,
    VendorUpload,
)
from app.modules.commercial_budget.infrastructure.dwh_platform import UPLOAD_PATH, DwhPlatformClient
from app.modules.commercial_budget.infrastructure.vendor_template import (
    SHEETS,
    vendor_master_template,
)
from tests.modules.commercial_budget.fakes import Policy
from tests.modules.fakes import principal

MANAGER = principal("analista.bi@jdh.com.co")
READER = principal("lector@jdh.com.co")
ANSWER = {
    "regionales_asignadas": 3,
    "unidades_negocio_procesadas": 2,
    "equipos_procesados": 5,
    "supervisores_procesados": 4,
    "vendedores_procesados": 112,
    "filas_rechazadas": [
        {"hoja": "COMERCIALES JDH", "fila": 7, "identificador": "123", "motivos": ["x"]}
    ],
    "advertencias": ["REGIONAL vacía en la fila 9: queda en VARIOS"],
    "transformacion": "programada",
}


class History:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def add(self, **fields) -> VendorUpload:
        self.items.append(fields)
        return VendorUpload(id=None, **fields)  # type: ignore[arg-type]

    async def list(self, limit: int) -> list[VendorUpload]:
        return []


def _client(handler, url: str = "http://dwh-platform:8000/") -> DwhPlatformClient:
    return DwhPlatformClient(url, "clave", 5, transport=httpx.MockTransport(handler))


def _service(client, history) -> VendorMasterService:
    return VendorMasterService(
        client,
        history,
        Policy(managers={MANAGER.email}, readers={READER.email}),
        vendor_master_template,
        max_upload_bytes=1024,
    )


async def test_the_workbook_goes_to_dwh_platform_with_the_key() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-API-Key")
        seen["body"] = request.content
        return httpx.Response(200, json=ANSWER)

    history = History()
    answer = await _service(_client(handler), history).upload(MANAGER, "Maestro.xlsx", b"xlsx")

    assert answer == ANSWER
    assert seen["url"] == f"http://dwh-platform:8000{UPLOAD_PATH}" and seen["key"] == "clave"
    assert b'name="archivo"; filename="Maestro.xlsx"' in seen["body"]
    record = history.items[0]
    assert record["status"] == LOADED and record["rejected_rows"] == 1 and record["warnings"] == 1
    assert record["summary"]["vendedores_procesados"] == 112
    assert "filas_rechazadas" not in record["summary"]


async def test_a_rejected_file_answers_its_reason() -> None:
    history = History()
    service = _service(
        _client(lambda _: httpx.Response(400, json={"detail": "Falta la hoja Directores"})),
        history,
    )

    with pytest.raises(InvalidInputError, match="Directores"):
        await service.upload(MANAGER, "m.xlsx", b"x")
    assert history.items[0]["status"] == REJECTED


async def test_an_unreachable_or_failing_platform_is_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    history = History()
    with pytest.raises(DependencyUnavailableError):
        await _service(_client(down), history).upload(MANAGER, "m.xlsx", b"x")
    with pytest.raises(DependencyUnavailableError):
        await _service(_client(lambda _: httpx.Response(500, text="boom")), history).upload(
            MANAGER, "m.xlsx", b"x"
        )
    assert [item["status"] for item in history.items] == [FAILED, FAILED]


async def test_not_configured_is_unavailable_and_status_says_so() -> None:
    service = _service(DwhPlatformClient(""), History())

    assert not (await service.status(MANAGER)).configured
    with pytest.raises(DependencyUnavailableError, match="DWH_PLATFORM_URL"):
        await service.upload(MANAGER, "m.xlsx", b"x")


async def test_who_and_what_may_be_uploaded() -> None:
    service = _service(_client(lambda _: httpx.Response(200, json={})), History())

    with pytest.raises(ForbiddenError):
        await service.upload(READER, "m.xlsx", b"x")
    with pytest.raises(InvalidInputError, match=".xlsx"):
        await service.upload(MANAGER, "m.csv", b"x")
    with pytest.raises(PayloadTooLargeError):
        await service.upload(MANAGER, "m.xlsx", b"x" * 2048)
    assert (await service.template(READER))[:2] == b"PK"


def test_the_template_keeps_dwh_platforms_sheets_and_headers_in_row_one() -> None:
    workbook = load_workbook(io.BytesIO(vendor_master_template()))

    for name, headers in SHEETS.items():
        assert [cell.value for cell in workbook[name][1]] == headers
    assert workbook["Listas"].sheet_state == "hidden"
    assert workbook["COMERCIALES JDH"]["A2"].number_format == "@"
