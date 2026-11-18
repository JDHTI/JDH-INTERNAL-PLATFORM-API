"""Use cases of the supplier support profiles."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.application import cells
from app.modules.supplier_supports.application import normalize as n
from app.modules.supplier_supports.domain import fields
from app.modules.supplier_supports.domain.ports import ManagerPolicy, MasterLookup
from app.modules.supplier_supports.domain.profiles import (
    DEFAULT_FIELDS,
    MAX_HEADERS_PER_FIELD,
    Profile,
    ProfileData,
    ProfileRepository,
)


def normalize_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the defaults of a profile and turn them into JSON-safe values.

    Empty values are dropped. Closed fields must hold a valid value; percentages are stored as
    fraction strings and scopes as upper-case lists.

    Raises:
        InvalidInputError: If a field is not allowed or a value is not valid for it.
    """

    clean: dict[str, Any] = {}
    for name, value in raw.items():
        if name not in DEFAULT_FIELDS:
            raise InvalidInputError(
                f"«{name}» no puede ser un valor por defecto "
                f"(permitidos: {', '.join(DEFAULT_FIELDS)})"
            )
        if value is None or (isinstance(value, (str, list, tuple)) and not value):
            continue
        spec = fields.FIELDS[name]
        if spec.type == fields.CHOICE:
            chosen = cells.choice(value, spec.choices)
            if chosen is None:
                raise InvalidInputError(
                    f"{name} inválido: {value!r} (válidos: {', '.join(spec.choices)})"
                )
            clean[name] = chosen
        elif spec.type == fields.FRACTION:
            try:
                clean[name] = str(cells.percent(value))
            except ValueError as exc:
                raise InvalidInputError(f"{name}: {exc}") from exc
        elif spec.type == fields.LIST:
            if scope := cells.split_list(value):
                clean[name] = scope
        elif spec.type == fields.FLAG:
            try:
                clean[name] = cells.flag(value)
            except ValueError as exc:
                raise InvalidInputError(f"{name}: {exc}") from exc
        else:
            text = n.clean_text(value)
            if text and spec.max_length and len(text) > spec.max_length:
                raise InvalidInputError(f"{name} supera {spec.max_length} caracteres")
            if text:
                clean[name] = text
    return clean


def normalize_column_map(raw: Mapping[str, Any]) -> dict[str, list[str]]:
    """Validate which headers of the supplier's file feed each field.

    Raises:
        InvalidInputError: If a field is unknown or its headers are not a short list of texts.
    """

    clean: dict[str, list[str]] = {}
    for name, headers in raw.items():
        if name not in fields.FIELDS:
            raise InvalidInputError(f"Campo desconocido en el mapeo de columnas: {name}")
        values = [headers] if isinstance(headers, str) else headers
        if not isinstance(values, (list, tuple)):
            raise InvalidInputError(f"El mapeo de {name} debe ser un texto o una lista de textos")
        names = list(dict.fromkeys(text for item in values if (text := n.clean_text(item))))
        if len(names) > MAX_HEADERS_PER_FIELD or any(len(text) > 100 for text in names):
            raise InvalidInputError(
                f"El mapeo de {name} admite hasta {MAX_HEADERS_PER_FIELD} encabezados de 100 letras"
            )
        if names:
            clean[name] = names
    return clean


class SupportProfileService:
    """Read and edit the profile of each commercial supplier."""

    def __init__(
        self, repository: ProfileRepository, masters: MasterLookup, managers: ManagerPolicy
    ) -> None:
        """Create the service.

        Args:
            repository (ProfileRepository): Profile persistence port.
            masters (MasterLookup): Resolves the commercial supplier by code.
            managers (ManagerPolicy): Decides who may write.
        """

        self.repository = repository
        self.masters = masters
        self.managers = managers

    async def _supplier_id(self, code: str):
        found = await self.masters.commercial_suppliers_by_code([code.strip()])
        supplier_id = found.get(code.strip())
        if supplier_id is None:
            raise NotFoundError(f"Proveedor comercial desconocido: {code}")
        return supplier_id

    async def list(self) -> list[Profile]:
        """Every profile, by supplier name."""

        return await self.repository.list()

    async def find(self, code: str) -> Profile | None:
        """The profile of a supplier code, or ``None`` when it has none."""

        return await self.repository.get(await self._supplier_id(code))

    async def get(self, code: str) -> Profile:
        """The profile of a supplier code.

        Raises:
            NotFoundError: If the supplier is unknown or has no profile.
        """

        profile = await self.find(code)
        if profile is None:
            raise NotFoundError("El proveedor no tiene perfil de apoyos")
        return profile

    async def save(
        self,
        principal: Principal,
        code: str,
        *,
        defaults: Mapping[str, Any],
        column_map: Mapping[str, Any],
        sheet: str | None,
        notes: str | None,
        expected_version: int | None,
    ) -> Profile:
        """Create the profile of a supplier, or replace it when ``expected_version`` is given.

        Raises:
            ForbiddenError: If the caller is not a manager.
            NotFoundError: If the supplier is unknown.
            ConflictError: If the profile exists and no version is sent, or it changed.
            InvalidInputError: If a default or a column mapping is not valid.
        """

        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar apoyos de proveedores")
        supplier_id = await self._supplier_id(code)
        data = ProfileData(
            defaults=normalize_defaults(defaults),
            column_map=normalize_column_map(column_map),
            sheet=n.clean_text(sheet),
            notes=n.clean_text(notes),
        )
        now = datetime.now(UTC)
        if expected_version is None:
            return await self.repository.create(uuid4(), supplier_id, data, principal.email, now)
        if await self.repository.get(supplier_id) is None:
            raise ConflictError("El proveedor no tiene perfil; créalo sin versión")
        return await self.repository.update(
            supplier_id, data, expected_version, principal.email, now
        )

    async def delete(self, principal: Principal, code: str) -> None:
        """Remove a profile; the supports already created are not touched."""

        if not await self.managers.is_manager(principal):
            raise ForbiddenError("No tienes permiso para gestionar apoyos de proveedores")
        if not await self.repository.delete(await self._supplier_id(code)):
            raise NotFoundError("El proveedor no tiene perfil de apoyos")


__all__ = ["SupportProfileService", "normalize_column_map", "normalize_defaults"]
