"""Manage the subjects and causes the public form classifies requests with."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.pqrsf.domain.entities import (
    CAUSE_ROUTES,
    REQUEST_TYPE_CODES,
    Cause,
    Subject,
    clean_optional,
)
from app.modules.pqrsf.domain.ports import CaseRepository, ManagerPolicy, PqrsfRepository
from app.modules.pqrsf.domain.tracking import clean_code

# Longest description of a subject or a cause: one visible line under the option.
MAX_OPTION_DESCRIPTION = 120


class ClassificationService:
    """Let PQRSF managers keep the subject and cause catalogs without a deployment.

    Nothing is deleted: a subject or cause that is no longer used is deactivated, so the
    requests that picked it keep pointing at it.
    """

    def __init__(
        self, requests: PqrsfRepository, cases: CaseRepository, managers: ManagerPolicy
    ) -> None:
        """Create the service.

        Args:
            requests (PqrsfRepository): Stores the subjects and causes.
            cases (CaseRepository): Resolves the dependencies a subject or cause names.
            managers (ManagerPolicy): Says who manages PQRSF.
        """

        self.requests = requests
        self.cases = cases
        self.managers = managers

    async def list_subjects(self, principal: Principal, include_inactive: bool) -> list[Subject]:
        """List subjects with their causes, including disabled ones for managers.

        Args:
            principal (Principal): The caller.
            include_inactive (bool): Include disabled subjects and causes (managers only).

        Returns:
            list[Subject]: Subjects in display order.
        """

        inactive = include_inactive and await self.managers.is_manager(principal)
        return await self.requests.list_subjects(active_only=not inactive)

    async def create_subject(
        self,
        principal: Principal,
        *,
        code: str,
        name: str,
        request_type_codes: list[str],
        description: str | None = None,
        dependency_id: UUID | None = None,
        is_other: bool = False,
        sort_order: int = 0,
    ) -> Subject:
        """Add a subject.

        Args:
            principal (Principal): The caller, who must manage PQRSF.
            code (str): Stable code.
            name (str): Visible name.
            request_type_codes (list[str]): Codes of the types that offer it.
            description (str | None): One short line shown under the option.
            dependency_id (UUID | None): Who attends it when no cause names one.
            is_other (bool): Whether it is the "Otro" option (description required).
            sort_order (int): Position on the form.

        Returns:
            Subject: The new subject, without causes.
        """

        await self._require_manager(principal)
        code = clean_code(code)
        subjects = await self.requests.list_subjects(active_only=False)
        if any(subject.code == code for subject in subjects):
            raise ConflictError("Ya existe un asunto con ese código")
        subject = Subject(
            id=uuid4(),
            code=code,
            name=_name(name),
            description=_description(description),
            request_type_codes=_type_codes(request_type_codes),
            dependency_id=await self._dependency(dependency_id),
            is_other=is_other,
            sort_order=sort_order,
        )
        return await self.requests.save_subject(subject)

    async def update_subject(
        self,
        subject_id: UUID,
        principal: Principal,
        *,
        fields: frozenset[str],
        name: str | None = None,
        description: str | None = None,
        request_type_codes: list[str] | None = None,
        dependency_id: UUID | None = None,
        is_other: bool | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> Subject:
        """Edit a subject; its code is immutable.

        Args:
            subject_id (UUID): Subject to change.
            principal (Principal): The caller, who must manage PQRSF.
            fields (frozenset[str]): Names of the fields to apply.
            name (str | None): Visible name.
            description (str | None): Short line; ``None`` clears it.
            request_type_codes (list[str] | None): Codes of the types that offer it.
            dependency_id (UUID | None): Dependency; ``None`` clears it.
            is_other (bool | None): Whether it is the "Otro" option.
            sort_order (int | None): Position on the form.
            is_active (bool | None): Offer it on the form.

        Returns:
            Subject: The updated subject with its causes.
        """

        await self._require_manager(principal)
        subject = await self._subject(subject_id)
        changes: dict = {}
        if "name" in fields and name is not None:
            changes["name"] = _name(name)
        if "description" in fields:
            changes["description"] = _description(description)
        if "request_type_codes" in fields and request_type_codes is not None:
            changes["request_type_codes"] = _type_codes(request_type_codes)
        if "dependency_id" in fields:
            changes["dependency_id"] = await self._dependency(dependency_id)
        if "is_other" in fields and is_other is not None:
            changes["is_other"] = is_other
        if "sort_order" in fields and sort_order is not None:
            changes["sort_order"] = sort_order
        if "is_active" in fields and is_active is not None:
            changes["is_active"] = is_active
        await self.requests.save_subject(replace(subject, **changes))
        return await self._subject(subject_id)

    async def create_cause(
        self,
        subject_id: UUID,
        principal: Principal,
        *,
        code: str,
        name: str,
        description: str | None = None,
        dependency_id: UUID | None = None,
        route: str = "none",
        requires_product: bool = False,
        allows_product: bool = False,
        is_other: bool = False,
        sort_order: int = 0,
    ) -> Cause:
        """Add a cause to a subject.

        Args:
            subject_id (UUID): Subject it belongs to.
            principal (Principal): The caller, who must manage PQRSF.
            code (str): Stable code, unique among all causes.
            name (str): Visible name.
            description (str | None): One short line shown under the option.
            dependency_id (UUID | None): Dependency that attends the case.
            route (str): ``supplier``, ``internal`` or ``none``.
            requires_product (bool): At least one product line is required.
            allows_product (bool): The form shows the product lines.
            is_other (bool): Whether it is the "Otro" option (description required).
            sort_order (int): Position on the form.

        Returns:
            Cause: The new cause.
        """

        await self._require_manager(principal)
        await self._subject(subject_id)
        code = clean_code(code)
        if any(
            cause.code == code
            for subject in await self.requests.list_subjects(active_only=False)
            for cause in subject.causes
        ):
            raise ConflictError("Ya existe una causal con ese código")
        cause = Cause(
            id=uuid4(),
            subject_id=subject_id,
            code=code,
            name=_name(name),
            description=_description(description),
            dependency_id=await self._dependency(dependency_id),
            route=_route(route),
            requires_product=requires_product,
            allows_product=allows_product or requires_product,
            is_other=is_other,
            sort_order=sort_order,
        )
        return await self.requests.save_cause(cause)

    async def update_cause(
        self,
        cause_id: UUID,
        principal: Principal,
        *,
        fields: frozenset[str],
        name: str | None = None,
        description: str | None = None,
        dependency_id: UUID | None = None,
        route: str | None = None,
        requires_product: bool | None = None,
        allows_product: bool | None = None,
        is_other: bool | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> Cause:
        """Edit a cause; its code and subject are immutable.

        Args:
            cause_id (UUID): Cause to change.
            principal (Principal): The caller, who must manage PQRSF.
            fields (frozenset[str]): Names of the fields to apply.
            name (str | None): Visible name.
            description (str | None): Short line; ``None`` clears it.
            dependency_id (UUID | None): Dependency; ``None`` clears it.
            route (str | None): ``supplier``, ``internal`` or ``none``.
            requires_product (bool | None): At least one product line is required.
            allows_product (bool | None): The form shows the product lines.
            is_other (bool | None): Whether it is the "Otro" option.
            sort_order (int | None): Position on the form.
            is_active (bool | None): Offer it on the form.

        Returns:
            Cause: The updated cause.
        """

        await self._require_manager(principal)
        cause = next(
            (
                item
                for subject in await self.requests.list_subjects(active_only=False)
                for item in subject.causes
                if item.id == cause_id
            ),
            None,
        )
        if cause is None:
            raise NotFoundError("Causal no encontrada")
        changes: dict = {}
        if "name" in fields and name is not None:
            changes["name"] = _name(name)
        if "description" in fields:
            changes["description"] = _description(description)
        if "dependency_id" in fields:
            changes["dependency_id"] = await self._dependency(dependency_id)
        if "route" in fields and route is not None:
            changes["route"] = _route(route)
        if "requires_product" in fields and requires_product is not None:
            changes["requires_product"] = requires_product
        if "allows_product" in fields and allows_product is not None:
            changes["allows_product"] = allows_product
        if "is_other" in fields and is_other is not None:
            changes["is_other"] = is_other
        if "sort_order" in fields and sort_order is not None:
            changes["sort_order"] = sort_order
        if "is_active" in fields and is_active is not None:
            changes["is_active"] = is_active
        updated = replace(cause, **changes)
        if updated.requires_product and not updated.allows_product:
            raise InvalidInputError("Una causal que exige productos debe admitirlos")
        return await self.requests.save_cause(updated)

    async def _require_manager(self, principal: Principal) -> None:
        if not await self.managers.is_manager(principal):
            raise ForbiddenError("Solo quien gestiona PQRSF puede hacer esta operación")

    async def _subject(self, subject_id: UUID) -> Subject:
        subject = next(
            (
                item
                for item in await self.requests.list_subjects(active_only=False)
                if item.id == subject_id
            ),
            None,
        )
        if subject is None:
            raise NotFoundError("Asunto no encontrado")
        return subject

    async def _dependency(self, dependency_id: UUID | None) -> UUID | None:
        if dependency_id is None:
            return None
        if await self.cases.get_dependency(dependency_id) is None:
            raise InvalidInputError("La dependencia no existe")
        return dependency_id


def _name(value: str) -> str:
    cleaned = clean_optional(value, "nombre", 120)
    if cleaned is None:
        raise InvalidInputError("El nombre no puede estar vacío")
    return cleaned


def _description(value: str | None) -> str | None:
    return clean_optional(value, "descripción", MAX_OPTION_DESCRIPTION)


def _type_codes(values: list[str]) -> tuple[str, ...]:
    codes = tuple(dict.fromkeys(value.strip().lower() for value in values))
    if not codes:
        raise InvalidInputError("Indica al menos un tipo de solicitud que ofrezca el asunto")
    unknown = [code for code in codes if code not in REQUEST_TYPE_CODES]
    if unknown:
        raise InvalidInputError(f"Tipo de solicitud desconocido: {unknown[0]}")
    return codes


def _route(value: str) -> str:
    if value not in CAUSE_ROUTES:
        raise InvalidInputError("La ruta debe ser 'supplier', 'internal' o 'none'")
    return value
