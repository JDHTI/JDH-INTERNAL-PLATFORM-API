"""Action plans (P.04): rules of an action, who works it, from a case, attempts, evidence."""

from datetime import date

import pytest

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError
from app.modules.action_plans.application.service import (
    ActivityInput,
    CauseInput,
    CorrectionInput,
    PlanChanges,
    UploadedFile,
)
from app.modules.action_plans.domain.entities import plan_code
from app.modules.action_plans.domain.ports import PlanFilters
from tests.modules.action_plans.fakes import PROCESSES, World


def _header(**values) -> PlanChanges:
    base = {
        "origin": "AE",
        "process_id": PROCESSES["ALM"].id,
        "action_type": "correctiva",
        "reported_on": date(2026, 7, 6),
        "finding": "Hallazgo de auditoría externa",
    }
    base.update(values)
    return PlanChanges(frozenset(base), base)


async def _plan_with_activity(w: World):
    plan = await w.service.create_plan(_header(), w.calidad)
    return await w.service.replace_activities(
        plan.id,
        [
            ActivityInput(
                "Capacitar al personal de despacho",
                responsible_user_ids=(w.owner.user_id,),
                due_on=date(2026, 11, 1),
            )
        ],
        w.calidad,
    )


def test_the_code_follows_the_excel_formula() -> None:
    assert plan_code("SNC", date(2026, 1, 29), 121, "DIS", 1) == "SNC-290126-121-DIS-1"


@pytest.mark.asyncio
async def test_calidad_creates_an_action_with_its_code_number_and_who_registered_it() -> None:
    w = World()

    plan = await w.service.create_plan(_header(), w.calidad)

    assert plan.number == 201 and plan.attempt == 1
    assert plan.code == "AE-060726-201-ALM-1"
    assert plan.rating == "real" and plan.status == "abierta"
    assert plan.registered_by_name == "Cristian Garzón"
    assert plan.registered_by_position == "Analista de Calidad"
    assert w.plans.events[0].event_type == "created"


@pytest.mark.asyncio
async def test_only_calidad_creates_and_edits() -> None:
    w = World()

    for who in (w.reader, w.owner):
        with pytest.raises(ForbiddenError):
            await w.service.create_plan(_header(), who)
    with pytest.raises(InvalidInputError, match="procedencia"):
        await w.service.create_plan(_header(process_id=None), w.calidad)


@pytest.mark.asyncio
async def test_the_type_decides_what_the_action_admits() -> None:
    w = World()
    preventive = await w.service.create_plan(_header(action_type="preventiva"), w.calidad)
    improvement = await w.service.create_plan(_header(action_type="mejora"), w.calidad)

    assert preventive.rating == "potencial" and improvement.rating == "oportunidad_mejora"
    with pytest.raises(InvalidInputError, match="corrección inmediata"):
        await w.service.replace_corrections(
            preventive.id, [CorrectionInput("Retirar el lote")], w.calidad
        )
    with pytest.raises(InvalidInputError, match="análisis"):
        await w.service.replace_causes(improvement.id, [CauseInput("Una causa")], w.calidad)


@pytest.mark.asyncio
async def test_an_action_from_a_case_comes_filled() -> None:
    w = World()
    earlier = await w.service.create_plan(
        _header(origin="SNC", reporter_nit="860066942"), w.calidad
    )
    w.plans.plans[earlier.id].cause_label = "Producto vencido"

    plan = await w.service.create_from_case(w.cases.facts_value.id, w.calidad)

    assert plan.origin == "SNC" and plan.action_type == "correctiva"
    assert plan.process.code == "DIS" and plan.code.startswith("SNC-031126-")
    assert (plan.reporter_nit, plan.reporter_company_role, plan.city) == (
        "860066942",
        "CAFAM",
        "Funza",
    )
    assert plan.finding == "Calidad del producto\n\nLlegó producto vencido"
    assert plan.has_photos and plan.has_history
    assert [c.description for c in plan.corrections] == [
        "Se aprobó la orden de recogida 3847",
        "Se envió la oportunidad de mejora 1181BOG al proveedor",
    ]
    assert plan.case_code == "Q26BG0001"
    assert w.cases.linked == [(w.cases.facts_value.id, plan.code)]


@pytest.mark.asyncio
async def test_editing_the_plan_keeps_progress_and_tells_only_the_new_responsibles() -> None:
    w = World()
    plan = await _plan_with_activity(w)
    activity = plan.activities[0]
    await w.service.mark_done(plan.id, activity.id, True, "Lista de asistencia", w.owner)
    w.bell.rung.clear()

    updated = await w.service.replace_activities(
        plan.id,
        [
            ActivityInput(
                "Capacitar al personal de despacho (todas las sedes)",
                id=activity.id,
                responsible_user_ids=(w.owner.user_id,),
            ),
            ActivityInput("Validar rótulos", responsible_user_ids=(w.other.user_id,)),
        ],
        w.calidad,
    )

    kept, added = updated.activities
    assert kept.id == activity.id and kept.done and kept.evidence_note is None
    assert not added.done
    assert w.bell.rung == [
        ([w.other.user_id], f"Plan de acción {plan.code}", f"/calidad/planes-accion/{plan.id}")
    ]


@pytest.mark.asyncio
async def test_a_responsible_marks_only_their_own_rows() -> None:
    w = World()
    plan = await _plan_with_activity(w)
    activity = plan.activities[0]

    with pytest.raises(ForbiddenError):
        await w.service.mark_done(plan.id, activity.id, True, None, w.other)
    with pytest.raises(ForbiddenError):
        await w.service.mark_done(plan.id, activity.id, True, None, w.reader)
    done = await w.service.mark_done(plan.id, activity.id, True, "Acta firmada", w.owner)

    assert done.activities[0].done and done.status == "cerrada"
    # Rating is Calidad's.
    with pytest.raises(ForbiddenError):
        await w.service.rate_activity(plan.id, activity.id, "si", None, w.owner)


@pytest.mark.asyncio
async def test_who_sees_what() -> None:
    w = World()
    plan = await _plan_with_activity(w)
    await w.service.create_plan(_header(), w.calidad)

    assert (await w.service.list_plans(PlanFilters(), 1, 25, w.reader)).total == 2
    mine = await w.service.list_plans(PlanFilters(), 1, 25, w.owner)
    assert [p.id for p in mine.items] == [plan.id]
    assert (await w.service.list_plans(PlanFilters(), 1, 25, w.other)).total == 0
    detail = await w.service.get_plan(plan.id, w.owner)
    assert detail.access.is_responsible and not detail.access.can_manage
    with pytest.raises(ForbiddenError):
        await w.service.get_plan(plan.id, w.other)
    with pytest.raises(ForbiddenError):
        await w.service.export_plans(PlanFilters(), w.owner)


@pytest.mark.asyncio
async def test_effectiveness_is_rated_once_done_and_a_failure_opens_a_new_attempt() -> None:
    w = World()
    plan = await _plan_with_activity(w)
    activity = plan.activities[0]

    with pytest.raises(ConflictError, match="realizada"):
        await w.service.rate_activity(plan.id, activity.id, "no", None, w.calidad)
    with pytest.raises(ConflictError, match="no eficaz"):
        await w.service.next_attempt(plan.id, w.calidad)
    await w.service.mark_done(plan.id, activity.id, True, None, w.owner)
    rated = await w.service.rate_activity(
        plan.id, activity.id, "no", "Se repitió el error", w.calidad
    )
    assert rated.effectiveness == "no"

    attempt = await w.service.next_attempt(plan.id, w.calidad)

    assert (attempt.number, attempt.attempt) == (plan.number, 2)
    assert attempt.code.endswith(f"-{plan.number}-ALM-2")
    assert attempt.previous_attempt_id == plan.id and attempt.has_history
    assert attempt.activities == [] and attempt.finding == plan.finding


@pytest.mark.asyncio
async def test_evidence_goes_to_the_bucket_for_calidad_or_a_responsible() -> None:
    w = World()
    plan = await _plan_with_activity(w)
    row = plan.activities[0].id

    stored, rejected = await w.service.add_files(
        plan.id,
        [UploadedFile("lista.pdf", b"%PDF"), UploadedFile("virus.exe", b"MZ")],
        w.owner,
        row_id=row,
    )

    assert [f.original_filename for f in stored] == ["lista.pdf"]
    assert stored[0].activity_id == row
    assert w.bucket.objects[stored[0].storage_key] == b"%PDF"
    assert rejected == [{"name": "virus.exe", "reason": "tipo de archivo no permitido"}]
    with pytest.raises(ForbiddenError):
        await w.service.add_files(plan.id, [UploadedFile("x.pdf", b"1")], w.reader)
    with pytest.raises(ForbiddenError):
        await w.service.delete_file(plan.id, stored[0].id, w.other)
    await w.service.delete_file(plan.id, stored[0].id, w.owner)
    assert w.bucket.objects == {}


@pytest.mark.asyncio
async def test_indicators_count_actions_and_activities() -> None:
    w = World()
    plan = await _plan_with_activity(w)
    await w.service.mark_done(plan.id, plan.activities[0].id, True, None, w.owner)
    await w.service.rate_activity(plan.id, plan.activities[0].id, "si", None, w.calidad)
    await w.service.create_plan(_header(action_type="mejora"), w.calidad)

    value = await w.service.indicators(PlanFilters(), w.reader)

    assert value.actions == 2 and value.activities == 1 and value.activities_done == 1
    assert value.by_type == {"correctiva": 1, "mejora": 1}
    assert value.by_status == {"cerrada": 1, "abierta": 1}
    assert value.activities_effective == {"si": 1}
