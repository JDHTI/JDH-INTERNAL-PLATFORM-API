"""Free codes for a new SIESA price list (BI, 2026-11-19).

A new list takes a code of 3 letters or digits that SIESA does not have yet. The free ones are
listed in order (digits before letters, as SIESA sorts them) so the validator picks one
instead of trying codes until one passes.
"""

from __future__ import annotations

import string
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import product

ALPHABET = string.digits + string.ascii_uppercase
CODE_LENGTH = 3
TOTAL = len(ALPHABET) ** CODE_LENGTH  # 46 656
MAX_PAGE = 200


@dataclass(frozen=True)
class FreeCodes:
    """One page of free codes and the counts."""

    items: tuple[str, ...]
    free: int
    used: int
    # The last code of the page when more follow (the next page starts after it).
    next_after: str | None


def normalized(codes: Iterable[str | None]) -> set[str]:
    """The codes as SIESA compares them: trimmed and in upper case."""

    return {code.strip().upper() for code in codes if code and code.strip()}


def free_codes(used: Iterable[str | None], after: str | None, size: int) -> FreeCodes:
    """The 3-character codes not in ``used``, in order, after ``after`` (exclusive).

    Args:
        used: The codes SIESA has (and the ones a validated list already took).
        after: Start after this code (``None``: from the first, ``000``).
        size: Codes in the page (1 to :data:`MAX_PAGE`).
    """

    taken = {code for code in normalized(used) if len(code) == CODE_LENGTH}
    start = (after or "").strip().upper()
    items: list[str] = []
    more = False
    for letters in product(ALPHABET, repeat=CODE_LENGTH):
        code = "".join(letters)
        if code <= start or code in taken:
            continue
        if len(items) == size:
            more = True
            break
        items.append(code)
    used_count = sum(1 for code in taken if all(char in ALPHABET for char in code))
    return FreeCodes(
        items=tuple(items),
        free=TOTAL - used_count,
        used=used_count,
        next_after=items[-1] if more and items else None,
    )
