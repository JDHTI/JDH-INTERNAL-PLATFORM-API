"""HTTP contract of the supplier supports routes, over the real service and in-memory ports."""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.modules.identity.presentation.principal import get_current_principal
from app.modules.supplier_supports.application.deletions import SupportDeletionService
from app.modules.supplier_supports.application.requests import SupportRequestService
from app.modules.supplier_supports.domain.entities import SupportCost
from tests.modules.supplier_supports.conftest import FakeManagers, person

BASE = "/api/v1/supplier-supports"
NEW = {
    "kind": "tactico_cliente",
    "calc_method": "pct_costo",
    "supplier_code": "233",
    "supplier_value": "0.05",
    "reference_cost": "100000",
    "customer_nit": "800116299",
    "product_sku": "1128",
    "valid_from": "2026-10-01",
    "valid_to": "2026-12-31",
    "regionals": ["antioquia"],
    "program": "PLAN 10X10",
    "value_unit": "unidad",
}


class AllowAll:
    async def allow_sliding_window(self, bucket, limit, window):
        return SimpleNamespace(allowed=True, count=1, retry_after=0)

    async def close(self):
        return None


class NoInternalRequests:
    """The internal requests side, out of these tests' scope."""

    async def permissions(self, principal):
        return {"can_review_internal": False, "can_request_internal": False}


class FakeCosts:
    """The DWH cost with the quoter's rule: 1.000 per unit for every SKU."""

    def __init__(self) -> None:
        self.refreshed: list = []

    async def refresh(self, support_ids):
        self.refreshed.extend(support_ids)
        return len(support_ids)

    async def cost_of(self, product_sku, customer_nit):
        return SupportCost(
            support_id=uuid4(),
            product_sku=product_sku,
            plan_key="lista1-mayor",
            plan_label="Lista 1",
            unit_cost=Decimal("1000"),
            source="inventario",
            quote_cost=None,
            inventory_cost=Decimal("1000"),
            snapshot_date=None,
        )


@pytest.fixture
def client(service):
    app = create_app(Settings(ENVIRONMENT=Environment.TEST))
    app.dependency_overrides[get_current_principal] = lambda: person("comercial@jdh.com.co")
    container = app.state.container
    with (
        container.redis.override(AllowAll()),
        container.v1_container.supplier_support_service.override(service),
        container.v1_container.supplier_support_request_service.override(
            SupportRequestService(None, service.managers, service.managers, service.calendar)
        ),
        container.v1_container.internal_support_request_service.override(NoInternalRequests()),
        container.v1_container.supplier_support_cost_service.override(FakeCosts()),
        # Nobody here leads BI: deleting for good is never offered (no database involved).
        container.v1_container.supplier_support_deletion_service.override(
            SupportDeletionService(None, FakeManagers([]), service)
        ),
        TestClient(app) as test_client,
    ):
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def outsider(client, service):
    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")
    return client


def test_create_returns_the_support_with_names_validity_and_derived_values(client) -> None:
    response = client.post(BASE, json=NEW)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "activo" and body["version"] == 1
    assert body["customer_name"] == "LIMPIEZA Y MANTENIMIENTO DE ANTIOQUIA SAS"
    assert body["regionals"] == ["ANTIOQUIA"] and body["product_sku"] == "1128"
    assert body["derived"]["net_cost"] == "95000.00" or float(body["derived"]["net_cost"]) == 95000
    assert body["validity"] == "vigente" and body["has_conflicts"] is False
    assert body["state"] == "vigente" and body["needs_review"] is False
    assert body["created_by"] == "comercial@jdh.com.co" and body["issues"] == []


def test_get_and_unknown_id(client) -> None:
    created = client.post(BASE, json=NEW).json()

    assert client.get(f"{BASE}/{created['id']}").json()["id"] == created["id"]
    missing = client.get(f"{BASE}/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404 and "no encontrado" in missing.json()["detail"]
    assert client.get(f"{BASE}/not-a-uuid").status_code == 422


def test_validation_errors_are_422_with_a_spanish_detail(client) -> None:
    for changes, fragment in (
        ({"product_sku": "999999"}, "Referencia JDH desconocida"),
        ({"supplier_value": "5"}, "entre 0 % y 100 %"),
        ({"supplier_code": "nope"}, "Proveedor comercial desconocido"),
        ({"customer_nit": "123"}, "NIT de cliente desconocido"),
    ):
        response = client.post(BASE, json={**NEW, **changes})
        assert response.status_code == 422 and fragment in response.json()["detail"]
    assert client.post(BASE, json={**NEW, "kind": "inventado"}).status_code == 422
    # The field to fix travels with the message, so the form marks that input.
    bad = client.post(BASE, json={**NEW, "supplier_value": "5"}).json()
    assert bad["field"] == "supplier_value"


def test_a_margin_exception_is_created_with_the_field_the_modal_shows(client) -> None:
    exception = {
        **NEW,
        "kind": "excepcion_margen",
        "calc_method": "margen_minimo",
        "supplier_value": None,
        "supported_margin_pct": "0.10",
    }

    created = client.post(BASE, json=exception)

    assert created.status_code == 201, created.json()
    missing = client.post(BASE, json={**exception, "supported_margin_pct": None})
    assert missing.status_code == 422 and missing.json()["field"] == "supported_margin_pct"
    assert "necesita el margen mínimo autorizado" in missing.json()["detail"]


def test_patch_changes_only_what_is_sent_and_enforces_the_version(client) -> None:
    created = client.post(BASE, json=NEW).json()
    url = f"{BASE}/{created['id']}"

    updated = client.patch(url, json={"version": 1, "supplier_value": "0.07", "valid_to": None})

    assert updated.status_code == 200
    body = updated.json()
    assert body["supplier_value"] in ("0.07", "0.070000") or float(body["supplier_value"]) == 0.07
    assert body["valid_to"] is None  # an explicit null clears the field
    assert body["customer_nit"] == "800116299" and body["program"] == "PLAN 10X10"
    assert body["version"] == 2
    assert client.patch(url, json={"version": 1, "notes": "x"}).status_code == 409
    assert client.patch(url, json={"notes": "x"}).status_code == 422  # version is required


def test_status_can_not_be_set_to_voided_through_patch(client) -> None:
    created = client.post(BASE, json=NEW).json()

    response = client.patch(f"{BASE}/{created['id']}", json={"version": 1, "status": "anulado"})

    assert response.status_code == 422


def test_void_keeps_the_support_but_hides_it_from_default_searches(client) -> None:
    created = client.post(BASE, json=NEW).json()

    voided = client.post(f"{BASE}/{created['id']}/void", json={"version": 1, "reason": "duplicado"})

    assert voided.status_code == 200 and voided.json()["status"] == "anulado"
    assert voided.json()["void_reason"] == "duplicado"
    assert client.post(f"{BASE}/search", json={}).json()["total"] == 0
    assert client.post(f"{BASE}/search", json={"include_voided": True}).json()["total"] == 1
    assert (
        client.patch(f"{BASE}/{created['id']}", json={"version": 2, "notes": "x"}).status_code
        == 409
    )


def test_search_validates_the_filter_language(client, repo) -> None:
    client.post(BASE, json=NEW)

    ok = client.post(
        f"{BASE}/search",
        json={
            "match": "all",
            "conditions": [{"field": "supplier.code", "op": "in", "value": ["233"]}],
            "validity": "vigente",
            "sort": ["-valid_from"],
            "page": 1,
            "size": 10,
        },
    )
    assert ok.status_code == 200 and ok.json()["total"] == 1
    assert repo.last_search.sort[0].field == "valid_from"

    for bad in (
        {"conditions": [{"field": "password", "op": "eq", "value": "x"}]},
        {"conditions": [{"field": "program", "op": "gt", "value": "x"}]},
        {"sort": ["nope"]},
    ):
        assert client.post(f"{BASE}/search", json=bad).status_code == 422
    assert client.post(f"{BASE}/search", json={"size": 500}).status_code == 422
    assert client.post(f"{BASE}/search", json={"validity": "nunca"}).status_code == 422


def test_search_fields_and_catalogs_describe_the_language(client) -> None:
    fields = {item["field"]: item for item in client.get(f"{BASE}/search/fields").json()}
    catalogs = client.get(f"{BASE}/catalogs").json()

    assert "between" in fields["supplier_value"]["operators"]
    assert "pct_costo" in catalogs["calc_methods"] and catalogs["regionals"] == ["ANTIOQUIA"]


def test_batch_is_all_or_nothing(client) -> None:
    ok = client.post(f"{BASE}/batch", json={"items": [NEW, {**NEW, "product_sku": "8749"}]})
    assert ok.status_code == 201 and len(ok.json()) == 2

    bad = client.post(f"{BASE}/batch", json={"items": [NEW, {**NEW, "product_sku": "999999"}]})
    assert bad.status_code == 422
    assert client.post(f"{BASE}/search", json={}).json()["total"] == 2  # nothing added


def test_only_managers_write_but_anyone_authenticated_reads(outsider) -> None:
    assert outsider.post(BASE, json=NEW).status_code == 403
    assert outsider.post(f"{BASE}/search", json={}).status_code == 200
    assert outsider.get(f"{BASE}/catalogs").status_code == 200


def test_routes_are_documented(client) -> None:
    paths = set(client.app.openapi()["paths"])

    for path in (
        f"{BASE}",
        f"{BASE}/search",
        f"{BASE}/search/fields",
        f"{BASE}/catalogs",
        f"{BASE}/batch",
        f"{BASE}/{{support_id}}",
        f"{BASE}/{{support_id}}/void",
        f"{BASE}/{{support_id}}/conflicts",
    ):
        assert path in paths, path


def test_state_is_one_value_and_voided_wins_over_validity(client, repo) -> None:
    created = client.post(BASE, json=NEW).json()
    assert created["state"] == "vigente"

    voided = client.post(f"{BASE}/{created['id']}/void", json={"version": 1, "reason": "duplicado"})

    assert voided.json()["state"] == "anulado" and voided.json()["needs_review"] is False
    client.post(f"{BASE}/search", json={"state": "anulado", "needs_review": False})
    assert repo.last_search.state == "anulado" and repo.last_search.needs_review is False
    assert client.post(f"{BASE}/search", json={"state": "activo"}).status_code == 422
    assert "states" in client.get(f"{BASE}/catalogs").json()


def test_prices_come_per_box_and_per_unit_and_the_unit_is_required(client) -> None:
    # Maizena x720g: 24 per box; the 13.783 is per unit.
    created = client.post(
        BASE,
        json={**NEW, "product_sku": "3474", "reference_cost": "13783", "supplier_value": "0.05"},
    ).json()

    derived = created["derived"]
    assert (created["value_unit"], created["unit_basis_source"]) == ("unidad", "manual")
    assert created["pack_factor"] in ("24", "24.0000") or float(created["pack_factor"]) == 24
    assert float(derived["cost"]["unit"]) == 13783 and float(derived["cost"]["box"]) == 330792
    assert float(derived["net_cost_per"]["unit"]) == pytest.approx(13093.85)
    assert float(derived["net_cost_per"]["box"]) == pytest.approx(13093.85 * 24)
    # Without the unit a creation is a 422 that marks the field (2026-11-20).
    missing = client.post(BASE, json={**NEW, "value_unit": None})
    assert missing.status_code == 422 and missing.json()["field"] == "value_unit"
    assert "Unidad valor es obligatoria" in missing.json()["detail"]


def test_a_support_can_be_switched_off_and_on_from_the_actions_column(client) -> None:
    created = client.post(BASE, json=NEW).json()
    url = f"{BASE}/{created['id']}"

    off = client.patch(url, json={"version": 1, "is_enabled": False}).json()
    on = client.patch(url, json={"version": 2, "is_enabled": True}).json()

    assert (off["state"], off["is_enabled"], off["status"]) == ("inactivo", False, "activo")
    assert (on["state"], on["is_enabled"]) == ("vigente", True)
    assert client.patch(url, json={"version": 3, "is_enabled": None}).status_code == 422


def test_base_search_accepts_client_supplier_and_reference_independently(client, repo) -> None:
    client.post(BASE, json=NEW)

    both = client.post(
        f"{BASE}/search",
        json={
            "customer_nits": ["800.116.299"],
            "supplier_codes": ["233"],
            "product_skus": ["1128"],
        },
    )
    assert both.status_code == 200
    base = repo.last_search.group.items[:-1]
    assert {item.field for item in base} == {"supplier.code", "product_sku"}
    assert repo.last_search.customer_nits == ("800116299",)

    client.post(f"{BASE}/search", json={"product_skus": ["1128"]})
    assert [item.field for item in repo.last_search.group.items[:-1]] == ["product_sku"]
    client.post(f"{BASE}/search", json={"state": "inactivo"})
    assert repo.last_search.state == "inactivo"


def test_the_counts_by_state_and_por_vencer(client, repo) -> None:
    client.post(BASE, json=NEW)

    counts = client.post(f"{BASE}/summary", json={"state": "vencido", "page": 2, "size": 10})
    listed = client.post(f"{BASE}/search", json={"validity": "por_vencer"})

    assert counts.status_code == 200 and listed.status_code == 200
    assert counts.json() == {
        "vigente": 1,
        "por_vencer": 0,
        "programado": 0,
        "vencido": 0,
        "inactivo": 0,
        "total": 1,
    }
    assert repo.last_search.validity == "por_vencer"
    item = client.post(f"{BASE}/search", json={}).json()["items"][0]
    assert item["expiring"] is False and item["group_wide"] is False
    assert client.post(f"{BASE}/search", json={"state": "pronto"}).status_code == 422


def test_a_crossing_support_is_a_409_with_the_overlaps_and_can_replace_it(client) -> None:
    first = client.post(BASE, json=NEW).json()

    crossing = client.post(BASE, json={**NEW, "valid_from": "2026-11-01"})
    assert crossing.status_code == 409
    assert [item["id"] for item in crossing.json()["overlaps"]] == [first["id"]]

    replaced = client.post(
        BASE, json={**NEW, "valid_from": "2026-11-01", "on_overlap": "vencer_anterior"}
    )
    assert replaced.status_code == 201
    assert client.get(f"{BASE}/{first['id']}").json()["valid_to"] == "2026-10-31"


def test_mass_void_by_ids_with_the_observation(client) -> None:
    created = client.post(BASE, json=NEW).json()

    missing = client.post(f"{BASE}/retire", json={"action": "anular", "reason": "Duplicado"})
    response = client.post(
        f"{BASE}/retire",
        json={
            "action": "anular",
            "reason": "Duplicado",
            "items": [{"id": created["id"], "version": 1}],
        },
    )

    assert missing.status_code == 422
    assert response.status_code == 200 and response.json()["voided"] == [created["id"]]
    assert client.get(f"{BASE}/{created['id']}").json()["void_reason"] == "Duplicado"


def test_the_preview_shows_the_form_figures_without_saving(client) -> None:
    body = {**NEW, "supplier_value": "0.10", "supported_margin_pct": "0.08"}
    body.pop("reference_cost")

    response = client.post(f"{BASE}/preview", json=body)

    assert response.status_code == 200
    derived = response.json()["derived"]
    # DWH cost 1.000, Food service 20 %: list price 1.250, 10 % of cost = 100 off.
    assert derived["cost_basis"] == "dwh" and float(derived["list_price"]["unit"]) == 1250
    assert float(derived["suggested_price"]) == 1150
    assert float(derived["supported_price"]) == pytest.approx(900 / 0.92)
    assert client.post(f"{BASE}/search", json={}).json()["total"] == 0  # nothing saved


def test_comercial_never_sees_the_cost(client) -> None:
    created = client.post(BASE, json=NEW).json()
    assert created["reference_cost"] is not None  # BI sees it

    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")
    seen = client.post(f"{BASE}/search", json={}).json()["items"][0]

    assert seen["reference_cost"] is None and seen["dwh_cost"] is None
    assert seen["derived"]["cost"] == {"box": None, "unit": None}
    assert seen["derived"]["net_cost"] is None and seen["derived"]["margin_order"] is None
    # The prices and the support itself stay.
    assert seen["supplier_value"] == created["supplier_value"]
    assert "suggested_price" in seen["derived"] and "supported_price" in seen["derived"]


def test_permissions_tell_the_ui_whether_to_offer_write_actions(client) -> None:
    assert client.get(f"{BASE}/permissions").json() == {
        "can_manage": True,
        "can_request": True,
        "can_review": True,
        "can_read": True,
        "can_review_internal": False,
        "can_request_internal": False,
        # Deleting for good is only for BI's leader and platform admins (2026-11-23).
        "can_delete": False,
        "customer_scope": "all",
    }

    client.app.dependency_overrides[get_current_principal] = lambda: person("otro@jdh.com.co")

    assert client.get(f"{BASE}/permissions").json() == {
        "can_manage": False,
        "can_request": False,
        "can_review": False,
        "can_read": False,
        "can_review_internal": False,
        "can_request_internal": False,
        "can_delete": False,
        "customer_scope": "all",
    }
