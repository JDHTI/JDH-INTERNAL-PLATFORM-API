"""Image sanitation and antivirus adapters for public PQRSF uploads."""

from __future__ import annotations

import asyncio
import struct
from io import BytesIO
from pathlib import PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import DependencyUnavailableError, InvalidInputError, PayloadTooLargeError
from app.modules.pqrsf.domain.entities import SanitizedImage

_FORMATS = {
    "JPEG": ("jpg", "image/jpeg", {"image/jpeg", "image/jpg"}),
    "PNG": ("png", "image/png", {"image/png"}),
    "WEBP": ("webp", "image/webp", {"image/webp"}),
}
_EXTENSIONS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


class PillowImageProcessor:
    """Validate, decode and re-encode allowed image formats."""

    def __init__(self, max_bytes: int, max_pixels: int) -> None:
        """Create an image processor with request-specific safety limits.

        Args:
            max_bytes (int): Largest original upload in bytes.
            max_pixels (int): Largest decoded pixel count.
        """

        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    async def sanitize(
        self, filename: str | None, content_type: str | None, content: bytes
    ) -> SanitizedImage:
        """Return a clean rasterized image or raise a validation error."""

        if len(content) > self.max_bytes:
            raise PayloadTooLargeError("Cada imagen supera el tamaño máximo permitido")
        safe_name = self._filename(filename)
        source_extension = safe_name.rsplit(".", 1)[1].lower()
        source_format = _EXTENSIONS.get(source_extension)
        if source_format is None:
            raise InvalidInputError("Solo se permiten imágenes JPG, JPEG, PNG o WebP")

        try:
            with Image.open(BytesIO(content)) as image:
                detected = image.format
                self._validate_header(image, detected, source_format, content_type)
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
                clean = ImageOps.exif_transpose(image)
                output = BytesIO()
                extension, media_type, _ = _FORMATS[detected]
                output_format = detected
                if output_format == "JPEG":
                    clean = clean.convert("RGB")
                    clean.save(output, format="JPEG", quality=90, optimize=True, exif=b"")
                elif output_format == "PNG":
                    clean.save(output, format="PNG", optimize=True, exif=b"")
                else:
                    if clean.mode not in {"RGB", "RGBA"}:
                        clean = clean.convert("RGBA")
                    clean.save(output, format="WEBP", quality=90, method=4, exif=b"")
                safe_content = output.getvalue()
        except (
            Image.DecompressionBombError,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
        ) as exc:
            raise InvalidInputError("La imagen no es válida o está dañada") from exc

        if not safe_content:
            raise InvalidInputError("La imagen está vacía")
        return SanitizedImage(
            original_filename=safe_name,
            extension=extension,
            content_type=media_type,
            content=safe_content,
        )

    def _filename(self, filename: str | None) -> str:
        """Remove client paths and reject names without a supported extension."""

        safe_name = PurePosixPath((filename or "").replace("\\", "/")).name.strip()
        if not safe_name or len(safe_name) > 255 or "." not in safe_name:
            raise InvalidInputError("La imagen debe tener un nombre y una extensión permitida")
        if any(character in safe_name for character in ("\x00", ":")):
            raise InvalidInputError("El nombre de la imagen no es válido")
        return safe_name

    def _validate_header(
        self, image: Image.Image, detected: str | None, source_format: str, content_type: str | None
    ) -> None:
        if detected not in _FORMATS or detected != source_format:
            raise InvalidInputError(
                "La extensión, el MIME y el formato real de la imagen no coinciden"
            )
        if not content_type or content_type.lower() not in _FORMATS[detected][2]:
            raise InvalidInputError("El tipo MIME de la imagen no está permitido")
        width, height = image.size
        if width < 1 or height < 1 or width * height > self.max_pixels:
            raise PayloadTooLargeError("La imagen supera la resolución máxima permitida")
        if getattr(image, "n_frames", 1) != 1:
            raise InvalidInputError("No se permiten imágenes animadas")


class ClamAVScanner:
    """Scan bytes through ClamAV's INSTREAM protocol."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        """Create a scanner client.

        Args:
            host (str): ClamAV daemon hostname.
            port (int): ClamAV daemon TCP port.
            timeout (float): Connection and response timeout in seconds.
        """

        self.host = host
        self.port = port
        self.timeout = timeout

    async def scan(self, content: bytes) -> None:
        """Reject malware or fail closed when ClamAV is unavailable."""

        writer = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
            writer.write(b"zINSTREAM\0")
            for offset in range(0, len(content), 1024 * 1024):
                chunk = content[offset : offset + 1024 * 1024]
                writer.write(struct.pack("!I", len(chunk)))
                writer.write(chunk)
            writer.write(b"\0\0\0\0")
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
            result = await asyncio.wait_for(reader.read(1024), timeout=self.timeout)
        except (OSError, TimeoutError) as exc:
            raise DependencyUnavailableError(
                "El antivirus no está disponible; intenta nuevamente"
            ) from exc
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()

        if b"FOUND" in result:
            raise InvalidInputError("La imagen fue rechazada por el antivirus")
        if not result.rstrip().endswith(b"OK"):
            raise DependencyUnavailableError("El antivirus no pudo validar la imagen")


__all__ = ["ClamAVScanner", "PillowImageProcessor"]
