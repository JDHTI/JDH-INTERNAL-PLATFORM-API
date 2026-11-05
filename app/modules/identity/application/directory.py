"""User lookups offered to other modules, and the principal of a request."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from app.core.errors import NotFoundError
from app.modules.identity.domain.principal import DirectoryUser, Principal
from app.modules.identity.domain.roles import is_platform_admin
from app.modules.identity.infrastructure.models import UserModel
from app.modules.identity.infrastructure.repository import IdentityRepository


def principal_from_user(user: UserModel) -> Principal:
    """Build the request principal from an authenticated local user.

    Args:
        user (UserModel): User resolved by the session, with its role loaded.

    Returns:
        Principal: Identity other modules authorize against.
    """

    return Principal(
        user_id=user.id,
        email=user.email or user.name,
        display_name=user.name,
        is_platform_admin=is_platform_admin(user.role.code),
        role=user.role.code,
    )


def _directory_user(user: UserModel) -> DirectoryUser:
    return DirectoryUser(
        id=user.id, email=user.email, display_name=user.name, is_active=user.is_active
    )


class UserDirectory:
    """Resolve platform users for modules that must not read identity tables."""

    def __init__(self, repository: IdentityRepository) -> None:
        """Create the directory.

        Args:
            repository (IdentityRepository): Identity persistence adapter.
        """

        self.repository = repository

    async def require_user(self, email: str) -> DirectoryUser:
        """Return the user with this email.

        Users are created by Entra sign-in or by the directory sync (both carry a
        tenant and object id), so a person must be in one of them before anyone can
        reference them.

        Args:
            email (str): Email address of the person.

        Returns:
            DirectoryUser: The existing user.

        Raises:
            NotFoundError: When nobody has that email in the directory or the platform.
        """

        user = await self.repository.get_user_by_email(email)
        if user is None:
            raise NotFoundError(
                f"No hay un usuario con el correo {email.strip()}: debe estar en el "
                "directorio de empleados o haber iniciado sesión en la plataforma"
            )
        return _directory_user(user)

    async def get_users(self, user_ids: Iterable[UUID]) -> dict[UUID, DirectoryUser]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Identifiers to load.

        Returns:
            dict[UUID, DirectoryUser]: Found users keyed by identifier.
        """

        users = await self.repository.get_users_by_ids(user_ids)
        return {user.id: _directory_user(user) for user in users}

    async def active_emails(self) -> dict[str, UUID]:
        """Lowercase email -> id of every active user that has one.

        Returns:
            dict[str, UUID]: The index, for matching other systems by email.
        """

        return await self.repository.active_user_emails()

    async def search_users(self, query: str, limit: int = 20) -> list[DirectoryUser]:
        """Find active users by name or email, for people pickers.

        Args:
            query (str): Text contained in the name or email.
            limit (int): Maximum number of users to return.

        Returns:
            list[DirectoryUser]: Matching active users ordered by name.
        """

        users = await self.repository.search_active_users(query, limit)
        return [_directory_user(user) for user in users]
