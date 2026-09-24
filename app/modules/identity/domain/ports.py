"""Contracts required by the identity use cases."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.entities import User, VerifiedIdentity


class UserRepository(Protocol):
    """Define persistence operations for platform users."""

    async def upsert_from_identity(self, identity: VerifiedIdentity) -> User:
        """Create or refresh the user matching a verified identity.

        Args:
            identity (VerifiedIdentity): Verified claims. Matching is by Entra
                object id first and by email second, so a stub created by
                email is linked on the user's first sign-in.

        Returns:
            User: The stored user with ``last_seen_at`` set to now.
        """
        ...

    async def ensure_by_email(self, email: str) -> User:
        """Return the user with this email, creating a stub when missing.

        Args:
            email (str): Normalized email address.

        Returns:
            User: Existing or newly created user.
        """
        ...

    async def get_many(self, user_ids: Iterable[UUID]) -> dict[UUID, User]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Identifiers to load; unknown ones are skipped.

        Returns:
            dict[UUID, User]: Found users keyed by identifier.
        """
        ...


class TokenVerifier(Protocol):
    """Verify a bearer credential issued by the identity provider."""

    async def verify(self, token: str) -> VerifiedIdentity:
        """Validate a token and extract the caller's identity.

        Args:
            token (str): Raw bearer token.

        Returns:
            VerifiedIdentity: Claims of the authenticated caller.
        """
        ...
