"""Identity use cases: resolve the caller and look users up for other modules."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from app.core.errors import InvalidInputError, UnauthenticatedError
from app.modules.identity.domain.entities import (
    Principal,
    User,
    VerifiedIdentity,
    normalize_email,
)
from app.modules.identity.domain.ports import TokenVerifier, UserRepository


class IdentityService:
    """Turn credentials into principals and expose users as a directory."""

    def __init__(
        self,
        repository: UserRepository,
        token_verifier: TokenVerifier,
        admin_emails: Iterable[str],
        dev_bypass: bool = False,
        dev_user_email: str = "",
    ) -> None:
        """Create the service with its persistence and verification ports.

        Args:
            repository (UserRepository): Port that stores platform users.
            token_verifier (TokenVerifier): Port that validates bearer tokens.
            admin_emails (Iterable[str]): Emails granted platform administration.
            dev_bypass (bool): Accept requests without a token (dev only; the
                settings refuse it in other environments).
            dev_user_email (str): Identity used by the bypass when the request
                names none.
        """

        self.repository = repository
        self.token_verifier = token_verifier
        self.admin_emails = frozenset(normalize_email(e) for e in admin_emails)
        self.dev_bypass = dev_bypass
        self.dev_user_email = normalize_email(dev_user_email)

    async def authenticate(self, token: str | None, dev_email: str | None = None) -> Principal:
        """Resolve the principal behind a request.

        Args:
            token (str | None): Bearer token from the Authorization header.
            dev_email (str | None): ``X-Dev-User-Email`` header, honored only
                when the dev bypass is enabled and no token is present.

        Returns:
            Principal: The authenticated caller, provisioned on first sight.
        """

        if token:
            identity = await self.token_verifier.verify(token)
        elif self.dev_bypass:
            email = normalize_email(dev_email or self.dev_user_email)
            if "@" not in email:
                raise UnauthenticatedError("X-Dev-User-Email no es un correo válido")
            identity = VerifiedIdentity(email=email)
        else:
            raise UnauthenticatedError("Falta el token de acceso")

        user = await self.repository.upsert_from_identity(identity)
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_platform_admin=user.email in self.admin_emails,
        )

    async def ensure_user(self, email: str) -> User:
        """Return the user with this email, creating a stub when missing.

        Args:
            email (str): Email address, in any case.

        Returns:
            User: Existing or newly created user.
        """

        normalized = normalize_email(email)
        if "@" not in normalized:
            raise InvalidInputError("Correo inválido")
        return await self.repository.ensure_by_email(normalized)

    async def get_users(self, user_ids: Iterable[UUID]) -> dict[UUID, User]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Identifiers to load.

        Returns:
            dict[UUID, User]: Found users keyed by identifier.
        """

        return await self.repository.get_many(user_ids)
