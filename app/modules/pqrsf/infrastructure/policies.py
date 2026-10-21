"""Business calendar and manager policy adapters for PQRSF follow-up."""

from __future__ import annotations

from collections.abc import Container
from datetime import date, datetime
from functools import lru_cache
from uuid import UUID
from zoneinfo import ZoneInfo

import holidays

from app.core.errors import NotFoundError
from app.modules.areas.application.service import AreaService
from app.modules.areas.infrastructure.policies import AreaManagerPolicy


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


class AreaTeamDirectory:
    """The members of the managing area: who receives the team-wide emails."""

    def __init__(self, areas: AreaService, area_slug: str | None) -> None:
        """Create the directory.

        Args:
            areas (AreaService): Area use cases.
            area_slug (str | None): Area whose members form the team; blank means nobody.
        """

        self.areas = areas
        self.area_slug = (area_slug or "").strip() or None

    async def team_user_ids(self) -> list[UUID]:
        """User identifiers of the area's members (empty when the area does not exist)."""

        if self.area_slug is None:
            return []
        try:
            return await self.areas.member_user_ids(self.area_slug)
        except NotFoundError:
            return []


__all__ = ["AreaManagerPolicy", "AreaTeamDirectory", "HolidayCalendar"]
