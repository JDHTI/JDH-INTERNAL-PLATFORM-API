"""Clean the free-text phones, e-mails and websites the prospect files bring.

The agents write contact cells by hand: several numbers per cell, notes in parentheses
(``313 776 8650 (WhatsApp · central)``), scraped junk before an e-mail (``u003einfo@…``) or a
map link where an e-mail should be. The raw text is kept as well; this only extracts what a
person can dial or write to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

_SEGMENT_SPLIT = re.compile(r"\n|;|\|")
# A parenthesis holding a letter: the note of a number, not its area code.
_LABEL = re.compile(r"\(([^)]*[^\W\d_][^)]*)\)")
# A run of digits with the separators people type between them.
_RUN = re.compile(r"\+?\d[\d\s().\-/,]{5,}\d")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Escaped characters a scraper left in front of an address (``u003e`` is ``>``).
_ESCAPE_JUNK = re.compile(r"^(?:u00[0-9a-f]{2})+", re.IGNORECASE)
_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")
_COORDINATES = re.compile(
    r"(?:[?&](?:q|query|ll|destination)=|@)(-?\d{1,3}(?:\.\d+)?),\s*(-?\d{1,3}(?:\.\d+)?)"
)


@dataclass(frozen=True)
class Phone:
    """A number someone can dial."""

    # Digits only, without the country code: ``3137768650``.
    number: str
    display: str
    # The note that came with it: ``WhatsApp · central``.
    label: str | None
    # A Colombian mobile (10 digits starting with 3): it may take WhatsApp.
    is_mobile: bool


def _display(number: str) -> str:
    if len(number) == 10 and number.startswith("3"):
        return f"{number[:3]} {number[3:6]} {number[6:]}"
    if len(number) == 10 and number.startswith("60"):
        return f"({number[:3]}) {number[3:6]} {number[6:]}"
    if len(number) == 7:
        return f"{number[:3]} {number[3:]}"
    return number


def _digits(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) == 12 and digits.startswith("57"):
        digits = digits[2:]
    return digits


def _numbers(run: str) -> list[str]:
    """The numbers of a run: one, or several typed one after the other."""

    digits = _digits(run)
    if len(digits) <= 12:
        return [digits]
    numbers, current = [], ""
    for token in re.findall(r"\d+", run):
        current += token
        if len(current) >= 10:
            numbers.append(_digits(current))
            current = ""
    if current:
        numbers.append(current)
    return numbers


def parse_phones(raw: str | None) -> list[Phone]:
    """The numbers of a phone cell, without repeats, in the order they appear.

    Args:
        raw (str | None): Cell text.

    Returns:
        list[Phone]: Numbers of 7 to 10 digits; anything else is left out.
    """

    if not raw:
        return []
    found: dict[str, Phone] = {}
    for segment in _SEGMENT_SPLIT.split(str(raw)):
        # A parenthesis with letters is a note; one without is an area code: ``(604) 235 2699``.
        labels = [note.strip() for note in _LABEL.findall(segment)]
        for run in _RUN.findall(_LABEL.sub(" ", segment)):
            for number in _numbers(run):
                if not 7 <= len(number) <= 10 or number in found:
                    continue
                found[number] = Phone(
                    number=number,
                    display=_display(number),
                    label=" · ".join(labels) or None,
                    is_mobile=len(number) == 10 and number.startswith("3"),
                )
    return list(found.values())


def parse_emails(raw: str | None) -> list[str]:
    """The e-mail addresses of a cell, lowercase and without repeats.

    Args:
        raw (str | None): Cell text.

    Returns:
        list[str]: Valid-looking addresses; a link or a phone yields nothing.
    """

    if not raw:
        return []
    emails: list[str] = []
    for match in _EMAIL.findall(str(raw)):
        email = _ESCAPE_JUNK.sub("", match).lower().strip(".")
        if "@" in email and not email.startswith("@") and email not in emails:
            emails.append(email)
    return emails


def clean_website(value: str | None) -> str | None:
    """An ``http(s)`` link, or ``None`` when the cell holds no website.

    Args:
        value (str | None): Cell text or its hyperlink target.

    Returns:
        str | None: The link; a bare domain gets ``https://``.
    """

    if not value:
        return None
    text = str(value).strip()
    if re.match(r"^https?://\S+$", text, re.IGNORECASE):
        return text
    if _DOMAIN.match(text) and "@" not in text:
        return f"https://{text}"
    return None


def coordinates_from_link(link: str | None) -> tuple[float, float] | None:
    """The ``(latitude, longitude)`` of a map link such as ``…/maps?q=6.28,-75.56``.

    Args:
        link (str | None): A Google Maps (or similar) URL.

    Returns:
        tuple[float, float] | None: The point, or ``None`` when the link has none.
    """

    if not link:
        return None
    match = _COORDINATES.search(unquote(str(link)))
    if match is None:
        return None
    latitude, longitude = float(match.group(1)), float(match.group(2))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


__all__ = ["Phone", "clean_website", "coordinates_from_link", "parse_emails", "parse_phones"]
