from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.core.errors import ConflictError, InvalidInputError
from app.modules.sites.application.service import SiteService
from app.modules.sites.domain.entities import Site

NOW = datetime(2026, 9, 25, tzinfo=UTC)


class FakeSiteRepository:
    def __init__(self) -> None:
        self.sites: dict[UUID, Site] = {}
        self.referenced: set[UUID] = set()

    async def list_sites(self, include_inactive=False):
        return [s for s in self.sites.values() if include_inactive or s.is_active]

    async def get_by_id(self, site_id):
        return self.sites.get(site_id)

    async def get_by_code(self, code):
        return next((s for s in self.sites.values() if s.code == code), None)

    async def create_site(self, code, name, abbreviation, is_active):
        site = Site(uuid4(), code, name, is_active, NOW, NOW, abbreviation)
        self.sites[site.id] = site
        return site

    async def save_site(self, site):
        self.sites[site.id] = replace(site)
        return self.sites[site.id]

    async def delete_site(self, site_id):
        if site_id in self.referenced:
            raise ConflictError("La sede tiene solicitudes asociadas")
        self.sites.pop(site_id, None)


@pytest.mark.asyncio
async def test_site_service_normalizes_and_disables_a_site() -> None:
    repository = FakeSiteRepository()
    service = SiteService(repository)

    site = await service.create_site("  Bogota ", "  Bogotá   Centro ", " bg ")
    assert site.abbreviation == "BG"
    updated = await service.update_site(
        site.id,
        "Bogotá Principal",
        False,
        frozenset({"name", "is_active", "abbreviation"}),
        abbreviation="bog",
    )

    assert updated.abbreviation == "BOG"
    assert updated.code == "bogota"
    assert updated.name == "Bogotá Principal"
    assert updated.is_active is False
    assert await service.list_sites() == []
    assert await service.list_sites(include_inactive=True) == [updated]


@pytest.mark.asyncio
async def test_referenced_site_cannot_be_deleted() -> None:
    repository = FakeSiteRepository()
    service = SiteService(repository)
    site = await service.create_site("aco", "Aco", "AC")
    repository.referenced.add(site.id)

    with pytest.raises(ConflictError, match="desactívala"):
        await service.delete_site(site.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("abbreviation", ["A", "ACOSTA", "Á1", "GN"])
async def test_site_abbreviation_is_two_to_four_letters_and_not_generic(abbreviation) -> None:
    service = SiteService(FakeSiteRepository())

    with pytest.raises(InvalidInputError):
        await service.create_site("aco", "Aco", abbreviation)
