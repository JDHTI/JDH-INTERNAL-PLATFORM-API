from io import BytesIO

import pytest
from app.core.errors import InvalidInputError, PayloadTooLargeError
from app.modules.pqrsf.infrastructure.security import PillowImageProcessor
from PIL import Image


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (4, 3), (12, 34, 56))
    image.save(output, format=image_format)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "image_format", "extension"),
    [
        ("evidence.jpg", "image/jpeg", "JPEG", "jpg"),
        ("evidence.jpeg", "image/jpeg", "JPEG", "jpg"),
        ("evidence.png", "image/png", "PNG", "png"),
        ("evidence.webp", "image/webp", "WEBP", "webp"),
    ],
)
async def test_processor_accepts_allowed_images_and_normalizes_them(
    filename: str, content_type: str, image_format: str, extension: str
) -> None:
    processor = PillowImageProcessor(max_bytes=1024 * 1024, max_pixels=100)

    result = await processor.sanitize(filename, content_type, _image_bytes(image_format))

    assert result.original_filename == filename
    assert result.extension == extension
    assert result.content_type == content_type
    with Image.open(BytesIO(result.content)) as image:
        assert image.format == image_format
        assert image.size == (4, 3)
        assert not image.getexif()


@pytest.mark.asyncio
async def test_processor_rejects_mismatched_extension_and_mime() -> None:
    processor = PillowImageProcessor(max_bytes=1024 * 1024, max_pixels=100)

    with pytest.raises(InvalidInputError, match="formato real"):
        await processor.sanitize("evidence.png", "image/png", _image_bytes("JPEG"))

    with pytest.raises(InvalidInputError, match="MIME"):
        await processor.sanitize("evidence.jpg", "image/png", _image_bytes("JPEG"))


@pytest.mark.asyncio
async def test_processor_rejects_oversized_and_unsupported_images() -> None:
    processor = PillowImageProcessor(max_bytes=20, max_pixels=100)

    with pytest.raises(PayloadTooLargeError, match="tama"):
        await processor.sanitize("evidence.jpg", "image/jpeg", _image_bytes("JPEG"))

    processor = PillowImageProcessor(max_bytes=1024 * 1024, max_pixels=4)
    with pytest.raises(PayloadTooLargeError, match="resoluci"):
        await processor.sanitize("evidence.png", "image/png", _image_bytes("PNG"))

    with pytest.raises(InvalidInputError, match="JPG"):
        await processor.sanitize("evidence.gif", "image/gif", _image_bytes("PNG"))
