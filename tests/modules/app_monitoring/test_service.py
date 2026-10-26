"""Registering applications, checking them and reading their status."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.app_monitoring.application.service import AppMonitoringService, slugify
from app.modules.app_monitoring.domain.entities import CheckResult, CheckStatus, Target

from tests.modules.app_monitoring.conftest import NOW, TI_MEMBER, FakeAccess
from tests.modules.dwh_monitoring.conftest import OUTSIDER


async def register(service, name="Intranet", url="https://intranet.jdh.local", **values):
    view = await service.create(TI_MEMBER, {"name": name, "base_url": url, **values})
    return view["aplicativo"]


def test_slugs_drop_accents_and_symbols() -> None:
    assert slugify("Catálogo (Tienda)") == "catalogo-tienda"


async def test_a_new_application_gets_the_default_paths_and_a_slug(service) -> None:
    app = await register(service, url="https://intranet.jdh.local/")

    assert app.slug == "intranet" and app.base_url == "https://intranet.jdh.local"
    assert app.url(Target.API) == "https://intranet.jdh.local/api/health"
    assert app.url(Target.UI) == "https://intranet.jdh.local/"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://intranet.jdh.local",
        "intranet.jdh.local",
        "https://x.jdh.local/api/health",
        "https://u:p@x.jdh.local",
        "https://x.jdh.local?a=1",
    ],
)
async def test_the_base_url_is_only_a_server(service, url) -> None:
    with pytest.raises(InvalidInputError):
        await register(service, url=url)


async def test_slugs_are_unique(service) -> None:
    await register(service)
    with pytest.raises(ConflictError):
        await register(service, url="https://otra.jdh.local")


async def test_only_ti_reads_and_edits(apps, prober) -> None:
    service = AppMonitoringService(apps, prober, FakeAccess(), clock=lambda: NOW)
    with pytest.raises(ForbiddenError):
        await service.applications(OUTSIDER)
    with pytest.raises(ForbiddenError):
        await service.create(OUTSIDER, {"name": "X", "base_url": "https://x.jdh.local"})


async def test_check_all_probes_api_and_ui_of_enabled_apps_and_purges(service, apps, prober):
    await register(service)
    await register(service, "API sola", "https://api.jdh.local", check_ui=False)
    await register(service, "Apagada", "https://off.jdh.local", enabled=False)

    results = await service.check_all()

    assert sorted(prober.calls) == [
        ("api-sola", Target.API),
        ("intranet", Target.API),
        ("intranet", Target.UI),
    ]
    assert len(results) == 3 and len(apps.checks) == 3
    assert apps.purged_before == NOW - timedelta(days=30)


async def test_the_status_is_the_worst_target_and_counts_by_status(service, prober) -> None:
    await register(service)
    await register(service, "Tienda", "https://tienda.jdh.local")
    await register(service, "Proveedores", "https://app-proveedores.jdh.local")
    prober.answers[("tienda", Target.UI)] = CheckStatus.DEGRADED
    prober.answers[("proveedores", Target.API)] = CheckStatus.DOWN
    await service.check_all()

    overview = await service.overview(TI_MEMBER)

    by_slug = {item["aplicativo"].slug: item for item in overview["aplicativos"]}
    assert by_slug["intranet"]["estado"] is CheckStatus.UP
    assert by_slug["tienda"]["estado"] is CheckStatus.DEGRADED
    assert by_slug["proveedores"]["estado"] is CheckStatus.DOWN
    assert by_slug["proveedores"]["api"]["disponibilidad_24h"] == 0
    assert by_slug["tienda"]["ui"]["disponibilidad_24h"] == 100
    assert (overview["arriba"], overview["degradados"], overview["caidos"]) == (1, 1, 1)
    assert overview["estado"] is CheckStatus.DOWN and overview["ultimo_chequeo"] == NOW


async def test_a_never_checked_or_disabled_app_has_no_status(service) -> None:
    app = await register(service)
    assert (await service.application(app.id, TI_MEMBER))["estado"] is None

    await service.check_all()
    await service.update(app.id, TI_MEMBER, {"enabled": False})

    overview = await service.overview(TI_MEMBER)
    assert overview["aplicativos"][0]["estado"] is None and overview["habilitados"] == 0


async def test_check_now_works_on_a_disabled_app(service, prober) -> None:
    app = await register(service, enabled=False, check_ui=False)

    view = await service.check_now(app.id, TI_MEMBER)

    assert prober.calls == [("intranet", Target.API)]
    assert view["api"]["ultimo"].status is CheckStatus.UP and view["ui"] is None


async def test_update_changes_only_what_is_sent(service) -> None:
    app = await register(service)

    view = await service.update(app.id, TI_MEMBER, {"verify_tls": False, "slow_ms": 5000})

    updated = view["aplicativo"]
    assert updated.verify_tls is False and updated.slow_ms == 5000
    assert updated.base_url == app.base_url and updated.slug == app.slug
    with pytest.raises(InvalidInputError):
        await service.update(app.id, TI_MEMBER, {"health_path": "api/health"})
    with pytest.raises(InvalidInputError):
        await service.update(app.id, TI_MEMBER, {"name": None})


async def test_unknown_applications_are_not_found(service) -> None:
    with pytest.raises(NotFoundError):
        await service.application(uuid4(), TI_MEMBER)
    with pytest.raises(NotFoundError):
        await service.delete(uuid4(), TI_MEMBER)


async def test_history_is_newest_first_and_filtered_by_target(service, apps) -> None:
    app = await register(service)
    for minutes in (1, 2, 3):
        apps.checks.append(
            CheckResult(app.id, Target.API, NOW - timedelta(minutes=minutes), CheckStatus.UP)
        )
    apps.checks.append(CheckResult(app.id, Target.UI, NOW, CheckStatus.UP))

    items, total = await service.history(app.id, TI_MEMBER, Target.API, None, None, 1, 2)

    assert total == 3 and [i.checked_at for i in items] == [
        NOW - timedelta(minutes=1),
        NOW - timedelta(minutes=2),
    ]


async def test_asking_for_a_check_needs_the_loop_on(apps, prober) -> None:
    woken = []
    on = AppMonitoringService(
        apps, prober, FakeAccess(), check_enabled=True, request_check=lambda: woken.append(1)
    )
    await on.request_check(TI_MEMBER)
    assert woken == [1]

    off = AppMonitoringService(apps, prober, FakeAccess())
    with pytest.raises(ConflictError):
        await off.request_check(TI_MEMBER)
