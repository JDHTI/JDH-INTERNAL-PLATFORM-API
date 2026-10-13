"""Ports of the retained orders validator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.principal import Principal
from app.modules.order_validation.domain.entities import (
    Agreement,
    Batch,
    BatchPage,
    CustomerScope,
    LineResult,
    ManualApproval,
    OrderLine,
    ProductInfo,
    StoredLine,
)


class OrderFileReader(Protocol):
    """Turns an uploaded spreadsheet into order lines."""

    def read(self, filename: str, content: bytes) -> list[OrderLine]: ...


class AgreementSource(Protocol):
    """The supports master and the commercial masters, read-only."""

    async def agreements(
        self, supplier_codes: Sequence[str], skus: Sequence[str], nits: Sequence[str]
    ) -> list[Agreement]: ...

    async def products(self, skus: Sequence[str]) -> dict[str, ProductInfo]: ...

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerScope]: ...


class BatchRepository(Protocol):
    """Uploaded files, their lines and the latest result of each line."""

    async def create(
        self,
        batch_id: UUID,
        file_name: str,
        actor: str,
        now: datetime,
        lines: Sequence[tuple[OrderLine, LineResult]],
        margin_threshold: Decimal,
    ) -> Batch: ...

    async def get(self, batch_id: UUID) -> Batch | None: ...

    async def list(self, page: int, size: int) -> BatchPage: ...

    async def lines(self, batch_id: UUID) -> list[StoredLine]: ...

    async def save_results(
        self,
        batch_id: UUID,
        results: Sequence[tuple[UUID, LineResult]],
        actor: str,
        now: datetime,
        margin_threshold: Decimal,
    ) -> Batch: ...

    async def set_threshold(self, batch_id: UUID, margin_threshold: Decimal) -> Batch | None: ...

    async def approvals(self, batch_id: UUID) -> dict[str, ManualApproval]: ...

    async def approve(self, batch_id: UUID, approvals: Sequence[ManualApproval]) -> Batch: ...

    async def remove_approval(self, batch_id: UUID, document: str) -> bool: ...

    async def delete(self, batch_id: UUID) -> bool: ...


class ManagerPolicy(Protocol):
    """Decides who may upload files and re-run the validation."""

    async def is_manager(self, principal: Principal) -> bool: ...


class Calendar(Protocol):
    """Business calendar."""

    def today(self) -> date: ...
