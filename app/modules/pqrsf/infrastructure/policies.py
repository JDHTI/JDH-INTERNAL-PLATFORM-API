"""Business calendar and manager policy adapters for PQRSF follow-up."""

from __future__ import annotations

from collections.abc import Container
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

import holidays

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.areas.application.service import AreaService
from app.modules.identity.domain.principal import Principal


@lru_cache(maxsize=16)
def _country_holidays(country: str, first_year: int, last_year: int) -> frozenset[date]:
    return frozenset(holidays.country_holidays(country, years=range(first_year, last_year + 1)))


class HolidayCalendar:
    """Local dates in the business time zone and the country's public holidays."""

    def __init__(self, timezone: str = "America/Bogota", country: str = "CO") -> None:
        """Create the calendar.

        Args:
            timezone (str): IANA time zone used to date instants.
            country (str): ISO country whose holidays are non-working days.
        """

        self.zone = ZoneInfo(timezone)
        self.country = country

    def today(self) -> date:
        """Current date in the business time zone.

        Returns:
            date: Local date.
        """

        return datetime.now(self.zone).date()

    def local_date(self, moment: datetime) -> date:
        """Date of an instant in the business time zone.

        Args:
            moment (datetime): Aware instant.

        Returns:
            date: Local date.
        """

        return moment.astimezone(self.zone).date()

    def holidays(self, start: date, end: date) -> Container[date]:
        """Holidays in the years spanned by two dates.

        Args:
            start (date): First date of the range.
            end (date): Last date of the range.

        Returns:
            Container[date]: Holiday dates.
        """

        return _country_holidays(self.country, start.year, max(start.year, end.year))


class AreaManagerPolicy:
    """Platform administrators and members of one area manage PQRSF."""

    def __init__(self, areas: AreaService, area_slug: str | None) -> None:
        """Create the policy.

        Args:
            areas (AreaService): Area access use cases.
            area_slug (str | None): Area whose members manage PQRSF; blank
                leaves management to platform administrators only.
        """

        self.areas = areas
        self.area_slug = (area_slug or "").strip() or None

    async def is_manager(self, principal: Principal) -> bool:
        """Whether the caller manages PQRSF.

        Args:
            principal (Principal): The caller.

        Returns:
            bool: ``True`` for administrators and members of the managing area.
        """

        if principal.is_platform_admin:
            return True
        if self.area_slug is None:
            return False
        try:
            await self.areas.authorize(self.area_slug, principal)
        except (ForbiddenError, NotFoundError):
            return False
        return True
