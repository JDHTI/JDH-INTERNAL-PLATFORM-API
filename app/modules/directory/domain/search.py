"""Accent-insensitive search over employees without a database extension.

Every employee keeps ``search_text``: its name, position, job title and email folded to
lowercase ASCII words separated by single spaces, with a leading space. A query matches
when each of its words starts a word of that text (``LIKE '% term%'``), in any order:
``"maria per"`` finds "María Pérez".
"""

from __future__ import annotations

import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9]+")
MAX_TERMS = 8


def normalize_text(value: str | None) -> str:
    """Fold a text to lowercase ASCII words separated by single spaces.

    Args:
        value (str | None): Any text; ``None`` folds to an empty string.

    Returns:
        str: The words, e.g. ``"ana maria perez gomez"`` for "Ana María Pérez-Gómez".
    """

    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_WORD.sub(" ", ascii_text.lower()).strip()


def search_terms(query: str | None) -> list[str]:
    """The words of a query, at most ``MAX_TERMS``.

    Args:
        query (str | None): What the person typed.

    Returns:
        list[str]: Normalized, de-duplicated words in their order.
    """

    words = normalize_text(query).split()
    return list(dict.fromkeys(words))[:MAX_TERMS]


def build_search_text(*parts: str | None) -> str:
    """The searchable text of an employee.

    Args:
        *parts (str | None): Name, position, job title, email...

    Returns:
        str: The folded words with a leading space, so every word is preceded by one.
    """

    words = " ".join(normalize_text(part) for part in parts if part).split()
    return " " + " ".join(dict.fromkeys(words)) if words else ""
