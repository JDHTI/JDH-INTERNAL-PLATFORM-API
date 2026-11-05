"""In-process cache of what the platform read from the DWH, by sequence run."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class CachedEntry:
    """A value and the sequence run it was read under."""

    version: UUID | None
    value: Any


class MasterReadCache:
    """Least recently used entries, one per request (master, filters, page).

    An entry is never expired by time: a new run of the sequence makes it old (the
    service compares versions), and an old entry is still what the platform serves
    while the DWH reloads. The API runs as one instance, so the cache lives in memory.
    """

    def __init__(self, max_entries: int = 1024) -> None:
        """Create the cache.

        Args:
            max_entries (int): Entries kept; the least recently used goes first.
        """

        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[Hashable, CachedEntry] = OrderedDict()

    def get(self, key: Hashable) -> CachedEntry | None:
        """The last value stored under the key, whatever its run."""

        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def put(self, key: Hashable, version: UUID | None, value: Any) -> None:
        """Store a value read while the sequence's last run was ``version``."""

        self._entries[key] = CachedEntry(version, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
