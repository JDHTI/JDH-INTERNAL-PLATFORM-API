"""Endpoints of Ajuste de costos (``/cost-settings``, module ``ajuste-costos``)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from app.app_container import ApplicationContainer
from app.modules.areas.presentation.dependencies import require_permission
from app.modules.cost_settings.application.service import CostSettingsService
from app.modules.cost_settings.domain.entities import BranchRule, Change, Parameter
from app.modules.identity.domain.principal import Principal
from app.modules.identity.presentation.principal import get_current_principal

MODULE = "ajuste-costos"

router = APIRouter()
_GATE = Depends(require_permission(MODULE))
_service = Depends(Provide[ApplicationContainer.v1_container.cost_settings_service])
_principal = Depends(get_current_principal)


class PermissionsResponse(BaseModel):
    can_read: bool
    can_manage: bool


class ParameterResponse(BaseModel):
    key: str
    label: str
    description: str
    # ``incremento`` raises the cost by a fraction (0.004 is 0,4 % more, 0 the standard cost;
    # 2026-11-27, it was a factor ``factor`` 1.004); ``margen`` is a fraction of margin (0.01
    # is one point).
    unit: Literal["incremento", "margen"]
    minimum: Decimal
    maximum: Decimal
    # The list family of a cost increment.
    family: str | None
    value: Decimal
    default: Decimal
    # True while BI has not set it: the value is the default.
    is_default: bool
    updated_by: str | None
    updated_at: datetime | None


class BranchRuleResponse(BaseModel):
    id: UUID
    supplier_nit: str
    supplier_name: str | None
    general_branch: str
    # Operation center -> the supplier's branch that quotes for it.
    center_branches: dict[str, str]
    is_active: bool
    note: str | None
    created_by: str | None
    created_at: datetime | None
    updated_by: str | None
    updated_at: datetime | None


class ChangeResponse(BaseModel):
    id: UUID
    entity: Literal["parametro", "regla_sucursal"]
    ref: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    note: str | None
    actor: str
    at: datetime


class OverviewResponse(BaseModel):
    parameters: list[ParameterResponse]
    branch_rules: list[BranchRuleResponse]
    changes: list[ChangeResponse]
    can_manage: bool


class ParametersRequest(BaseModel):
    """New values by key (only those that change); ``note`` says why."""

    values: dict[str, Decimal] = Field(min_length=1, max_length=20)
    note: str | None = Field(default=None, max_length=500)


class CenterBranches(BaseModel):
    center_branches: dict[str, str] = Field(min_length=1, max_length=40)


class CreateRuleRequest(CenterBranches):
    supplier_nit: str = Field(min_length=5, max_length=20)
    supplier_name: str | None = Field(default=None, max_length=200)
    general_branch: str = Field(default="001", min_length=1, max_length=10)
    is_active: bool = True
    note: str | None = Field(default=None, max_length=500)


class UpdateRuleRequest(BaseModel):
    """Only the keys sent change."""

    general_branch: str | None = Field(default=None, min_length=1, max_length=10)
    center_branches: dict[str, str] | None = Field(default=None, min_length=1, max_length=40)
    supplier_name: str | None = Field(default=None, max_length=200)
    is_active: bool | None = None
    note: str | None = Field(default=None, max_length=500)


def _parameter(item: Parameter) -> ParameterResponse:
    spec = item.spec
    return ParameterResponse(
        key=spec.key,
        label=spec.label,
        description=spec.description,
        unit=spec.unit,  # type: ignore[arg-type]
        minimum=spec.minimum,
        maximum=spec.maximum,
        family=spec.family,
        value=item.value,
        default=item.default,
        is_default=item.is_default,
        updated_by=item.updated_by,
        updated_at=item.updated_at,
    )


def _rule(rule: BranchRule) -> BranchRuleResponse:
    return BranchRuleResponse(
        id=rule.id,
        supplier_nit=rule.supplier_nit,
        supplier_name=rule.supplier_name,
        general_branch=rule.general_branch,
        center_branches=dict(rule.center_branches),
        is_active=rule.is_active,
        note=rule.note,
        created_by=rule.created_by,
        created_at=rule.created_at,
        updated_by=rule.updated_by,
        updated_at=rule.updated_at,
    )


def _change(change: Change) -> ChangeResponse:
    return ChangeResponse(
        id=change.id,
        entity=change.entity,  # type: ignore[arg-type]
        ref=change.ref,
        before=change.before,
        after=change.after,
        note=change.note,
        actor=change.actor,
        at=change.at,
    )


@router.get("/permissions", response_model=PermissionsResponse, summary="What the caller may do")
@inject
async def permissions(
    principal: Principal = _principal, service: CostSettingsService = _service
) -> PermissionsResponse:
    """Open to everyone: ``false`` when the caller has no access (no 403)."""

    return PermissionsResponse(
        can_read=await service.can_read(principal), can_manage=await service.can_manage(principal)
    )


@router.get(
    "",
    dependencies=[_GATE],
    response_model=OverviewResponse,
    summary="The cost parameters, the supplier branch rules and their latest changes",
)
@inject
async def overview(
    principal: Principal = _principal, service: CostSettingsService = _service
) -> OverviewResponse:
    """Each parameter in force (its default while BI has not set it), every supplier branch
    rule and the latest 50 changes."""

    result = await service.overview(principal)
    return OverviewResponse(
        parameters=[_parameter(item) for item in result.parameters],
        branch_rules=[_rule(rule) for rule in result.rules],
        changes=[_change(item) for item in result.changes],
        can_manage=result.can_manage,
    )


@router.get(
    "/changes",
    dependencies=[_GATE],
    response_model=list[ChangeResponse],
    summary="History of the changes",
)
@inject
async def changes(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = _principal,
    service: CostSettingsService = _service,
) -> list[ChangeResponse]:
    return [_change(item) for item in await service.changes(principal, limit)]


@router.put(
    "/parameters",
    dependencies=[_GATE],
    response_model=list[ParameterResponse],
    summary="Change some parameters",
)
@inject
async def set_parameters(
    body: ParametersRequest,
    principal: Principal = _principal,
    service: CostSettingsService = _service,
) -> list[ParameterResponse]:
    """``manage`` only. Each value within its range (factors 1 to 1.1, threshold 0 to 0.1).
    The quoter takes them in the next quotation built or refreshed. 422 on an unknown key, a
    value out of range or nothing that changes."""

    return [
        _parameter(item) for item in await service.set_parameters(principal, body.values, body.note)
    ]


@router.post(
    "/branch-rules",
    dependencies=[_GATE],
    response_model=BranchRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="A supplier's branch for each operation center",
)
@inject
async def create_rule(
    body: CreateRuleRequest,
    principal: Principal = _principal,
    service: CostSettingsService = _service,
) -> BranchRuleResponse:
    """``manage`` only. ``center_branches`` maps a C.O. to the supplier's branch that quotes
    for it; the rest of the C.O. take ``general_branch``. 409 when the supplier already has a
    rule; 422 on a code that is not one."""

    return _rule(
        await service.create_rule(
            principal,
            supplier_nit=body.supplier_nit,
            supplier_name=body.supplier_name,
            general_branch=body.general_branch,
            center_branches=body.center_branches,
            is_active=body.is_active,
            note=body.note,
        )
    )


@router.patch(
    "/branch-rules/{rule_id}",
    dependencies=[_GATE],
    response_model=BranchRuleResponse,
    summary="Change or turn off a supplier branch rule",
)
@inject
async def update_rule(
    rule_id: UUID,
    body: UpdateRuleRequest,
    principal: Principal = _principal,
    service: CostSettingsService = _service,
) -> BranchRuleResponse:
    """``manage`` only. ``is_active: false`` turns it off: the supplier is quoted, as before,
    at its highest quotation in force."""

    return _rule(
        await service.update_rule(
            principal,
            rule_id,
            general_branch=body.general_branch,
            center_branches=body.center_branches,
            supplier_name=body.supplier_name,
            is_active=body.is_active,
            note=body.note,
        )
    )


@router.delete(
    "/branch-rules/{rule_id}",
    dependencies=[_GATE],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a supplier branch rule",
)
@inject
async def delete_rule(
    rule_id: UUID,
    principal: Principal = _principal,
    service: CostSettingsService = _service,
) -> Response:
    """``manage`` only; the history keeps it."""

    await service.delete_rule(principal, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
