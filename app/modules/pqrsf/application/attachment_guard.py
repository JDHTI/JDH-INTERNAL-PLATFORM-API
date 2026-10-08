"""Rules every file that enters a case must pass, whether it came by email or from a comment."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import PurePosixPath

from app.core.errors import InvalidInputError
from app.modules.pqrsf.domain.ports import ImageProcessor, MalwareScanner

IMAGE_EXTENSIONS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
# Fixed, so a file is labeled the same on every machine (``mimetypes`` reads the OS
# registry: on Windows it calls a CSV ``application/vnd.ms-excel``).
_DOCUMENT_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
}
_OLE = b"\xd0\xcf\x11\xe0"
_ZIP = b"PK\x03\x04"
# What a file of each type must start with; plain text only has to be free of NUL bytes.
_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "doc": (_OLE,),
    "xls": (_OLE,),
    "ppt": (_OLE,),
    "docx": (_ZIP,),
    "xlsx": (_ZIP,),
    "pptx": (_ZIP,),
}
_TEXT_EXTENSIONS = {"txt", "csv"}


def safe_filename(name: str) -> str:
    """The file name without client paths or characters a file system rejects."""

    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = "".join(c for c in base if c.isprintable() and c not in '<>:"|?*').strip()
    return cleaned[:255]


def extension_of(name: str) -> str:
    """Lower-case extension of a name, empty when it has none."""

    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def _signature_ok(extension: str, content: bytes) -> bool:
    if extension in _TEXT_EXTENSIONS:
        return b"\x00" not in content[:8192]
    return any(content.startswith(prefix) for prefix in _SIGNATURES.get(extension, ()))


class AttachmentGuard:
    """Decide which files are accepted and clean the ones that are."""

    def __init__(
        self,
        image_processor: ImageProcessor,
        malware_scanner: MalwareScanner,
        allowed_extensions: Collection[str],
        max_attachments: int,
        max_attachment_bytes: int,
        max_total_attachment_bytes: int,
    ) -> None:
        """Create the guard.

        Args:
            image_processor (ImageProcessor): Re-encodes pictures (drops metadata and payloads).
            malware_scanner (MalwareScanner): Antivirus.
            allowed_extensions (Collection[str]): Lower-case extensions accepted.
            max_attachments (int): Files kept per message or comment.
            max_attachment_bytes (int): Largest single file.
            max_total_attachment_bytes (int): Largest sum of files.
        """

        self.image_processor = image_processor
        self.malware_scanner = malware_scanner
        self.allowed_extensions = {e.lower() for e in allowed_extensions}
        self.max_attachments = max_attachments
        self.max_attachment_bytes = max_attachment_bytes
        self.max_total_attachment_bytes = max_total_attachment_bytes

    def refuse(self, name: str, size: int, count: int, total: int) -> str | None:
        """Why a file is turned away before it is even read, or ``None``."""

        if extension_of(safe_filename(name)) not in self.allowed_extensions:
            return "tipo de archivo no permitido"
        if count >= self.max_attachments:
            return "supera el máximo de adjuntos por correo"
        if size > self.max_attachment_bytes:
            return "supera el tamaño máximo por archivo"
        if total + size > self.max_total_attachment_bytes:
            return "supera el tamaño máximo total"
        return None

    async def clean(self, name: str, content: bytes) -> tuple[str, str, str, bytes]:
        """Return (name, extension, content type, bytes) ready to store.

        Raises:
            InvalidInputError: The file is not what its extension says, or the antivirus refused it.
        """

        name = safe_filename(name)
        extension = extension_of(name)
        if not content or len(content) > self.max_attachment_bytes:
            raise InvalidInputError("archivo vacío o demasiado grande")
        if extension in IMAGE_EXTENSIONS:
            # Re-encoding strips metadata and anything appended after the image.
            image = await self.image_processor.sanitize(name, IMAGE_EXTENSIONS[extension], content)
            data, extension, content_type = image.content, image.extension, image.content_type
        else:
            if not _signature_ok(extension, content):
                raise InvalidInputError("el contenido no corresponde a su extensión")
            data = content
            content_type = _DOCUMENT_TYPES.get(extension, "application/octet-stream")
        try:
            await self.malware_scanner.scan(data)
        except InvalidInputError as exc:
            raise InvalidInputError("rechazado por el antivirus") from exc
        return name, extension, content_type, data
