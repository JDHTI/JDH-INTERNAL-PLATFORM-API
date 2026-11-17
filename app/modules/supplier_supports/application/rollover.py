"""Programmed supports replace the previous one (rules v2): the daily job and its alerts.

When a support of the same class and scope is programmed after another one in force, the
previous one ends the day before the new one starts: the job cuts it (``vencimiento``) so the
two never cross. Creating with ``on_overlap = vencer_anterior`` already leaves them chained;
the job also resolves crossings that came before the rule (loads, imports).

The alerts list each programmed support with the one it will replace, so BI decides: leave it
programmed (the previous one stays until the day before) or expire the previous one now
(``POST /supplier-supports/retire`` with ``vencer``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from app.modules.supplier_supports.domain.entities import Support
from app.modules.supplier_supports.domain.overlap import Supersede, dates_cross, same_scope
from app.modules.supplier_supports.domain.ports import Calendar
from app.modules.supplier_supports.domain.vocabulary import ACTIVE_KINDS

ACTOR = "renovacion-apoyos"


class RolloverRepository(Protocol):
    async def live_in_groups(self, kinds: Sequence[str]) -> list[Support]: ...

    async def supersede(
        self, items: Sequence[Supersede], actor: str, now: datetime
    ) -> list[Support]: ...


@dataclass(frozen=True)
class Replacement:
    """A programmed support and the one in force it replaces."""

    programmed: Support
    previous: Support
    # The previous one's last day once the programmed one starts.
    previous_ends: date
    # Whether the previous one is already chained (ends that day) or still crosses.
    chained: bool


def _key(support: Support) -> tuple[Any, ...]:
    data = support.data
    return (
        data.commercial_supplier_id,
        data.kind,
        data.customer_nit or "",
        data.product_sku or (data.supplier_sku or "").upper(),
    )


def successions(supports: Sequence[Support]) -> list[tuple[Support, Support]]:
    """``(previous, next)`` of each support followed by another of the same scope.

    Inside each scope the supports are ordered by start; a support is followed by the next
    one that starts strictly later. Two that start the same day cannot be told apart and
    are left out (they are a crossing for a person).
    """

    groups: dict[tuple[Any, ...], list[Support]] = {}
    for support in supports:
        groups.setdefault(_key(support), []).append(support)
    pairs: list[tuple[Support, Support]] = []
    for members in groups.values():
        ordered = sorted(members, key=lambda item: item.data.valid_from or date.min)
        for index, previous in enumerate(ordered):
            start = previous.data.valid_from or date.min
            following = next(
                (
                    item
                    for item in ordered[index + 1 :]
                    if (item.data.valid_from or date.min) > start
                    and same_scope(previous.data, item.data)
                ),
                None,
            )
            if following is not None:
                pairs.append((previous, following))
    return pairs


class SupportRolloverService:
    """Chain programmed supports after the previous one, and list what will be replaced."""

    def __init__(
        self,
        repository: RolloverRepository,
        calendar: Calendar,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.calendar = calendar
        self._clock = clock or (lambda: datetime.now(UTC))

    async def replacements(self) -> list[Replacement]:
        """Programmed supports (start after today) and the support in force they replace."""

        today = self.calendar.today()
        found: list[Replacement] = []
        for previous, following in successions(await self.repository.live_in_groups(ACTIVE_KINDS)):
            start = following.data.valid_from
            if start is None or start <= today:
                continue
            if previous.data.valid_from and previous.data.valid_from > today:
                continue  # the previous one is programmed too
            if previous.data.valid_to is not None and previous.data.valid_to < today:
                continue
            ends = start - timedelta(days=1)
            found.append(
                Replacement(
                    following,
                    previous,
                    ends,
                    chained=previous.data.valid_to == ends,
                )
            )
        return sorted(found, key=lambda item: item.programmed.data.valid_from or date.max)

    async def run(self) -> Mapping[str, Any]:
        """Cut every previous support that still crosses the next one (the daily job)."""

        plan: dict[Any, Supersede] = {}
        for previous, following in successions(await self.repository.live_in_groups(ACTIVE_KINDS)):
            if not dates_cross(previous.data, following.data) or following.data.valid_from is None:
                continue
            ends = following.data.valid_from - timedelta(days=1)
            plan.setdefault(
                previous.id,
                Supersede(
                    previous.id,
                    previous.version,
                    ends,
                    f"Reemplazado por el apoyo programado desde {following.data.valid_from}",
                ),
            )
        changed = (
            await self.repository.supersede(list(plan.values()), ACTOR, self._clock())
            if plan
            else []
        )
        return {
            "cut": sum(1 for item in changed if item.data.status != "anulado"),
            "voided": sum(1 for item in changed if item.data.status == "anulado"),
            "errors": [],
        }


__all__ = ["Replacement", "SupportRolloverService", "successions"]
