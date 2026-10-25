"""命令参数解析与出图参数换算（纯函数，不依赖 AstrBot）。"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

_SIZE_RE = re.compile(r"^(\d{2,5})\s*[x×*]\s*(\d{2,5})$", re.IGNORECASE)
_RATIO_RE = re.compile(r"^(\d{1,3})\s*[:：/]\s*(\d{1,3})$")
_TEXT_RATIO_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*[:：]\s*(\d{1,2})(?![\d.])")

KNOWN_RATIOS = (
    "1:1",
    "2:1",
    "1:2",
    "3:2",
    "2:3",
    "4:3",
    "3:4",
    "5:4",
    "4:5",
    "16:9",
    "9:16",
    "21:9",
    "9:21",
)

SQUARE_SIZE = "1024x1024"
LANDSCAPE_SIZE = "1536x1024"
PORTRAIT_SIZE = "1024x1536"

_FLAGS = {
    "--size": "size",
    "-s": "size",
    "--尺寸": "size",
    "--ratio": "ratio",
    "-r": "ratio",
    "--aspect": "ratio",
    "--aspect-ratio": "ratio",
    "--比例": "ratio",
    "--quality": "quality",
    "-q": "quality",
    "--质量": "quality",
    "--format": "format",
    "-f": "format",
    "--output-format": "format",
    "--格式": "format",
    "--style": "style",
    "--风格": "style",
    "--transparent": "transparent",
    "--transparent-background": "transparent",
    "--no-bg": "transparent",
    "--透明": "transparent",
}


@dataclass(frozen=True)
class Options:
    size: str = ""
    ratio: str = ""
    quality: str = ""
    output_format: str = ""
    style: str = ""
    transparent: bool = False


def parse_inline_options(text: str) -> tuple[Options, str]:
    """从指令文本里拆出 ``--flag value`` 参数，返回 ``(选项, 提示词)``。

    无法识别的 ``--xxx`` 会原样留在提示词里，避免吞掉用户内容。
    """
    if not text or not text.strip():
        return Options(), ""

    tokens = _tokenize(text)
    collected: dict[str, object] = {}
    prompt_parts: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name, inline_value = _split_flag(token)
        if name is None:
            prompt_parts.append(token)
            index += 1
            continue
        if name == "transparent":
            collected["transparent"] = True
            index += 1
            continue
        if inline_value is not None:
            value = inline_value
            index += 1
        elif index + 1 < len(tokens):
            value = tokens[index + 1]
            index += 2
        else:
            prompt_parts.append(token)
            index += 1
            continue

        if name == "size":
            _assign_size(collected, value)
        elif name == "ratio":
            collected["ratio"] = value
        elif name == "format":
            collected["output_format"] = value
        else:
            collected[name] = value

    ratio = str(collected.get("ratio", "") or "")
    prompt = " ".join(prompt_parts).strip()
    if not ratio:
        ratio = extract_ratio_from_text(prompt)

    return (
        Options(
            size=str(collected.get("size", "") or ""),
            ratio=ratio,
            quality=str(collected.get("quality", "") or ""),
            output_format=str(collected.get("output_format", "") or ""),
            style=str(collected.get("style", "") or ""),
            transparent=bool(collected.get("transparent", False)),
        ),
        prompt,
    )


def normalize_ratio(value: str) -> str:
    """把 ``16:9`` / ``16：9`` / ``16/9`` 统一成 ``16:9``。"""
    match = _RATIO_RE.match((value or "").strip())
    if not match:
        raise ValueError("比例格式应为 16:9 这样的「宽:高」。")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError("比例的宽高必须大于 0。")
    return f"{width}:{height}"


def ratio_to_size(ratio: str) -> str:
    """把比例映射到官方支持的像素尺寸档位。"""
    canonical = normalize_ratio(ratio)
    width, height = (int(part) for part in canonical.split(":"))
    value = width / height
    if 0.95 <= value <= 1.05:
        return SQUARE_SIZE
    return LANDSCAPE_SIZE if value > 1 else PORTRAIT_SIZE


def resolve_size(size: str, ratio: str, default: str) -> str:
    """决定最终提交的尺寸；``--size`` 优先于 ``--ratio``，都为空时用配置默认值。"""
    if size:
        if size.strip().lower() == "auto":
            return "auto"
        match = _SIZE_RE.match(size.strip())
        if not match:
            raise ValueError("尺寸格式应为 1024x1024 这样的「宽x高」，或 auto。")
        return f"{int(match.group(1))}x{int(match.group(2))}"
    if ratio:
        return ratio_to_size(ratio)
    return default


def extract_ratio_from_text(text: str) -> str:
    """从提示词里认领常见比例，认不出就返回空串。"""
    if not text:
        return ""
    for match in _TEXT_RATIO_RE.finditer(text):
        candidate = f"{int(match.group(1))}:{int(match.group(2))}"
        if candidate in KNOWN_RATIOS:
            return candidate
    return ""


def build_prompt(prompt: str, *, prefix: str = "", suffix: str = "", style: str = "") -> str:
    """按 前缀 → 用户提示词 → 风格词 → 后缀 的顺序拼接，不改写内容。"""
    parts = [part.strip() for part in (prefix, prompt, style, suffix)]
    return ", ".join(part for part in parts if part)


def _tokenize(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def _split_flag(token: str) -> tuple[str | None, str | None]:
    if not token.startswith("-") or token in ("-", "--"):
        return None, None
    if "=" in token:
        head, _, tail = token.partition("=")
        name = _FLAGS.get(head.lower())
        return (name, tail) if name else (None, None)
    name = _FLAGS.get(token.lower())
    return (name, None) if name else (None, None)


def _assign_size(collected: dict[str, object], value: str) -> None:
    stripped = (value or "").strip()
    if _RATIO_RE.match(stripped):
        collected["ratio"] = stripped
        return
    collected["size"] = stripped
