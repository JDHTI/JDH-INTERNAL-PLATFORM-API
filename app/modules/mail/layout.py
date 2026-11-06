"""The JDH email layout: one branded template for every email that leaves to a customer.

The content is data (:class:`BrandedEmail`), so it can wait in a queue as JSON and be
rendered when the email is sent. Email clients ignore stylesheets and web fonts, so the
layout is tables with inline styles and system fonts; the logo travels inline (``cid:``)
because the intranet that hosts the platform is not reachable from outside.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from app.modules.mail.domain import OutgoingFile

LOGO_CONTENT_ID = "jdh-logo"
_LOGO_PATH = Path(__file__).parent / "assets" / "jdh-logo-white.png"

# Brand palette (manual de marca JDH).
_BLACK = "#1D1D1B"
_BLUE = "#16304E"
_DARK_GRAY = "#343430"
_LIGHT_GRAY = "#F2F2F2"
_MID_GRAY = "#D9D9D9"
_GRAY = "#919189"
_FONT = "'Segoe UI', Helvetica, Arial, sans-serif"
_COMPANY = "Juan D. Hoyos Distribuciones S.A.S."


@dataclass(frozen=True)
class BrandedEmail:
    """What one branded email says; the layout around it is always the same.

    Attributes:
        title (str): The headline («Radicamos tu queja»).
        paragraphs (tuple[str, ...]): The message. A blank line starts a new paragraph.
        greeting (str | None): «Hola Ana,».
        facts (tuple[tuple[str, str], ...]): Label and value rows shown in a box
            (radicado, tipo, fecha de recepción). The first value is highlighted.
        closing (tuple[str, ...]): Lines after the box (what happens next).
        eyebrow (str): Small line above the title: the module that writes.
        footnote (str | None): Small print at the bottom.
    """

    title: str
    paragraphs: tuple[str, ...]
    greeting: str | None = None
    facts: tuple[tuple[str, str], ...] = ()
    closing: tuple[str, ...] = ()
    eyebrow: str = "PQRSF · Servicio al cliente"
    footnote: str | None = None

    def to_payload(self) -> dict:
        """JSON form, kept with the queued email until it is sent."""

        return {
            "title": self.title,
            "paragraphs": list(self.paragraphs),
            "greeting": self.greeting,
            "facts": [list(fact) for fact in self.facts],
            "closing": list(self.closing),
            "eyebrow": self.eyebrow,
            "footnote": self.footnote,
        }

    @classmethod
    def from_payload(cls, value: dict) -> BrandedEmail:
        """Read the JSON form back."""

        return cls(
            title=str(value.get("title") or ""),
            paragraphs=tuple(str(p) for p in value.get("paragraphs") or ()),
            greeting=value.get("greeting"),
            facts=tuple((str(label), str(text)) for label, text in value.get("facts") or ()),
            closing=tuple(str(line) for line in value.get("closing") or ()),
            eyebrow=str(value.get("eyebrow") or BrandedEmail.eyebrow),
            footnote=value.get("footnote"),
        )


def render_text(email: BrandedEmail) -> str:
    """The plain-text version: what the queue lists and what a text-only reader sees."""

    parts: list[str] = []
    if email.greeting:
        parts.append(email.greeting)
    parts += [p.strip() for p in email.paragraphs if p.strip()]
    if email.facts:
        parts.append("\n".join(f"{label}: {value}" for label, value in email.facts))
    parts += [line.strip() for line in email.closing if line.strip()]
    parts.append(f"PQRSF JDH · {_COMPANY}")
    if email.footnote:
        parts.append(email.footnote)
    return "\n\n".join(parts)


def logo_file() -> OutgoingFile:
    """The white JDH logo, attached inline so the header shows it."""

    return OutgoingFile("jdh-logo.png", "image/png", _logo_bytes(), LOGO_CONTENT_ID)


@cache
def _logo_bytes() -> bytes:
    return _LOGO_PATH.read_bytes()


def render_html(email: BrandedEmail) -> str:
    """The full HTML document of the email."""

    return (
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_escape(email.title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{_LIGHT_GRAY};">'
        f"{render_fragment(email)}</body></html>"
    )


def render_fragment(email: BrandedEmail) -> str:
    """The layout without the document around it (for a reply inside a thread)."""

    body = [_eyebrow(email.eyebrow), _title(email.title)]
    if email.greeting:
        body.append(_paragraph(email.greeting))
    body += [_paragraph(text) for text in _split(email.paragraphs)]
    if email.facts:
        body.append(_facts(email.facts))
    body += [_paragraph(text) for text in _split(email.closing)]
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{_LIGHT_GRAY};font-family:{_FONT};">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:600px;background:#FFFFFF;border-collapse:separate;">'
        f'<tr><td style="background:{_BLACK};padding:28px 32px;">'
        f'<img src="cid:{LOGO_CONTENT_ID}" width="200" height="76" alt="JDH Juan D. Hoyos '
        'Distribuciones" style="display:block;border:0;width:200px;height:auto;"></td></tr>'
        f'<tr><td style="padding:32px 32px 8px 32px;">{"".join(body)}</td></tr>'
        f'<tr><td style="padding:8px 32px 28px 32px;">{_footer(email.footnote)}</td></tr>'
        "</table></td></tr></table>"
    )


def _escape(text: str) -> str:
    return html.escape(text.replace("\r\n", "\n"), quote=True)


def _lines(text: str) -> str:
    """Escaped text whose single line breaks stay as breaks."""

    return _escape(text.strip()).replace("\n", "<br>")


def _split(texts: tuple[str, ...]) -> list[str]:
    """Each blank-line-separated block of the texts as its own paragraph."""

    blocks: list[str] = []
    for text in texts:
        blocks += [block for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    return blocks


def _eyebrow(text: str) -> str:
    return (
        f'<p style="margin:0 0 8px 0;font-size:12px;line-height:16px;letter-spacing:1px;'
        f'text-transform:uppercase;color:{_GRAY};font-family:{_FONT};">{_escape(text)}</p>'
    )


def _title(text: str) -> str:
    return (
        f'<h1 style="margin:0 0 20px 0;font-size:24px;line-height:30px;font-weight:700;'
        f'color:{_BLACK};font-family:{_FONT};">{_escape(text)}</h1>'
    )


def _paragraph(text: str) -> str:
    return (
        f'<p style="margin:0 0 16px 0;font-size:15px;line-height:24px;color:{_DARK_GRAY};'
        f'font-family:{_FONT};">{_lines(text)}</p>'
    )


def _facts(facts: tuple[tuple[str, str], ...]) -> str:
    rows = []
    for index, (label, value) in enumerate(facts):
        # The first value (the radicado) is the one the customer will quote: highlighted.
        color = _BLUE if index == 0 else _BLACK
        size = "18px" if index == 0 else "15px"
        rows.append(
            f'<tr><td style="padding:{"0" if index == 0 else "12px"} 0 0 0;">'
            f'<p style="margin:0;font-size:11px;line-height:16px;letter-spacing:1px;'
            f'text-transform:uppercase;color:{_GRAY};font-family:{_FONT};">{_escape(label)}</p>'
            f'<p style="margin:2px 0 0 0;font-size:{size};line-height:24px;font-weight:700;'
            f'color:{color};font-family:{_FONT};">{_escape(value)}</p></td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:4px 0 24px 0;">'
        f'<tr><td style="background:{_LIGHT_GRAY};border-left:4px solid {_BLUE};'
        'padding:18px 20px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"{''.join(rows)}</table></td></tr></table>"
    )


def _footer(footnote: str | None) -> str:
    note = (
        f'<p style="margin:8px 0 0 0;font-size:12px;line-height:18px;color:{_GRAY};'
        f'font-family:{_FONT};">{_lines(footnote)}</p>'
        if footnote
        else ""
    )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td style="border-top:1px solid {_MID_GRAY};padding-top:16px;">'
        f'<p style="margin:0;font-size:13px;line-height:20px;font-weight:700;color:{_BLACK};'
        f'font-family:{_FONT};">PQRSF JDH</p>'
        f'<p style="margin:0;font-size:12px;line-height:18px;color:{_GRAY};'
        f'font-family:{_FONT};">{_COMPANY}</p>{note}</td></tr></table>'
    )
