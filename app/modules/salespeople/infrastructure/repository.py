"""PostgreSQL store of the salesperson links."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import Database
from app.modules.salespeople.domain.entities import SalespersonLink
from app.modules.salespeople.infrastructure.models import SalespersonLinkModel as L


def _link(model: L) -> SalespersonLink:
    return SalespersonLink(
        id=model.id,
        kind=model.kind,
        code=model.code,
        user_id=model.user_id,
        source=model.source,
        created_by_id=model.created_by_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class SqlAlchemyLinkRepository:
    """Who holds each salesperson or supervisor code (one row per kind and code)."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Application database.
        """

        self.database = database

    async def list_for_user(self, user_id: UUID) -> list[SalespersonLink]:
        """The links of a user, supervisors first."""

        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(L).where(L.user_id == user_id).order_by(L.kind.desc(), L.code)
            )
            return [_link(row) for row in rows]

    async def list_all(self) -> list[SalespersonLink]:
        """Every link, excluded codes included."""

        async with self.database.session_factory() as session:
            return [_link(row) for row in await session.scalars(select(L))]

    async def get(self, link_id: UUID) -> SalespersonLink | None:
        """One link by id."""

        async with self.database.session_factory() as session:
            model = await session.get(L, link_id)
            return _link(model) if model else None

    async def get_by_code(self, kind: str, code: str) -> SalespersonLink | None:
        """The link of a code, if any."""

        async with self.database.session_factory() as session:
            model = await session.scalar(select(L).where(L.kind == kind, L.code == code))
            return _link(model) if model else None

    async def put(
        self,
        kind: str,
        code: str,
        user_id: UUID | None,
        source: str,
        created_by_id: UUID | None,
    ) -> SalespersonLink:
        """Create or replace the link of a code.

        Args:
            kind (str): ``seller`` or ``supervisor``.
            code (str): SIESA code.
            user_id (UUID | None): Holder; ``None`` for an excluded code.
            source (str): ``auto``, ``manual`` or ``excluded``.
            created_by_id (UUID | None): Who made the change.

        Returns:
            SalespersonLink: The stored link.
        """

        values = {
            "kind": kind,
            "code": code,
            "user_id": user_id,
            "source": source,
            "created_by_id": created_by_id,
        }
        statement = (
            insert(L)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_salesperson_links_kind_code",
                set_={
                    "user_id": user_id,
                    "source": source,
                    "created_by_id": created_by_id,
                    "updated_at": func.now(),
                },
            )
            .returning(L)
        )
        async with self.database.session_factory() as session:
            model = (await session.scalars(statement)).one()
            await session.commit()
            return _link(model)

    async def create_missing(self, rows: Sequence[tuple[str, str, UUID]], source: str) -> int:
        """Insert links whose code has no row yet.

        Args:
            rows (Sequence[tuple[str, str, UUID]]): ``(kind, code, user_id)``.
            source (str): Source of the new links (``auto``).

        Returns:
            int: How many were created.
        """

        if not rows:
            return 0
        statement = (
            insert(L)
            .values(
                [
                    {"kind": kind, "code": code, "user_id": user_id, "source": source}
                    for kind, code, user_id in rows
                ]
            )
            .on_conflict_do_nothing(constraint="uq_salesperson_links_kind_code")
            .returning(L.id)
        )
        async with self.database.session_factory() as session:
            created = (await session.scalars(statement)).all()
            await session.commit()
            return len(created)
