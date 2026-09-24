"""PostgreSQL implementation of the user repository."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.identity.domain.entities import User, VerifiedIdentity, normalize_email
from app.modules.identity.infrastructure.models import UserModel


def _to_entity(model: UserModel) -> User:
    return User(
        id=model.id,
        email=model.email,
        display_name=model.display_name,
        entra_object_id=model.entra_object_id,
        last_seen_at=model.last_seen_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyUserRepository:
    """Persist platform users; each operation owns its transaction."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared engine and session factory.
        """

        self.database = database

    async def upsert_from_identity(self, identity: VerifiedIdentity) -> User:
        """Create or refresh the user matching a verified identity.

        Args:
            identity (VerifiedIdentity): Verified claims. Matching is by Entra
                object id first and by email second, so a stub created by
                email is linked on the user's first sign-in.

        Returns:
            User: The stored user with ``last_seen_at`` set to now.
        """

        email = normalize_email(identity.email)
        async with self.database.session_factory() as session:
            conditions = [UserModel.email == email]
            if identity.entra_object_id:
                conditions.append(UserModel.entra_object_id == identity.entra_object_id)
            result = await session.execute(
                select(UserModel)
                .where(or_(*conditions))
                .order_by(UserModel.entra_object_id.is_(None))
                .limit(1)
            )
            model = result.scalar_one_or_none()
            if model is None:
                model = UserModel(email=email)
                session.add(model)
            model.email = email
            if identity.display_name:
                model.display_name = identity.display_name
            if identity.entra_object_id:
                model.entra_object_id = identity.entra_object_id
            model.last_seen_at = func.now()
            await session.commit()
            await session.refresh(model)
            return _to_entity(model)

    async def ensure_by_email(self, email: str) -> User:
        """Return the user with this email, creating a stub when missing.

        Args:
            email (str): Normalized email address.

        Returns:
            User: Existing or newly created user.
        """

        normalized = normalize_email(email)
        async with self.database.session_factory() as session:
            await session.execute(
                insert(UserModel)
                .values(email=normalized)
                .on_conflict_do_nothing(index_elements=[UserModel.email])
            )
            await session.commit()
            result = await session.execute(select(UserModel).where(UserModel.email == normalized))
            return _to_entity(result.scalar_one())

    async def get_many(self, user_ids: Iterable[UUID]) -> dict[UUID, User]:
        """Load several users at once.

        Args:
            user_ids (Iterable[UUID]): Identifiers to load; unknown ones are skipped.

        Returns:
            dict[UUID, User]: Found users keyed by identifier.
        """

        ids = {user_id for user_id in user_ids if user_id is not None}
        if not ids:
            return {}
        async with self.database.session_factory() as session:
            result = await session.execute(select(UserModel).where(UserModel.id.in_(ids)))
            return {model.id: _to_entity(model) for model in result.scalars()}
