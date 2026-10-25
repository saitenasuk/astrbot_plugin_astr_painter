import pytest

from painter import references as refs
from painter.errors import PainterError
from tests.conftest import make_jpeg_bytes, make_png_bytes


def test_detect_image_type():
    assert refs.detect_image_type(make_png_bytes()) == ("png", "image/png")
    assert refs.detect_image_type(make_jpeg_bytes()) == ("jpg", "image/jpeg")
    assert refs.detect_image_type(b"RIFF0000WEBPVP8 ") == ("webp", "image/webp")
    assert refs.detect_image_type(b"GIF89a" + b"0" * 8) == ("gif", "image/gif")
    assert refs.detect_image_type(b"xx") is None


def test_safe_filename_strips_directories():
    assert refs.safe_filename("a/b/c.png", "png") == "c.png"
    assert refs.safe_filename("C:\\tmp\\photo", "jpg") == "photo.jpg"
    assert refs.safe_filename("怪 名字.png", "jpg") == "reference.png"


def test_small_png_passes_through_unchanged():
    data = make_png_bytes((16, 16))
    result = refs.normalize_reference(data, "a.png", 2048)
    assert result.data == data
    assert result.content_type == "image/png"


def test_large_image_is_scaled_down():
    data = make_png_bytes((1200, 600))
    result = refs.normalize_reference(data, "big.png", 512)
    assert result.content_type in ("image/png", "image/jpeg")
    from io import BytesIO

    from PIL import Image as PILImage

    with PILImage.open(BytesIO(result.data)) as image:
        assert max(image.size) <= 512


def test_unsupported_format_is_converted():
    buffer = __import__("io").BytesIO()
    from PIL import Image as PILImage

    PILImage.new("RGB", (24, 24), "green").save(buffer, format="BMP")
    result = refs.normalize_reference(buffer.getvalue(), "x.bmp", 2048)
    assert result.content_type in ("image/jpeg", "image/png")


def test_transparent_image_stays_png():
    buffer = __import__("io").BytesIO()
    from PIL import Image as PILImage

    PILImage.new("RGBA", (800, 800), (0, 0, 0, 0)).save(buffer, format="PNG")
    result = refs.normalize_reference(buffer.getvalue(), "t.png", 256)
    assert result.content_type == "image/png"


def test_empty_and_corrupt_input():
    with pytest.raises(PainterError):
        refs.normalize_reference(b"", "x.png", 1024)
    with pytest.raises(PainterError):
        refs.normalize_reference(b"\x89PNG\r\n\x1a\ntrash", "x.png", 1024)


def test_fallback_without_pillow(monkeypatch):
    monkeypatch.setattr(refs, "PIL_AVAILABLE", False)
    data = make_png_bytes()
    result = refs.normalize_reference(data, "a.png", 2048)
    assert result.data == data

    with pytest.raises(PainterError) as captured:
        refs.normalize_reference(b"GIF89a" + b"0" * 8, "a.gif", 2048)
    assert "不支持" in captured.value.user_message

    monkeypatch.setattr(refs, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(PainterError) as captured:
        refs.normalize_reference(data, "a.png", 2048)
    assert "8MB" in captured.value.user_message
