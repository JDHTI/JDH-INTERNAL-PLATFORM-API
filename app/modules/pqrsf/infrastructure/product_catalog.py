"""The JDH product master as PQRSF sees it, read through the commercial masters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.modules.pqrsf.domain.entities import ProductMaster, ProductOption


class _ProductRef(Protocol):
    sku: str
    description: str | None
    commercial_supplier_code: str | None


class MasterProducts(Protocol):
    """What this adapter needs from the commercial masters lookup."""

    async def search_products(self, query: str, limit: int) -> Sequence[_ProductRef]: ...

    async def products_by_sku(self, skus: Sequence[str]) -> dict[str, _ProductRef]: ...

    async def commercial_suppliers_by_code(self, codes: Sequence[str]) -> dict[str, UUID]: ...

    async def commercial_supplier_names(
        self, ids: Sequence[UUID]
    ) -> dict[UUID, tuple[str, str]]: ...


class MasterProductCatalog:
    """Search references for the public form and resolve their name and supplier."""

    def __init__(self, masters: MasterProducts) -> None:
        """Create the catalog.

        Args:
            masters (MasterProducts): Lookup of the commercial masters module.
        """

        self.masters = masters

    async def search(self, query: str, limit: int) -> list[ProductOption]:
        """Find active products whose reference is the text or whose name contains it.

        Args:
            query (str): Reference or part of the name.
            limit (int): Most results to return.

        Returns:
            list[ProductOption]: Matching products, by reference.
        """

        return [
            ProductOption(sku=product.sku, description=product.description)
            for product in await self.masters.search_products(query, limit)
        ]

    async def resolve(self, skus: Sequence[str]) -> dict[str, ProductMaster]:
        """Look up references with their name and supplier.

        Args:
            skus (Sequence[str]): JDH references.

        Returns:
            dict[str, ProductMaster]: The references that exist, by reference.
        """

        products = await self.masters.products_by_sku(skus)
        codes = sorted(
            {p.commercial_supplier_code for p in products.values() if p.commercial_supplier_code}
        )
        ids = await self.masters.commercial_suppliers_by_code(codes) if codes else {}
        names = await self.masters.commercial_supplier_names(list(ids.values())) if ids else {}
        name_by_code = {code: name for code, name in names.values()}
        return {
            sku: ProductMaster(
                sku=sku,
                description=product.description,
                supplier_code=product.commercial_supplier_code,
                supplier_name=name_by_code.get(product.commercial_supplier_code or ""),
            )
            for sku, product in products.items()
        }
