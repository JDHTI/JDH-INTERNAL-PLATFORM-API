"""How supports and their loads are published to the warehouse (``plataforma`` schema).

Every write of a support queues its whole new version in ``dwh_outbox`` inside the same
transaction (:func:`queue_supports`). The topics below turn those events into rows of:

- ``plataforma.apoyo_proveedor``: the current version of each support (upsert by ``id``,
  only when the incoming ``version`` is newer, so retries and late events change nothing).
- ``plataforma.apoyo_proveedor_historial``: every version, append-only (``id, version``).
- ``plataforma.carga_apoyos``: each bulk load.

The typed columns are what BI filters on; ``datos`` carries the full record (JSONB), so a new
field never needs an ``ALTER TABLE`` in the warehouse. The DDL is owned by the DWH team
(``dwh-platform`` migrations); see ``docs/apoyos-dwh.md`` for the contract.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select

from app.infrastructure.database import Database
from app.modules.commercial_masters.infrastructure.models import CommercialSupplierModel
from app.modules.dwh_publish.domain.entities import OutboxEvent, OutboxItem
from app.modules.dwh_publish.infrastructure.outbox import enqueue
from app.modules.supplier_supports.domain.vocabulary import ACTIVE_KINDS
from app.modules.supplier_supports.infrastructure.models import (
    SupplierSupportCostModel,
    SupplierSupportLoadModel,
    SupplierSupportModel,
    SupplierSupportTierModel,
)

SUPPORT_TOPIC = "supplier_support"
LOAD_TOPIC = "supplier_support_load"
DELETE_TOPIC = "supplier_support_delete"
SCHEMA = "plataforma"

S = SupplierSupportModel


def _plain(value: Any) -> Any:
    """A JSON-safe value (decimals as text so no precision is lost)."""

    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _record(model: SupplierSupportModel) -> dict[str, Any]:
    return {column.name: _plain(getattr(model, column.name)) for column in S.__table__.columns}


def support_payload(
    model: SupplierSupportModel,
    supplier: tuple[str, str] | None,
    cost: SupplierSupportCostModel | None,
    tiers: Sequence[SupplierSupportTierModel] = (),
) -> dict[str, Any]:
    """The version of a support as the warehouse stores it."""

    record = _record(model)
    record["tiers"] = [
        {
            "threshold_type": tier.threshold_type,
            "from_value": _plain(tier.from_value),
            "to_value": _plain(tier.to_value),
            "value": _plain(tier.value),
        }
        for tier in sorted(tiers, key=lambda item: item.position)
    ]
    code, name = supplier or (None, None)
    return {
        "id": str(model.id),
        "version": model.version,
        "proveedor_codigo": code,
        "proveedor_nombre": name,
        "tipo": model.kind,
        "metodo_calculo": model.calc_method,
        "financia": model.funding,
        "programa": model.program,
        "referencia_externa": model.external_ref,
        "nit_cliente": model.customer_nit,
        "cliente": model.customer_name,
        "grupo_cliente": model.customer_group,
        "sku": model.product_sku,
        "sku_proveedor": model.supplier_sku,
        "marca": model.brand,
        "regionales": list(model.regionals or []),
        "unidades_negocio": list(model.business_units or []),
        "canales": list(model.channels or []),
        "valor_proveedor": _plain(model.supplier_value),
        "apoyo_jdh_pct": _plain(model.jdh_pct),
        "apoyo_jdh_costo_neto": _plain(model.jdh_net_cost),
        "unidad_valor": model.value_unit,
        "margen_objetivo": _plain(model.target_margin_pct),
        "margen_minimo": _plain(model.min_margin_pct),
        "margen_lista": _plain(model.list_margin_pct),
        "factor_empaque": _plain(model.pack_factor),
        "costo_referencia": _plain(model.reference_cost),
        "precio_lista_referencia": _plain(model.reference_list_price),
        "costo_dwh_unidad": _plain(cost.unit_cost) if cost else None,
        "costo_dwh_fuente": cost.source if cost else None,
        "costo_dwh_fecha": _plain(cost.snapshot_date) if cost else None,
        "vigencia_desde": _plain(model.valid_from),
        "vigencia_hasta": _plain(model.valid_to),
        "estado": model.status,
        # Rebates and budgets are kept apart (rules v2): never shown as in force.
        "habilitado": model.is_enabled and model.kind in ACTIVE_KINDS,
        "origen": model.last_origin,
        "carga_id": _plain(model.last_load_id),
        "creado_en": _plain(model.created_at),
        "creado_por": model.created_by,
        "modificado_en": _plain(model.updated_at),
        "modificado_por": model.updated_by,
        "anulado_en": _plain(model.voided_at),
        "motivo_anulacion": model.void_reason,
        "datos": record,
    }


def load_payload(model: SupplierSupportLoadModel) -> dict[str, Any]:
    """A bulk load as the warehouse stores it."""

    return {
        "id": str(model.id),
        "version": 1,
        "archivo": model.file_name,
        "archivo_sha256": model.file_sha256,
        "proveedor_codigo": model.supplier_code,
        "hoja": model.sheet,
        "usuario": model.actor,
        "creado_en": _plain(model.created_at),
        "filas": model.rows_read,
        "creados": model.created,
        "actualizados": model.updated,
        "sin_cambio": model.unchanged,
        "ausentes": model.missing,
        "vencidos": model.expired,
    }


async def support_events(session: Any, models: Sequence[SupplierSupportModel]) -> list[OutboxEvent]:
    """Events of the current version of these supports, read inside ``session``."""

    if not models:
        return []
    for model in models:
        # Columns the database set on flush (server defaults, triggers) come back expired;
        # reading them lazily is not allowed in async, so they are refreshed explicitly.
        expired = inspect(model).expired_attributes
        if expired:
            await session.refresh(model, attribute_names=list(expired))
    ids = [model.id for model in models]
    supplier_ids = {
        model.commercial_supplier_id for model in models if model.commercial_supplier_id
    }
    suppliers: dict[UUID, tuple[str, str]] = {}
    if supplier_ids:
        rows = await session.execute(
            select(
                CommercialSupplierModel.id,
                CommercialSupplierModel.code,
                CommercialSupplierModel.name,
            ).where(CommercialSupplierModel.id.in_(supplier_ids))
        )
        suppliers = {row[0]: (row[1], row[2]) for row in rows.all()}
    costs = {
        cost.support_id: cost
        for cost in (
            await session.execute(
                select(SupplierSupportCostModel).where(SupplierSupportCostModel.support_id.in_(ids))
            )
        ).scalars()
    }
    tiers: dict[UUID, list[SupplierSupportTierModel]] = {}
    for tier in (
        await session.execute(
            select(SupplierSupportTierModel).where(SupplierSupportTierModel.support_id.in_(ids))
        )
    ).scalars():
        tiers.setdefault(tier.support_id, []).append(tier)
    return [
        OutboxEvent(
            SUPPORT_TOPIC,
            model.id,
            model.version,
            support_payload(
                model,
                suppliers.get(model.commercial_supplier_id)
                if model.commercial_supplier_id
                else None,
                costs.get(model.id),
                tiers.get(model.id, ()),
            ),
        )
        for model in models
    ]


async def queue_supports(
    session: Any, models: Sequence[SupplierSupportModel], *, not_before: datetime | None = None
) -> None:
    """Queue the new version of these supports in the change's own transaction.

    Call it after the models are flushed (so their values and tiers are final) and before
    the commit.
    """

    await enqueue(session, await support_events(session, models), not_before=not_before)


async def queue_load(session: Any, model: SupplierSupportLoadModel) -> None:
    """Queue a bulk load in the load's own transaction."""

    await enqueue(session, [OutboxEvent(LOAD_TOPIC, model.id, 1, load_payload(model))])


# ------------------------------------------------------------------ topics

_SUPPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("proveedor_codigo", "text"),
    ("proveedor_nombre", "text"),
    ("tipo", "text"),
    ("metodo_calculo", "text"),
    ("financia", "text"),
    ("programa", "text"),
    ("referencia_externa", "text"),
    ("nit_cliente", "text"),
    ("cliente", "text"),
    ("grupo_cliente", "text"),
    ("sku", "text"),
    ("sku_proveedor", "text"),
    ("marca", "text"),
    ("regionales", "text[]"),
    ("unidades_negocio", "text[]"),
    ("canales", "text[]"),
    ("valor_proveedor", "numeric"),
    ("apoyo_jdh_pct", "numeric"),
    ("apoyo_jdh_costo_neto", "numeric"),
    ("unidad_valor", "text"),
    ("margen_objetivo", "numeric"),
    ("margen_minimo", "numeric"),
    ("margen_lista", "numeric"),
    ("factor_empaque", "numeric"),
    ("costo_referencia", "numeric"),
    ("precio_lista_referencia", "numeric"),
    ("costo_dwh_unidad", "numeric"),
    ("costo_dwh_fuente", "text"),
    ("costo_dwh_fecha", "date"),
    ("vigencia_desde", "date"),
    ("vigencia_hasta", "date"),
    ("estado", "text"),
    ("habilitado", "boolean"),
    ("origen", "text"),
    ("carga_id", "uuid"),
    ("creado_en", "timestamptz"),
    ("creado_por", "text"),
    ("modificado_en", "timestamptz"),
    ("modificado_por", "text"),
    ("anulado_en", "timestamptz"),
    ("motivo_anulacion", "text"),
)


def _extract(name: str, kind: str) -> str:
    if kind == "text[]":
        return f"ARRAY(SELECT jsonb_array_elements_text(coalesce(r->'{name}', '[]'::jsonb)))"
    if kind == "text":
        return f"r->>'{name}'"
    return f"(r->>'{name}')::{kind}"


def _support_upsert(table: str) -> str:
    names = ", ".join(name for name, _ in _SUPPORT_COLUMNS)
    values = ", ".join(_extract(name, kind) for name, kind in _SUPPORT_COLUMNS)
    head = (
        f"INSERT INTO {SCHEMA}.{table} AS t (id, version, {names}, datos) "
        f"SELECT (r->>'id')::uuid, (r->>'version')::bigint, {values}, r->'datos' "
        "FROM jsonb_array_elements($1::jsonb) AS r "
    )
    if table == "apoyo_proveedor_historial":
        return head + "ON CONFLICT (id, version) DO NOTHING"
    updates = ", ".join(f"{name} = EXCLUDED.{name}" for name, _ in _SUPPORT_COLUMNS)
    return (
        head + f"ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, {updates}, "
        "datos = EXCLUDED.datos, actualizado_en = now() WHERE t.version < EXCLUDED.version"
    )


_UPSERT_CURRENT = _support_upsert("apoyo_proveedor")
_INSERT_HISTORY = _support_upsert("apoyo_proveedor_historial")
_UPSERT_LOAD = (
    f"INSERT INTO {SCHEMA}.carga_apoyos AS t (id, archivo, archivo_sha256, proveedor_codigo, "
    "hoja, usuario, creado_en, filas, creados, actualizados, sin_cambio, ausentes, vencidos) "
    "SELECT (r->>'id')::uuid, r->>'archivo', r->>'archivo_sha256', r->>'proveedor_codigo', "
    "r->>'hoja', r->>'usuario', (r->>'creado_en')::timestamptz, (r->>'filas')::int, "
    "(r->>'creados')::int, (r->>'actualizados')::int, (r->>'sin_cambio')::int, "
    "(r->>'ausentes')::int, (r->>'vencidos')::int "
    "FROM jsonb_array_elements($1::jsonb) AS r ON CONFLICT (id) DO NOTHING"
)


def _latest(items: Sequence[OutboxItem]) -> list[dict[str, Any]]:
    """One payload per record, the highest version (an upsert may touch a row only once)."""

    latest: dict[UUID, OutboxItem] = {}
    for item in items:
        if item.entity_id not in latest or item.version > latest[item.entity_id].version:
            latest[item.entity_id] = item
    return [item.payload for item in latest.values()]


class SupportTopic:
    """Supports: current table plus append-only history."""

    name = SUPPORT_TOPIC

    def __init__(self, database: Database) -> None:
        """Create the topic.

        Args:
            database (Database): The platform database (for reconciliation).
        """

        self.database = database

    async def write(self, connection: Any, events: Sequence[OutboxItem]) -> None:
        """Upsert the current version and append every version to the history."""

        await connection.execute(_INSERT_HISTORY, json.dumps([item.payload for item in events]))
        await connection.execute(_UPSERT_CURRENT, json.dumps(_latest(events)))

    async def remote_versions(self, connection: Any) -> dict[UUID, int]:
        """``id -> version`` held by the warehouse."""

        rows = await connection.fetch(f"SELECT id, version FROM {SCHEMA}.apoyo_proveedor")
        return {row["id"]: int(row["version"]) for row in rows}

    async def local_versions(self) -> dict[UUID, int]:
        """``id -> version`` held by the platform."""

        async with self.database.session_factory() as session:
            rows = await session.execute(select(S.id, S.version))
            return {row[0]: int(row[1]) for row in rows.all()}

    async def snapshots(self, ids: Sequence[UUID]) -> list[OutboxEvent]:
        """The current version of these supports."""

        async with self.database.session_factory() as session:
            models = list((await session.execute(select(S).where(S.id.in_(ids)))).scalars())
            return await support_events(session, models)


class LoadTopic:
    """Bulk loads (inserted once, never changed)."""

    name = LOAD_TOPIC

    def __init__(self, database: Database) -> None:
        """Create the topic.

        Args:
            database (Database): The platform database (for reconciliation).
        """

        self.database = database

    async def write(self, connection: Any, events: Sequence[OutboxItem]) -> None:
        """Insert the loads; one already there is left as it is."""

        await connection.execute(_UPSERT_LOAD, json.dumps(_latest(events)))

    async def remote_versions(self, connection: Any) -> dict[UUID, int]:
        """Every load the warehouse holds (always version 1)."""

        rows = await connection.fetch(f"SELECT id FROM {SCHEMA}.carga_apoyos")
        return {row["id"]: 1 for row in rows}

    async def local_versions(self) -> dict[UUID, int]:
        """Every load of the platform (always version 1)."""

        async with self.database.session_factory() as session:
            rows = await session.execute(select(SupplierSupportLoadModel.id))
            return {row[0]: 1 for row in rows.all()}

    async def snapshots(self, ids: Sequence[UUID]) -> list[OutboxEvent]:
        """These loads as events."""

        async with self.database.session_factory() as session:
            models = (
                await session.execute(
                    select(SupplierSupportLoadModel).where(SupplierSupportLoadModel.id.in_(ids))
                )
            ).scalars()
            return [OutboxEvent(LOAD_TOPIC, model.id, 1, load_payload(model)) for model in models]


class DeleteTopic:
    """Supports deleted from the platform (the discontinued classes, rules v2).

    The warehouse writer has no ``DELETE``: it calls ``plataforma.borrar_apoyo_proveedor``
    (``SECURITY DEFINER``, owned by the DWH team), which deletes only the discontinued classes
    and appends a ``borrado`` row to the history, atomically. The event's version is above
    the last one published, so a repeated or late event changes nothing. Never reconciled:
    a deleted support has nothing to compare.
    """

    name = DELETE_TOPIC

    async def write(self, connection: Any, events: Sequence[OutboxItem]) -> None:
        """Delete each support at its version (idempotent)."""

        for item in events:
            await connection.fetch(
                f"SELECT * FROM {SCHEMA}.borrar_apoyo_proveedor($1::uuid, $2::bigint, $3::text, "
                "$4::timestamptz)",
                item.entity_id,
                item.version,
                item.payload.get("borrado_por") or "plataforma",
                datetime.fromisoformat(item.payload["borrado_en"]),
            )

    async def remote_versions(self, connection: Any) -> dict[UUID, int]:
        return {}

    async def local_versions(self) -> dict[UUID, int]:
        return {}

    async def snapshots(self, ids: Sequence[UUID]) -> list[OutboxEvent]:
        return []


def delete_event(support_id: UUID, version: int, actor: str, when: datetime) -> OutboxEvent:
    """The event that deletes a support from the warehouse (its version + 1)."""

    return OutboxEvent(
        DELETE_TOPIC,
        support_id,
        version,
        {"id": str(support_id), "borrado_por": actor, "borrado_en": when.isoformat()},
    )


def build_topics(
    supports: SupportTopic, loads: LoadTopic, deletes: DeleteTopic | None = None
) -> dict[str, Any]:
    """Topic name -> topic, for the publisher."""

    topics: dict[str, Any] = {supports.name: supports, loads.name: loads}
    if deletes is not None:
        topics[deletes.name] = deletes
    return topics


__all__ = [
    "LOAD_TOPIC",
    "build_topics",
    "SUPPORT_TOPIC",
    "LoadTopic",
    "SupportTopic",
    "load_payload",
    "queue_load",
    "queue_supports",
    "support_events",
    "support_payload",
]
