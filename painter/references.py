"""参考图嗅探与归一化。

Pillow 是可选依赖：装了就做等比缩放与格式转换，没装就只做格式嗅探后原样透传。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from .errors import PainterError

try:  # pragma: no cover - 取决于运行环境
    from PIL import Image as PILImage
    from PIL import ImageOps

    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover
    PIL_AVAILABLE = False

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
_CONTENT_TYPE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class ReferenceImage:
    """一张待上传的参考图。"""

    data: bytes
    filename: str
    content_type: str


def detect_image_type(data: bytes) -> tuple[str, str] | None:
    """返回 ``(扩展名, MIME)``，无法识别时返回 None。"""
    if len(data) < 12:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if data[:2] == b"BM":
        return "bmp", "image/bmp"
    return None


def safe_filename(name: str, default_ext: str) -> str:
    """保留扩展名、去掉目录与不安全字符，得到上传用的文件名。"""
    base = (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, default_ext
    stem = _collapse(_SAFE_NAME.sub("_", stem).strip("._")) or "reference"
    suffix = _SAFE_NAME.sub("", suffix).strip("._").lower() or default_ext
    return f"{stem[:64]}.{suffix[:8]}"


def _collapse(text: str) -> str:
    return re.sub(r"_{2,}", "_", text)


def normalize_reference(data: bytes, filename: str, max_side: int) -> ReferenceImage:
    """把任意来源的图片整理成可以直接 multipart 上传的形式。"""
    if not data:
        raise PainterError("参考图内容为空，请重新发送图片。")
    if not PIL_AVAILABLE:
        kind = detect_image_type(data)
        if kind is None:
            raise PainterError("无法识别参考图格式，请使用 png、jpg 或 webp。")
        ext, content_type = kind
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise PainterError(f"参考图格式不支持（{content_type}），请使用 png、jpg 或 webp。")
        if len(data) > MAX_UPLOAD_BYTES:
            raise PainterError("参考图超过 8MB，请换一张更小的图片。")
        return ReferenceImage(data, safe_filename(filename, ext), content_type)

    return _normalize_with_pillow(data, filename, max_side)


def _normalize_with_pillow(data: bytes, filename: str, max_side: int) -> ReferenceImage:
    try:
        with PILImage.open(io.BytesIO(data)) as image:
            image.load()
            source_format = (image.format or "").upper()
            width, height = image.size
            content_type = PILImage.MIME.get(source_format, "")
            passthrough = (
                content_type in ALLOWED_CONTENT_TYPES
                and max(width, height) <= max_side
                and len(data) <= MAX_UPLOAD_BYTES
            )
            if passthrough:
                ext = _CONTENT_TYPE_EXT[content_type]
                return ReferenceImage(data, safe_filename(filename, ext), content_type)

            converted = ImageOps.exif_transpose(image).copy()
    except Exception as exc:  # Pillow 对损坏文件抛出的异常类型很杂
        raise PainterError(
            "参考图无法解析，可能已损坏或不是图片，请重新发送。",
            detail=str(exc),
        ) from exc

    side = max_side
    payload = b""
    ext, content_type = "png", "image/png"
    for _ in range(4):
        payload, ext, content_type = _encode(converted, side)
        if len(payload) <= MAX_UPLOAD_BYTES:
            break
        side = max(384, side // 2)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise PainterError("参考图压缩后仍然过大，请换一张更小的图片。")

    return ReferenceImage(payload, safe_filename(filename, ext), content_type)


def _encode(image: PILImage.Image, side: int) -> tuple[bytes, str, str]:
    working = image
    if max(working.size) > side:
        working = working.copy()
        working.thumbnail((side, side), PILImage.LANCZOS)

    keep_alpha = working.mode in ("RGBA", "LA") or "transparency" in working.info
    buffer = io.BytesIO()
    if keep_alpha:
        working.convert("RGBA").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "png", "image/png"
    working.convert("RGB").save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue(), "jpg", "image/jpeg"
