"""Load the 2026 P.04.F.02 matrix into the module (once, idempotent by code).

Each action keeps the code of the Excel (column E) and its rows; responsibles stay as the
text the Excel had (they are not matched to platform users). Dates the Excel could not hold
(«30-02-2026») stay empty and are reported. An action whose code is already in the module is
left alone, so running it twice changes nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from app.modules.action_plans.domain.entities import (
    DEFAULT_RATING,
    ActionPlan,
    Activity,
    Cause,
    Correction,
    PlanEvent,
)
from app.modules.action_plans.domain.ports import ActionPlanRepository
from app.modules.action_plans.infrastructure.workbook import (
    ImportedAction,
    group_actions,
    header_choices,
    read_rows,
)

SOURCE = "P.04.F.02 Seguimiento PA JDH V2 SIG 2026"


@dataclass
class ImportReport:
    """What the import found and did."""

    actions: int = 0
    rows: int = 0
    created: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    corrections: int = 0
    causes: int = 0
    activities: int = 0


def _yes(value: str | None) -> bool:
    return (value or "").strip().lower() in {"si", "sí", "yes"}


def _number(value: str | None) -> int | None:
    try:
        return int(float(value)) if value else None
    except ValueError:
        return None


async def to_plan(action: ImportedAction, repository: ActionPlanRepository) -> ActionPlan | str:
    """The ``ActionPlan`` of an imported action, or why it cannot be loaded."""

    origin, action_type, rating, method = header_choices(action)
    header = action.header
    number = _number(header.get("number"))
    process_code = (header.get("process") or "").strip().upper()
    process = await repository.get_process_by_code(process_code) if process_code else None
    if origin is None:
        return f"origen «{header.get('origin')}» desconocido"
    if process is None:
        return f"procedencia «{header.get('process')}» no está en el catálogo"
    if number is None:
        return "sin número"
    if header.get("reported_on") is None:
        return "sin fecha"
    action_type = action_type or "correctiva"
    corrections, causes, activities = [], [], []
    for row in action.rows:
        values = row.values
        if row.kind == "correction":
            corrections.append(
                Correction(
                    uuid4(),
                    values["description"],
                    responsible_text=values["responsible_text"],
                    due_on=values["due_on"],
                    evidence_note=values["evidence_note"],
                )
            )
        elif row.kind == "cause":
            causes.append(Cause(uuid4(), values["description"], values["category"]))
        else:
            activities.append(
                Activity(
                    uuid4(),
                    values["description"],
                    responsible_text=values["responsible_text"],
                    due_on=values["due_on"],
                    evidence_note=values["evidence_note"],
                    follow_up_text=values["follow_up_text"],
                    follow_up_due_on=values["follow_up_due_on"],
                    done=values["done"],
                    effective=values["effective"],
                    effectiveness_note=values["effectiveness_note"],
                )
            )
    return ActionPlan(
        id=uuid4(),
        number=number,
        attempt=_number(header.get("attempt")) or 1,
        origin=origin,
        process=process,
        action_type=action_type,
        rating=rating or DEFAULT_RATING[action_type],
        reported_on=header["reported_on"],
        code=action.code,
        reporter_nit=header.get("reporter_nit"),
        reporter_name=header.get("reporter_name"),
        reporter_company_role=header.get("reporter_company_role"),
        city=(header.get("city") or "").strip() or None,
        registered_by_name=header.get("registered_by_name"),
        registered_by_position=header.get("registered_by_position"),
        registered_on=header.get("registered_on"),
        unmet_requirement=header.get("unmet_requirement"),
        finding=header.get("finding"),
        has_photos=_yes(header.get("has_photos")),
        has_history=_yes(header.get("has_history")) or (_number(header.get("attempt")) or 1) > 1,
        analysis_method=method if causes else None,
        legacy={"source": SOURCE, "rows": action.source_rows, "issues": action.issues},
        corrections=corrections,
        causes=causes,
        activities=activities,
    )


async def import_matrix(
    content: bytes, repository: ActionPlanRepository, apply: bool
) -> ImportReport:
    """Read the workbook and, with ``apply``, create the actions that are not there yet."""

    actions = group_actions(read_rows(content))
    report = ImportReport(actions=len(actions))
    for action in actions:
        report.rows += len(action.source_rows)
        report.warnings += [f"{action.code}: {issue}" for issue in action.issues]
        plan = await to_plan(action, repository)
        if isinstance(plan, str):
            report.skipped.append(f"{action.code}: {plan}")
            continue
        report.corrections += len(plan.corrections)
        report.causes += len(plan.causes)
        report.activities += len(plan.activities)
        if await repository.code_exists(plan.code):
            report.existing.append(plan.code)
            continue
        if apply:
            await repository.create(
                plan,
                PlanEvent(
                    uuid4(),
                    plan.id,
                    "imported",
                    None,
                    None,
                    {"source": SOURCE, "rows": action.source_rows},
                    datetime.now(UTC),
                ),
            )
        report.created.append(plan.code)
    return report


def summary(report: ImportReport, apply: bool) -> Sequence[str]:
    """Lines to print."""

    mode = "APLICADO" if apply else "SIMULACIÓN (usa --apply para escribir)"
    lines = [
        f"Importación {mode}",
        f"Acciones en el libro: {report.actions} ({report.rows} filas)",
        f"Correcciones: {report.corrections} · Causas: {report.causes} · "
        f"Actividades: {report.activities}",
        f"Nuevas: {len(report.created)} · Ya estaban: {len(report.existing)} · "
        f"Omitidas: {len(report.skipped)}",
    ]
    lines += [f"  Omitida {line}" for line in report.skipped]
    lines += [f"  Aviso {line}" for line in report.warnings]
    return lines
