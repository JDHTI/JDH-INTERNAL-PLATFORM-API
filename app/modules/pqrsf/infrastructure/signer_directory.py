"""Who signs a PQRSF document, from the employee directory (and the user when not synced)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.pqrsf.domain.improvement import Signer
from app.modules.pqrsf.domain.ports import PeopleDirectory


class EmployeeLookup(Protocol):
    """The directory repository (``get_employee_by_user``)."""

    async def get_employee_by_user(self, user_id: UUID):  # noqa: ANN201 - directory Employee
        """The employee of a platform user."""
        ...


class DirectorySigner:
    """Name, position, email and mobile of a user, as the directory shows them."""

    def __init__(self, employees: EmployeeLookup, people: PeopleDirectory) -> None:
        """Create the adapter.

        Args:
            employees (EmployeeLookup): The directory, synced from Entra.
            people (PeopleDirectory): Platform users, for whoever is not in the directory.
        """

        self.employees = employees
        self.people = people

    async def signer(self, user_id: UUID) -> Signer | None:
        """The signer; ``None`` when the user does not exist."""

        employee = await self.employees.get_employee_by_user(user_id)
        if employee is not None:
            phone = employee.mobile_phone or employee.landline_phone or None
            return Signer(
                user_id,
                employee.full_name,
                employee.job_title or employee.position or None,
                employee.email,
                phone,
            )
        user = (await self.people.get_users([user_id])).get(user_id)
        if user is None:
            return None
        return Signer(user_id, user.display_name or user.email, None, user.email, None)

    async def names(self, user_ids: Sequence[UUID]) -> dict[UUID, str]:
        """Name of each platform user that exists (the email when it has none)."""

        if not user_ids:
            return {}
        users = await self.people.get_users(user_ids)
        return {uid: user.display_name or user.email for uid, user in users.items()}
