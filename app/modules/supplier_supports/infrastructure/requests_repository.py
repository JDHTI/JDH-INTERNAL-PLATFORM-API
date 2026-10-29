"""Transactional proposals and decisions. The master audit is enforced by PostgreSQL."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel as C
from app.modules.supplier_supports.domain.requests import (
    request_mode,
    require_eligible,
    validate_value,
)
from app.modules.supplier_supports.infrastructure.models import (
    SupplierSupportModel as S,
)
from app.modules.supplier_supports.infrastructure.models import (
    SupportJdhEventModel as E,
)
from app.modules.supplier_supports.infrastructure.models import (
    SupportRequestModel as R,
)


def snapshot(model: S) -> dict[str, Any]:
    """JSON-safe original context, independent of later master edits."""
    names = (
        "id",
        "version",
        "calc_method",
        "supplier_value",
        "jdh_pct",
        "jdh_net_cost",
        "reference_cost",
        "reference_list_price",
        "value_unit",
        "pack_factor",
        "product_sku",
        "commercial_supplier_id",
        "customer_group",
        "customer_nit",
        "customer_branch_id",
        "customer_name",
        "program",
        "valid_from",
        "valid_to",
        "status",
        "is_enabled",
    )
    return {
        name: (
            str(value)
            if isinstance(value := getattr(model, name), (UUID, Decimal, date))
            else value
        )
        for name in names
    }


def serialize(model: Any) -> dict[str, Any]:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


def named_requests():
    """Resolve display names in one query, including requests created before this UI."""
    return (
        select(R, C.code, C.name)
        .join(S, S.id == R.support_id)
        .outerjoin(C, C.id == S.commercial_supplier_id)
    )


class SqlAlchemySupportRequestRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    async def _support(session, support_id: UUID) -> S:
        model = (
            await session.execute(select(S).where(S.id == support_id).with_for_update())
        ).scalar_one_or_none()
        if model is None:
            raise NotFoundError("Apoyo no encontrado")
        return model

    @staticmethod
    def _version(actual: int, expected: int) -> None:
        if actual != expected:
            raise ConflictError(
                "El registro cambió; recarga y revisa el valor vigente antes de confirmar"
            )

    @staticmethod
    def _event(session, support_id, request_id, event_type, actor, reason, before, after):
        session.add(
            E(
                id=uuid4(),
                support_id=support_id,
                request_id=request_id,
                event_type=event_type,
                actor=actor,
                reason=reason,
                before=before,
                after=after,
                created_at=datetime.now(UTC),
            )
        )

    async def create(
        self,
        support_id: UUID,
        version: int,
        value: Decimal,
        reason: str,
        actor_id: UUID,
        actor: str,
        today: date,
    ) -> dict[str, Any]:
        return (
            await self.create_many([(support_id, version, value, reason)], actor_id, actor, today)
        )[0]

    async def create_many(
        self, items: list[tuple[UUID, int, Decimal, str]], actor_id: UUID, actor: str, today: date
    ) -> list[dict[str, Any]]:
        ids = [item[0] for item in items]
        if not 1 <= len(items) <= 100 or len(ids) != len(set(ids)):
            raise InvalidInputError("Selecciona de 1 a 100 acuerdos sin repetir")
        async with self.database.session_factory() as session, session.begin():
            # Consistent lock order prevents competing overlapping batches from deadlocking.
            supports = {
                support_id: await self._support(session, support_id)
                for support_id in sorted(ids, key=str)
            }
            scopes = set()
            for support_id, version, value, _ in items:
                support = supports[support_id]
                self._version(support.version, version)
                require_eligible(support, today)
                validate_value(request_mode(support.calc_method), value)
                customer = (
                    ("nit", support.customer_nit)
                    if support.customer_nit
                    else (
                        "scope",
                        support.customer_group,
                        support.customer_name,
                        support.customer_branch_id,
                    )
                )
                scopes.add((support.commercial_supplier_id, customer))
            if len(items) > 1 and (
                len(scopes) != 1 or any(s.commercial_supplier_id is None for s in supports.values())
            ):
                raise InvalidInputError("Los acuerdos deben tener el mismo proveedor y cliente")
            models = []
            for support_id, version, value, reason in items:
                support = supports[support_id]
                model = R(
                    id=uuid4(),
                    support_id=support_id,
                    mode=request_mode(support.calc_method),
                    value=value,
                    reason=reason,
                    requested_by_id=actor_id,
                    requested_by=actor,
                    created_at=datetime.now(UTC),
                    status="pendiente",
                    version=1,
                    support_version=version,
                    snapshot=snapshot(support),
                )
                session.add(model)
                models.append(model)
            await session.flush()
            for model in models:
                self._event(
                    session,
                    model.support_id,
                    model.id,
                    "solicitada",
                    actor,
                    model.reason,
                    model.snapshot,
                    {"mode": model.mode, "value": str(model.value)},
                )
            return [serialize(model) for model in models]

    async def search(
        self, owner: UUID | None, status: str | None, q: str | None, page: int, size: int
    ) -> dict[str, Any]:
        conditions = []
        if owner is not None:
            conditions.append(R.requested_by_id == owner)
        if status:
            conditions.append(R.status == status)
        if q:
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(
                or_(
                    R.requested_by.ilike(pattern),
                    R.reason.ilike(pattern),
                    R.snapshot["product_sku"].astext.ilike(pattern),
                    R.snapshot["customer_name"].astext.ilike(pattern),
                    R.snapshot["customer_nit"].astext.ilike(pattern),
                )
            )
        async with self.database.session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(R).where(*conditions))
            rows = (
                await session.execute(
                    named_requests()
                    .where(*conditions)
                    .order_by(R.created_at.desc(), R.id)
                    .offset((page - 1) * size)
                    .limit(size)
                )
            ).all()
            return {
                "items": [
                    {**serialize(row), "supplier_code": code, "supplier_name": name}
                    for row, code, name in rows
                ],
                "total": total,
                "page": page,
                "size": size,
            }

    async def get(self, request_id: UUID, owner: UUID | None) -> dict[str, Any]:
        async with self.database.session_factory() as session:
            conditions = [R.id == request_id]
            if owner is not None:
                conditions.append(R.requested_by_id == owner)
            result = (await session.execute(named_requests().where(*conditions))).first()
            if result is None:
                raise NotFoundError("Solicitud no encontrada")
            model, code, name = result
            events = (
                await session.execute(
                    select(E).where(E.request_id == model.id).order_by(E.created_at, E.id)
                )
            ).scalars()
            return {
                **serialize(model),
                "supplier_code": code,
                "supplier_name": name,
                "events": [serialize(event) for event in events],
            }

    async def decide(
        self,
        request_id: UUID,
        version: int,
        support_version: int | None,
        actor: str,
        approved: bool,
        reason: str | None,
        today: date,
    ) -> dict[str, Any]:
        async with self.database.session_factory() as session, session.begin():
            # Always acquire master before request, including concurrent decisions.
            support_id = await session.scalar(select(R.support_id).where(R.id == request_id))
            if support_id is None:
                raise NotFoundError("Solicitud no encontrada")
            support = await self._support(session, support_id)
            model = (
                await session.execute(select(R).where(R.id == request_id).with_for_update())
            ).scalar_one()
            self._version(model.version, version)
            if model.status != "pendiente":
                raise ConflictError("La solicitud ya fue resuelta")
            before = snapshot(support)
            if approved:
                self._version(support.version, support_version)
                require_eligible(support, today)
                if request_mode(support.calc_method) != model.mode:
                    raise ConflictError("Cambió el tipo del acuerdo; rechaza y solicita de nuevo")
                validate_value(model.mode, model.value)
                if model.mode == "costo_neto":
                    support.jdh_net_cost = model.value
                    support.jdh_pct = None
                else:
                    support.jdh_pct = model.value
                    support.jdh_net_cost = None
                support.updated_by = actor
                support.updated_at = datetime.now(UTC)
                support.version += 1
            model.status = "aprobada" if approved else "rechazada"
            model.version += 1
            model.decided_by = actor
            model.decided_at = datetime.now(UTC)
            model.decision_reason = reason
            self._event(
                session,
                support_id,
                model.id,
                model.status,
                actor,
                reason,
                before,
                snapshot(support),
            )
            await session.flush()
            return serialize(model)

    async def cancel(self, support_id: UUID, version: int, actor: str, reason: str) -> None:
        async with self.database.session_factory() as session, session.begin():
            support = await self._support(session, support_id)
            if support.jdh_pct is None and support.jdh_net_cost is None:
                return
            self._version(support.version, version)
            before = snapshot(support)
            support.jdh_pct = None
            support.jdh_net_cost = None
            support.updated_by = actor
            support.updated_at = datetime.now(UTC)
            support.version += 1
            self._event(
                session, support_id, None, "cancelada", actor, reason, before, snapshot(support)
            )

    async def history(self, support_id: UUID) -> list[dict[str, Any]]:
        async with self.database.session_factory() as session:
            if await session.get(S, support_id) is None:
                raise NotFoundError("Apoyo no encontrado")
            rows = (
                await session.execute(
                    select(E).where(E.support_id == support_id).order_by(E.created_at, E.id)
                )
            ).scalars()
            return [serialize(row) for row in rows]
