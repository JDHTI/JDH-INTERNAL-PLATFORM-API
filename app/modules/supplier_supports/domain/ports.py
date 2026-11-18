"""Ports of the supplier supports module."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.commercial_masters.domain.entities import (
    CommercialSupplier,
    CustomerRef,
    ProductRef,
    TargetMargin,
)
from app.modules.identity.domain.principal import Principal
from app.modules.supplier_supports.domain.attachments import (
    Attachment,
    FileUpload,
    StoredDocument,
    ViewLink,
)
from app.modules.supplier_supports.domain.entities import (
    Conflict,
    LoadPlan,
    LoadResult,
    Support,
    SupportData,
    SupportRow,
    Trace,
)
from app.modules.supplier_supports.domain.filters import SearchSpec
from app.modules.supplier_supports.domain.overlap import Supersede


class SupportRepository(Protocol):
    """Persistence of supports and their tiers.

    Every write queues the support's new version for the DWH in the same transaction;
    ``trace`` says what wrote it (``vocabulary.ORIGINS``) and the bulk load, if any.
    """

    async def create(
        self,
        support_id: UUID,
        data: SupportData,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
        supersede: Sequence[Supersede] = (),
    ) -> Support: ...

    async def create_many(
        self,
        items: Sequence[tuple[UUID, SupportData]],
        actor: str,
        now: datetime,
        trace: Trace | None = None,
        supersede: Sequence[Supersede] = (),
    ) -> list[Support]: ...

    async def get(self, support_id: UUID) -> Support | None: ...

    async def get_row(self, support_id: UUID) -> SupportRow | None: ...

    async def update(
        self,
        support_id: UUID,
        data: SupportData,
        expected_version: int,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
        supersede: Sequence[Supersede] = (),
    ) -> Support: ...

    async def overlapping(self, data: SupportData, exclude: Sequence[UUID] = ()) -> list[Support]:
        """Live supports of the same class and scope whose validity crosses ``data``'s."""
        ...

    async def pricing_context(
        self, product_sku: str | None, customer_nit: str | None
    ) -> tuple[dict[str, Any], Any, str | None, str | None]:
        """Product margins, pack factor, customer margin list and group (form preview)."""
        ...

    async def live_in_groups(self, kinds: Sequence[str]) -> list[Support]:
        """Live supports sharing supplier, class, customer and SKU with another live one."""
        ...

    async def supersede(
        self, items: Sequence[Supersede], actor: str, now: datetime
    ) -> list[Support]:
        """Expire or void several supports in one transaction (all or none)."""
        ...

    async def void(
        self,
        support_id: UUID,
        reason: str,
        expected_version: int,
        actor: str,
        now: datetime,
        trace: Trace | None = None,
    ) -> Support: ...

    async def live_for_suppliers(self, supplier_ids: Sequence[UUID]) -> list[Support]:
        """Supports of these suppliers that are not voided (the bulk load matches against them)."""
        ...

    async def apply_load(self, plan: LoadPlan, actor: str, now: datetime) -> LoadResult:
        """Write a whole bulk load in one transaction: its record, creations, updates and
        expirations, each queued for the DWH. Any version mismatch aborts everything."""
        ...

    async def search(self, spec: SearchSpec) -> tuple[list[SupportRow], int]: ...

    async def search_ids(self, spec: SearchSpec, limit: int) -> list[UUID]: ...

    async def get_by_source_key(self, source_key: str) -> Support | None: ...

    async def existing_source_keys(self, keys: Sequence[str]) -> set[str]: ...

    async def conflicts(self, support_id: UUID) -> list[Conflict]: ...

    async def scope_values(self) -> dict[str, list[str]]: ...


class MasterLookup(Protocol):
    """The masters a support points to."""

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, ProductRef]: ...

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]: ...

    async def commercial_suppliers_by_name(self, query: str) -> list[CommercialSupplier]: ...

    async def commercial_supplier_names(
        self, ids: Sequence[UUID]
    ) -> dict[UUID, tuple[str, str]]: ...

    async def customer_names(self, nits: Sequence[str]) -> dict[str, str]: ...

    async def customers(self, nits: Sequence[str]) -> dict[str, CustomerRef]: ...

    async def target_margins(
        self, nit: str | None, skus: Sequence[str]
    ) -> dict[str, TargetMargin]: ...

    async def products_by_supplier_sku(
        self, supplier_id: UUID, supplier_skus: Sequence[str]
    ) -> dict[str, list[ProductRef]]: ...

    async def business_group_members(self, groups: Sequence[str]) -> dict[str, list[tuple]]: ...

    async def business_groups(self, query: str, limit: int) -> list[tuple[str, int]]: ...


class AttachmentRepository(Protocol):
    """Persistence of the links between supports and documents."""

    async def link_many(
        self, pairs: Sequence[tuple[UUID, UUID]], actor: str, now: datetime
    ) -> int: ...

    async def unlink(self, support_id: UUID, document_id: UUID) -> bool: ...

    async def list_for_support(self, support_id: UUID) -> list[Attachment]: ...

    async def get_link(self, support_id: UUID, document_id: UUID) -> Attachment | None: ...

    async def existing_supports(self, ids: Sequence[UUID]) -> set[UUID]: ...

    async def supplier_codes(self, ids: Sequence[UUID]) -> list[str]: ...


class SupportDocumentStore(Protocol):
    """Keeps support files in the document manager, in the supports folder."""

    async def store(
        self,
        principal: Principal,
        upload: FileUpload,
        *,
        name: str | None,
        description: str | None,
        tags: Sequence[str],
    ) -> StoredDocument: ...

    async def fetch(self, principal: Principal, document_id: UUID) -> StoredDocument: ...

    async def discard(self, principal: Principal, document_id: UUID) -> None: ...

    async def view_link(
        self, principal: Principal, document_id: UUID, *, inline: bool
    ) -> ViewLink: ...


class SupportFileReader(Protocol):
    """Reads the cells of an uploaded file, one ``(title, rows)`` per sheet."""

    def read(
        self, filename: str, content: bytes, sheet: str | None = None
    ) -> list[tuple[str, list[list[Any]]]]: ...


class ManagerPolicy(Protocol):
    """Decides who may create, edit and void supports, and who may read them."""

    async def is_manager(self, principal: Principal) -> bool: ...

    async def can_read(self, principal: Principal) -> bool: ...


class Calendar(Protocol):
    """Business calendar."""

    def today(self) -> date: ...
