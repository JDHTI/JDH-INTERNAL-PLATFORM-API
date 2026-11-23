"""Deleting supports and requests for good (2026-11-23): only the area leader or a platform
administrator, through :class:`~app.modules.supplier_supports.application.deletions.\
SupportDeletionService`.

The supports' requests and JDH history are append-only in the database; each deletion runs in
one transaction that sets ``app.allow_support_delete = 'on'``, the only way their guards let a
row go (migration 0079).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, text

from app.core.errors import ConflictError, NotFoundError
from app.infrastructure.database import Database
from app.modules.supplier_supports.domain.entities import Trace
from app.modules.supplier_supports.infrastructure.models import (
    InternalSupportRequestModel,
    SupplierSupportModel,
    SupportJdhEventModel,
    SupportRequestModel,
)
from app.modules.supplier_supports.infrastructure.publication import queue_supports
from app.modules.supplier_supports.infrastructure.repository import _stamp

S = SupplierSupportModel
R = SupportRequestModel
E = SupportJdhEventModel
_ALLOW = text("SET LOCAL app.allow_support_delete = 'on'")


class SqlAlchemySupportDeletionRepository:
    """The deletions, each in its own transaction."""

    def __init__(self, database: Database) -> None:
        """Create the repository.

        Args:
            database (Database): Shared database resource.
        """

        self.database = database

    async def delete_supports(
        self, chosen: Mapping[UUID, int | None], reason: str, actor: str, now: datetime
    ) -> int:
        """Delete these supports with their requests, JDH history, costs, tiers and links to
        documents (the documents stay in the document manager), all or none.

        The warehouse has no delete for them: each one not voided yet is first published as
        voided with ``reason``, so it stops applying there too. A version given must still
        match.

        Raises:
            NotFoundError: A support does not exist.
            ConflictError: A support changed since it was seen.
        """

        ids = list(chosen)
        async with self.database.session_factory() as session, session.begin():
            await session.execute(_ALLOW)
            models = list(
                (
                    await session.execute(
                        select(S).where(S.id.in_(ids)).order_by(S.id).with_for_update()
                    )
                ).scalars()
            )
            found = {model.id: model for model in models}
            missing = [support_id for support_id in ids if support_id not in found]
            if missing:
                raise NotFoundError(f"{len(missing)} apoyo(s) no existen o ya fueron eliminados")
            for support_id, version in chosen.items():
                if version is not None and found[support_id].version != version:
                    raise ConflictError(
                        "Un apoyo cambió desde que lo viste; recarga y vuelve a elegir"
                    )
            voided = [model for model in models if model.status != "anulado"]
            for model in voided:
                model.status = "anulado"
                model.void_reason = f"Eliminado: {reason}"[:2000]
                model.voided_at = now
                _stamp(model, actor, now, Trace("eliminacion"))
            await session.flush()
            if voided:
                await queue_supports(session, voided)
            requests = select(R.id).where(R.support_id.in_(ids))
            await session.execute(
                delete(E).where(or_(E.support_id.in_(ids), E.request_id.in_(requests)))
            )
            await session.execute(delete(R).where(R.support_id.in_(ids)))
            result = await session.execute(delete(S).where(S.id.in_(ids)))
            return int(result.rowcount or 0)

    async def delete_requests(self, request_ids: Sequence[UUID]) -> int:
        """Delete JDH requests (pending or decided) with their history events. A decision
        already applied to its support stays on the support.

        Raises:
            NotFoundError: A request does not exist.
        """

        ids = list(dict.fromkeys(request_ids))
        async with self.database.session_factory() as session, session.begin():
            await session.execute(_ALLOW)
            found = set((await session.execute(select(R.id).where(R.id.in_(ids)))).scalars())
            if len(found) != len(ids):
                raise NotFoundError("Una solicitud no existe o ya fue eliminada")
            await session.execute(delete(E).where(E.request_id.in_(ids)))
            result = await session.execute(delete(R).where(R.id.in_(ids)))
            return int(result.rowcount or 0)

    async def delete_internal_requests(self, request_ids: Sequence[UUID]) -> int:
        """Delete internal requests (pending or decided) with their items. A support an
        approval created stays, without the link to the request.

        Raises:
            NotFoundError: A request does not exist.
        """

        ids = list(dict.fromkeys(request_ids))
        model = InternalSupportRequestModel
        async with self.database.session_factory() as session, session.begin():
            found = set(
                (await session.execute(select(model.id).where(model.id.in_(ids)))).scalars()
            )
            if len(found) != len(ids):
                raise NotFoundError("Una solicitud no existe o ya fue eliminada")
            result = await session.execute(delete(model).where(model.id.in_(ids)))
            return int(result.rowcount or 0)
