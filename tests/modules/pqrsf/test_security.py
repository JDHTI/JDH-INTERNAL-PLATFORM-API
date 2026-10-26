import asyncio
from io import BytesIO

import pytest
from app.core.errors import InvalidInputError, PayloadTooLargeError
from app.modules.pqrsf.infrastructure.security import (
    ClamAVScanner,
    PillowImageProcessor,
    VideoInspector,
)
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


@pytest.mark.asyncio
async def test_clamav_accepts_null_terminated_clean_response(monkeypatch) -> None:
    class FakeReader:
        async def read(self, size: int) -> bytes:
            return b"stream: OK\0"

    class FakeWriter:
        def write(self, content: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    async def fake_open_connection(host: str, port: int):
        return FakeReader(), FakeWriter()

    monkeypatch.setattr(asyncio, "open_connection", fake_open_connection)

    await ClamAVScanner("clamav", 3310, 1).scan(b"clean")


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "mime", "content", "stored"),
    [
        ("avería.mp4", "video/mp4", MP4, "video/mp4"),
        ("C:\\fotos\\caja.MOV", "video/quicktime", MOV, "video/quicktime"),
        ("clip.webm", "video/webm", WEBM, "video/webm"),
    ],
)
async def test_video_inspector_accepts_matching_videos(name, mime, content, stored) -> None:
    video = await VideoInspector(max_bytes=1024).inspect(name, mime, content)

    assert video.content_type == stored
    assert video.content == content
    assert "\\" not in video.original_filename


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "mime", "content", "error"),
    [
        ("clip.mp4", "video/mp4", MOV, InvalidInputError),
        ("clip.mp4", "video/quicktime", MP4, InvalidInputError),
        ("clip.avi", "video/x-msvideo", MP4, InvalidInputError),
        ("clip.mp4", "video/mp4", b"not a video at all", InvalidInputError),
        ("clip.mp4", "video/mp4", MP4 + b"\x00" * 2048, PayloadTooLargeError),
    ],
)
async def test_video_inspector_rejects_what_does_not_match(name, mime, content, error) -> None:
    with pytest.raises(error):
        await VideoInspector(max_bytes=1024).inspect(name, mime, content)
