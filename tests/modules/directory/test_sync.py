"""The directory sync: employees, users, leavers, managers and photos."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, DependencyUnavailableError
from app.modules.directory.application.sync import DirectorySyncService, account_change
from app.modules.directory.domain.entities import Employee, RunStatus, RunTrigger, Visibility
from tests.modules.directory.conftest import NOW, FakeGraph, graph_user


def service(graph, repository, runs, provisioning, storage) -> DirectorySyncService:
    return DirectorySyncService(
        graph,
        repository,
        runs,
        provisioning,
        storage,
        corporate_domains=["jdh.com.co"],
        batch_size=2,
        clock=lambda: NOW,
    )


async def sync(graph, repository, runs, provisioning, storage):
    return await service(graph, repository, runs, provisioning, storage).run(
        RunTrigger.MANUAL, "admin@jdh.com.co"
    )


@pytest.mark.asyncio
async def test_first_sync_creates_employees_and_their_users(
    repository, runs, provisioning, storage
) -> None:
    bodega = repository.add_site("Bodega Principal")
    graph = FakeGraph(
        [
            graph_user(
                "ana",
                "Ana María Pérez",
                job_title="Analista",
                department="TI",
                office_location="bodega principal",
                hire_date=date(2024, 3, 1),
            ),
            graph_user("luis", "Luis Gómez", department="TI"),
            graph_user("guest", "Invitado", user_type="Guest"),
        ]
    )
    graph.managers["ana"] = "luis"

    run = await sync(graph, repository, runs, provisioning, storage)

    assert run.status is RunStatus.SUCCESS
    assert run.counts["total"] == 2 and run.counts["created"] == 2
    assert run.counts["skipped_guests"] == 1 and run.counts["without_site"] == 1
    ana = await repository.get_employee_by_oid("ana")
    luis = await repository.get_employee_by_oid("luis")
    assert ana.user_id == provisioning.users["ana"].id
    assert ana.site.id == bodega.id and ana.position == ""
    assert ana.hire_date == date(2024, 3, 1)
    assert ana.manager.id == luis.id
    assert luis.is_manager is True and ana.is_manager is False
    assert ana.is_public and ana.auto_visible
    assert "guest" not in provisioning.users


@pytest.mark.asyncio
async def test_a_second_sync_keeps_what_the_directory_owns(
    repository, runs, provisioning, storage
) -> None:
    repository.add_site("Sede Norte")
    graph = FakeGraph([graph_user("ana", "Ana", office_location="Sede Norte")])
    await sync(graph, repository, runs, provisioning, storage)
    ana = await repository.get_employee_by_oid("ana")
    await repository.update_employee(
        ana.id, {"position": "Analista BI", "birth_date": date(1990, 5, 2), "site_id": None}
    )

    graph.users = [graph_user("ana", "Ana Pérez", office_location="Sede Norte", department="BI")]
    run = await sync(graph, repository, runs, provisioning, storage)

    ana = await repository.get_employee_by_oid("ana")
    assert run.counts["updated"] == 1 and run.counts["created"] == 0
    assert ana.full_name == "Ana Pérez" and ana.department == "BI"
    assert ana.position == "Analista BI" and ana.birth_date == date(1990, 5, 2)
    # The site came from Entra once; the directory cleared it, and it stays cleared.
    assert ana.site is None
    assert run.counts["without_site"] == 1


@pytest.mark.asyncio
async def test_leavers_are_deactivated_with_their_users(
    repository, runs, provisioning, storage
) -> None:
    graph = FakeGraph([graph_user("ana", "Ana"), graph_user("luis", "Luis")])
    await sync(graph, repository, runs, provisioning, storage)
    manual = await repository.create_employee({"full_name": "Contratista", "position": ""})

    graph.users = [graph_user("ana", "Ana")]
    run = await sync(graph, repository, runs, provisioning, storage)

    luis = await repository.get_employee_by_oid("luis")
    assert run.counts["deactivated"] == 1
    assert luis.is_active is False and luis.account_enabled is False
    assert provisioning.deactivated == [provisioning.users["luis"].id]
    assert (await repository.get_employee(manual.id)).is_active is True


@pytest.mark.asyncio
async def test_the_last_admin_is_never_blocked(repository, runs, provisioning, storage) -> None:
    graph = FakeGraph([graph_user("boss", "Jefe"), graph_user("ana", "Ana")])
    await sync(graph, repository, runs, provisioning, storage)
    provisioning.protected.add("boss")

    graph.users = [graph_user("ana", "Ana")]
    run = await sync(graph, repository, runs, provisioning, storage)

    assert run.counts["last_admin_protected"] == 1
    assert provisioning.deactivated == []


def test_users_is_active_only_changes_on_edges() -> None:
    enabled = graph_user("ana", "Ana")
    disabled = graph_user("ana", "Ana", account_enabled=False)
    was_disabled = Employee(id=uuid4(), full_name="Ana", account_enabled=False)
    was_enabled = replace(was_disabled, account_enabled=True)

    assert account_change(disabled, None) is False
    assert account_change(disabled, was_enabled) is False
    assert account_change(enabled, was_disabled) is True
    assert account_change(enabled, was_enabled) is None
    assert account_change(enabled, None) is None


@pytest.mark.asyncio
async def test_a_disabled_account_blocks_and_a_reenabled_one_unblocks(
    repository, runs, provisioning, storage
) -> None:
    graph = FakeGraph([graph_user("ana", "Ana", account_enabled=False)])
    await sync(graph, repository, runs, provisioning, storage)
    assert provisioning.users["ana"].is_active is False
    assert (await repository.get_employee_by_oid("ana")).is_public is False

    graph.users = [graph_user("ana", "Ana")]
    await sync(graph, repository, runs, provisioning, storage)
    assert provisioning.users["ana"].is_active is True

    # An admin blocks her by hand: the next quiet night leaves the block alone.
    provisioning.users["ana"].is_active = False
    await sync(graph, repository, runs, provisioning, storage)
    assert provisioning.users["ana"].last_change is None
    assert provisioning.users["ana"].is_active is False


@pytest.mark.asyncio
async def test_visibility_follows_the_corporate_domain(
    repository, runs, provisioning, storage
) -> None:
    graph = FakeGraph(
        [graph_user("ana", "Ana"), graph_user("svc", "Escáner", mail="scanner@proveedor.com")]
    )
    await sync(graph, repository, runs, provisioning, storage)

    service_account = await repository.get_employee_by_oid("svc")
    assert service_account.auto_visible is False and service_account.is_public is False
    await repository.update_employee(service_account.id, {"visibility": Visibility.SHOWN})
    assert (await repository.get_employee_by_oid("svc")).is_public is True


@pytest.mark.asyncio
async def test_photos_follow_the_graph_etag(repository, runs, provisioning, storage) -> None:
    graph = FakeGraph([graph_user("ana", "Ana"), graph_user("luis", "Luis")])
    graph.photos["ana"] = "v1"

    run = await sync(graph, repository, runs, provisioning, storage)
    ana = await repository.get_employee_by_oid("ana")
    assert run.counts["photos"] == 1
    assert ana.photo_key == "directory-photos/ana/432"
    assert ana.photo_thumb_key == "directory-photos/ana/96"
    assert storage.objects["directory-photos/ana/96"][0] == b"ana-96"
    assert ana.photo_version is not None
    assert (await repository.get_employee_by_oid("luis")).photo_key is None

    graph.downloads.clear()
    run = await sync(graph, repository, runs, provisioning, storage)
    assert run.counts["photos"] == 0 and graph.downloads == []

    graph.photos.pop("ana")
    await sync(graph, repository, runs, provisioning, storage)
    ana = await repository.get_employee_by_oid("ana")
    assert ana.photo_key is None and ana.photo_version is None
    assert "directory-photos/ana/432" in storage.deleted


@pytest.mark.asyncio
async def test_one_failing_person_does_not_fail_the_sync(
    repository, runs, provisioning, storage
) -> None:
    graph = FakeGraph([graph_user("ana", "Ana"), graph_user("luis", "Luis")])
    graph.fail_manager.add("luis")

    run = await sync(graph, repository, runs, provisioning, storage)

    assert run.status is RunStatus.SUCCESS and run.counts["errors"] == 1


@pytest.mark.asyncio
async def test_a_graph_failure_fails_the_run(repository, runs, provisioning, storage) -> None:
    graph = FakeGraph()
    graph.fail_users = DependencyUnavailableError("Microsoft Graph respondió 503")

    run = await sync(graph, repository, runs, provisioning, storage)

    assert run.status is RunStatus.FAILED
    assert run.error_code == "DependencyUnavailableError"
    assert "503" in run.error_message and run.finished_at == NOW


@pytest.mark.asyncio
async def test_only_one_sync_runs_and_stale_runs_are_recovered(
    repository, runs, provisioning, storage
) -> None:
    sync_service = service(FakeGraph(), repository, runs, provisioning, storage)
    await sync_service.start(RunTrigger.SCHEDULED, None)

    with pytest.raises(ConflictError):
        await sync_service.start(RunTrigger.MANUAL, "admin")
    assert await sync_service.needs_catch_up() is True

    assert await sync_service.recover_stale_runs() == 1
    latest = await runs.latest()
    assert latest.status is RunStatus.FAILED and latest.error_code == "interrupted"

    await sync_service.run(RunTrigger.MANUAL, "admin")
    assert await sync_service.needs_catch_up() is False
