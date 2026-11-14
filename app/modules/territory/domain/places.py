"""Which municipality an address names, to catch points the agents geocoded in the wrong one.

The agents sometimes geocode a Bello address (``Calle 53 50-58 Bello``) with Medellín's street
grid, so the point lands in downtown Medellín. Only the municipality written at the end of the
address counts: ``Bello Horizonte`` is a barrio of Medellín, not Bello.
"""

from __future__ import annotations

import re
import unicodedata

# The municipalities of the Aburrá valley, as the official layer names them.
ABURRA_MUNICIPALITIES = (
    "Medellín",
    "Bello",
    "Itagüí",
    "Envigado",
    "Sabaneta",
    "La Estrella",
    "Caldas",
    "Copacabana",
    "Girardota",
    "Barbosa",
)
_TRAILING_NOISE = re.compile(r"(?:[\s,.;-]+(?:antioquia|colombia|ant))+\s*$")


def fold(text: str | None) -> str:
    """Lowercase text without accents, punctuation runs or extra spaces.

    Args:
        text (str | None): Any text.

    Returns:
        str: The folded text.
    """

    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().lower()


_BY_FOLDED = {fold(name): name for name in ABURRA_MUNICIPALITIES}
_AT_THE_END = re.compile(
    r"(?:^|[\s,.;#-])(" + "|".join(re.escape(key) for key in _BY_FOLDED) + r")[\s,.;]*$"
)


def municipality_in_address(address: str | None) -> str | None:
    """The Aburrá valley municipality an address ends with, if any.

    Args:
        address (str | None): Free-text address.

    Returns:
        str | None: The municipality's official name (``Itagüí``), or ``None``.
    """

    folded = _TRAILING_NOISE.sub("", fold(address))
    match = _AT_THE_END.search(folded)
    return _BY_FOLDED[match.group(1)] if match else None


def mentions(address: str | None, municipality: str) -> bool:
    """Whether the address names the municipality anywhere, as whole words.

    ``Cl. 64 #45a-30, Riachuelos, Itagüi, Medellín`` names Itagüí even though it ends with the
    metropolitan Medellín, and ``Envigado - Caldas`` names both.

    Args:
        address (str | None): Free-text address.
        municipality (str): A municipality name.

    Returns:
        bool: ``True`` when the address names it.
    """

    pattern = r"(?:^|[^a-z0-9])" + re.escape(fold(municipality)) + r"(?:$|[^a-z0-9])"
    return re.search(pattern, fold(address)) is not None


__all__ = ["ABURRA_MUNICIPALITIES", "fold", "mentions", "municipality_in_address"]
