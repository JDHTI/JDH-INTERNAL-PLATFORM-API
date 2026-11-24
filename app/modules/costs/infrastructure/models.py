"""Tables of Costos: reasons, novelties of the purchase entries, credit notes and history."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

_STATUSES = "('pendiente','espera_nota_credito','resuelta','anulada')"
_KINDS = "('diferencia','sin_cotizacion','sin_novedad')"


class CostReasonModel(Base):
    """A reason an entry line did not match its quotation («INCREMENTO NO ANUNCIADO»)."""

    __tablename__ = "cost_reasons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # With it a novelty waits for the supplier's credit note; without it, it is resolved.
    requires_credit_note: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CostNoveltyModel(Base):
    """An entry line with a novelty: a copy of the warehouse line, its reason and status.

    ``line_key`` is the warehouse's ``compra_linea_key`` (SIESA's movement rowid): stable
    between loads, gone only when the document is voided.
    """

    __tablename__ = "cost_entry_novelties"
    __table_args__ = (
        CheckConstraint(f"status IN {_STATUSES}", name="ck_cost_entry_novelties_status"),
        CheckConstraint(f"kind IN {_KINDS}", name="ck_cost_entry_novelties_kind"),
        Index("ix_cost_entry_novelties_entry_date", "entry_date"),
        Index("ix_cost_entry_novelties_status", "status"),
        Index("ix_cost_entry_novelties_supplier_code", "supplier_code"),
        Index("ix_cost_entry_novelties_sku", "sku"),
        Index("ix_cost_entry_novelties_reason_id", "reason_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    line_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    co: Mapped[str | None] = mapped_column(Text)
    regional_code: Mapped[str | None] = mapped_column(Text)
    regional_name: Mapped[str | None] = mapped_column(Text)
    warehouse: Mapped[str | None] = mapped_column(Text)
    document: Mapped[str | None] = mapped_column(Text)
    purchase_order: Mapped[str | None] = mapped_column(Text)
    invoice_ref: Mapped[str | None] = mapped_column(Text)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    supplier_label: Mapped[str | None] = mapped_column(Text)
    supplier_code: Mapped[str | None] = mapped_column(Text)
    supplier_nit: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    # The quotation less its Tasa 1 (what the entry should have cost), the Tasa 1 as a
    # fraction and the quotation's unit price before it.
    quotation: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    quotation_since: Mapped[date | None] = mapped_column(Date)
    discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    quotation_list_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    difference: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    difference_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    previous_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    antepenultimate_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    # The warehouse no longer has the line (its document was voided).
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_reasons.id", ondelete="RESTRICT")
    )
    reason_by: Mapped[str | None] = mapped_column(String(320))
    reason_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CostCreditNoteModel(Base):
    """A supplier's credit note document: one per NIT and number, shared by its novelties."""

    __tablename__ = "cost_credit_notes"
    __table_args__ = (
        UniqueConstraint("supplier_nit", "number", name="uq_cost_credit_notes_supplier_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    supplier_nit: Mapped[str] = mapped_column(Text, nullable=False)
    number: Mapped[str] = mapped_column(String(60), nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    uploaded_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostNoveltyCreditNoteModel(Base):
    """The credit note registered against one novelty, with the amount for that line."""

    __tablename__ = "cost_novelty_credit_notes"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_cost_novelty_credit_notes_amount"),
        Index("ix_cost_novelty_credit_notes_credit_note_id", "credit_note_id"),
    )

    novelty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_entry_novelties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    credit_note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cost_credit_notes.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    registered_by: Mapped[str] = mapped_column(String(320), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostNoveltyEventModel(Base):
    """What happened to a novelty: detected, reason, credit note, voided, re-read."""

    __tablename__ = "cost_novelty_events"
    __table_args__ = (Index("ix_cost_novelty_events_novelty_id", "novelty_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    novelty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_entry_novelties.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # ``None`` for the daily job.
    actor: Mapped[str | None] = mapped_column(String(320))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostIncreaseRequestModel(Base):
    """What one supplier sent at once: the «MAESTRA» workbook or the manual form."""

    __tablename__ = "cost_increase_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('en_revision','aprobada','rechazada')",
            name="ck_cost_increase_requests_status",
        ),
        CheckConstraint(
            "source IN ('plantilla','manual')", name="ck_cost_increase_requests_source"
        ),
        Index("ix_cost_increase_requests_status", "status", "created_at"),
        Index("ix_cost_increase_requests_supplier_code", "supplier_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    supplier_code: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    # The workbook as the supplier returned it (``plantilla`` only).
    file_name: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(320))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostIncreaseLineModel(Base):
    """One row of a request: a reference in one regional scope, the item as it was and the
    increase the supplier sent."""

    __tablename__ = "cost_increase_request_lines"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "sku", "regional", name="uq_cost_increase_request_lines_sku_regional"
        ),
        CheckConstraint("new_cost > 0", name="ck_cost_increase_request_lines_new_cost"),
        CheckConstraint(
            "public_price IS NULL OR public_price > 0",
            name="ck_cost_increase_request_lines_public_price",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cost_increase_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_number: Mapped[int | None] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_sku: Mapped[str | None] = mapped_column(Text)
    ean: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    pack_factor: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    category: Mapped[str | None] = mapped_column(Text)
    tax_group: Mapped[str | None] = mapped_column(Text)
    icui_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    ibua: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    old_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    old_cost_with_tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    new_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    new_cost_with_tax: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    variation_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    variation_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    change: Mapped[str | None] = mapped_column(String(10))
    increased_on: Mapped[date | None] = mapped_column(Date)
    regular_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # ``NACIONAL``, a regional or ``RESTO DE REGIONALES`` (the workbook's «Regional»).
    regional: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NACIONAL", server_default="NACIONAL"
    )
    # The public sale price without VAT the supplier suggests («PVP»).
    public_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    observation: Mapped[str | None] = mapped_column(Text)
    # The reviewer may leave a line out (the workbook's «Estado» SI/NO).
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The first row it became in the history of increases, once approved.
    ledger_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


__all__ = [
    "CostCreditNoteModel",
    "CostIncreaseLineModel",
    "CostIncreaseRequestModel",
    "CostNoveltyCreditNoteModel",
    "CostNoveltyEventModel",
    "CostNoveltyModel",
    "CostReasonModel",
]
