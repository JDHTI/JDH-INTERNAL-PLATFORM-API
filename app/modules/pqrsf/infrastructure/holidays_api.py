"""Working-day calendar backed by a public holidays API, with the local library as fallback."""

from __future__ import annotations

import logging
import time
from datetime import date

import httpx

from app.modules.pqrsf.domain.ports import BusinessCalendar

logger = logging.getLogger(__name__)

NAGER_URL = "https://date.nager.at/api/v3"
# After a failed lookup the library answers for this long before the API is tried again.
_RETRY_AFTER_SECONDS = 600.0


class NagerHolidayProvider:
    """Public holidays from Nager.Date (free, no key), cached per year.

    The offline ``holidays`` library backs it up, so a network failure never stops a
    scheduled job: the answer is just less fresh until the API answers again.
    """

    def __init__(
        self,
        country: str,
        fallback: BusinessCalendar,
        *,
        base_url: str = NAGER_URL,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Create the provider.

        Args:
            country (str): ISO country code, e.g. ``CO``.
            fallback (BusinessCalendar): Offline calendar used when the API fails.
            base_url (str): Nager.Date API root.
            http (httpx.AsyncClient | None): Injected transport for tests.
        """

        self._country = country
        self._fallback = fallback
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._years: dict[int, frozenset[date]] = {}
        self._retry_at = 0.0

    async def close(self) -> None:
        """Release the HTTP connections."""

        await self._http.aclose()

    async def is_business_day(self, day: date) -> bool:
        """Monday to Friday and not a public holiday of the country."""

        if day.weekday() >= 5:
            return False
        return day not in await self._holidays(day.year)

    async def _holidays(self, year: int) -> frozenset[date]:
        cached = self._years.get(year)
        if cached is not None:
            return cached
        if time.monotonic() >= self._retry_at:
            try:
                response = await self._http.get(
                    f"{self._base_url}/PublicHolidays/{year}/{self._country}"
                )
                response.raise_for_status()
                found = frozenset(
                    date.fromisoformat(item["date"])
                    for item in response.json()
                    # County-only holidays do not stop the whole organization.
                    if item.get("global", True)
                )
                if found:
                    self._years[year] = found
                    return found
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                logger.warning("Holidays API failed for %s; using the local calendar", year)
            self._retry_at = time.monotonic() + _RETRY_AFTER_SECONDS
        return frozenset(self._fallback.holidays(date(year, 1, 1), date(year, 12, 31)))
