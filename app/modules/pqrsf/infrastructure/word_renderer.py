"""Fill Word templates with docxtpl (Jinja marks inside a .docx)."""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence

from docx.shared import Mm
from docxtpl import DocxTemplate, InlineImage, Listing
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import InvalidInputError

logger = logging.getLogger(__name__)

# A phone photo is several MB: the OM prints it about 7 cm wide, so 1600 px is plenty.
_MAX_SIDE = 1600
_PHOTO_WIDTH = Mm(70)
# Marks whose text may span several lines (the case description, what the team wrote).
_MULTILINE = frozenset({"description", "immediate_actions", "request_note"})


def _photo(content: bytes) -> io.BytesIO | None:
    """The photo as a JPEG no larger than ``_MAX_SIDE``; ``None`` when it is not an image."""

    try:
        with Image.open(io.BytesIO(content)) as image:
            picture = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError):
        return None
    picture.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=85)
    buffer.seek(0)
    return buffer


class DocxTemplateRenderer:
    """Renders a docxtpl template from bytes; CPU work, so callers run it in a thread."""

    def marks(self, template: bytes) -> set[str]:
        """The marks a template uses.

        Raises:
            InvalidInputError: The file is not a Word document or its marks do not parse.
        """

        try:
            return set(DocxTemplate(io.BytesIO(template)).get_undeclared_template_variables())
        except Exception as exc:
            raise InvalidInputError(
                "La plantilla no es un Word válido o tiene marcas {{ }} mal escritas"
            ) from exc

    def render(self, template: bytes, context: dict, photos: Sequence[bytes]) -> bytes:
        """The filled document; files that are not images are left out of the photos."""

        document = DocxTemplate(io.BytesIO(template))
        values = {
            key: Listing(value) if key in _MULTILINE and isinstance(value, str) else value
            for key, value in context.items()
        }
        pictures = [buffer for buffer in (_photo(content) for content in photos) if buffer]
        values["photos"] = [InlineImage(document, p, width=_PHOTO_WIDTH) for p in pictures]
        try:
            document.render(values, autoescape=True)
        except Exception as exc:
            logger.exception("Could not render a Word template")
            raise InvalidInputError("La plantilla no se pudo llenar: revisa sus marcas") from exc
        out = io.BytesIO()
        document.save(out)
        return out.getvalue()
