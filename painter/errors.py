"""面向用户的错误类型与脱敏工具。

所有会回显给聊天窗口的文案都在这里生成，避免 API Key、Authorization 头、
带签名参数的图片链接泄露到群聊或日志。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

MAX_DETAIL = 300

_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=\-]+")
_URL_QUERY = re.compile(r"(https?://[^\s\"'<>?]+)\?[^\s\"'<>]*")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class PainterError(Exception):
    """可以直接把 ``user_message`` 发给用户的异常。"""

    def __init__(
        self,
        user_message: str,
        *,
        detail: str = "",
        status: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
        self.status = status
        self.retryable = retryable


class ConfigError(PainterError):
    """插件配置缺失或不合法。"""


def sanitize(text: str, secrets: Iterable[str] = (), limit: int = MAX_DETAIL) -> str:
    """去掉密钥、令牌、URL 查询串与控制字符，并截断到 ``limit``。"""
    if not text:
        return ""
    cleaned = _CONTROL.sub("", str(text))
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "***")
    cleaned = _SK_KEY.sub("***", cleaned)
    cleaned = _BEARER.sub("Bearer ***", cleaned)
    cleaned = _URL_QUERY.sub(r"\1", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "…"
    return cleaned


def extract_error_detail(body: str) -> str:
    """从接口错误响应里取出最有信息量的一句话。"""
    text = (body or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return text
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "detail", "code"):
                value = error.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("message", "detail", "msg"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text


_PARAM_HINTS = (
    "size",
    "quality",
    "background",
    "output_format",
    "output format",
    "unsupported",
    "unknown parameter",
    "unrecognized",
    "invalid value",
)


def mentions_param_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _PARAM_HINTS)


def mentions_image_field(text: str) -> bool:
    lowered = (text or "").lower()
    return "image" in lowered and any(
        hint in lowered for hint in ("field", "parameter", "missing", "required", "unknown")
    )


def map_http_error(status: int, body: str, secrets: Iterable[str] = ()) -> PainterError:
    """把上游 HTTP 状态码翻译成用户能看懂的一句话。"""
    detail = sanitize(extract_error_detail(body), secrets)
    suffix = f"（{detail}）" if detail else ""

    if status in (401, 403):
        message = "API Key 无效或无权限，请检查插件配置里的 api.api_key。"
    elif status == 404:
        message = "接口地址或模型名不存在，请确认 api.base_url 填到 /v1，且 api.model 正确。"
    elif status == 400:
        if mentions_param_error(detail):
            message = (
                f"接口拒绝了当前出图参数，请检查 size / quality / 输出格式是否被该模型支持{suffix}"
            )
        else:
            message = f"请求被接口拒绝{suffix}"
    elif status == 413:
        message = "上传的参考图太大，请换一张更小的图片。"
    elif status == 429:
        message = "上游限流或额度不足，请稍后再试。"
    elif status >= 500:
        message = f"上游服务异常（HTTP {status}），请稍后再试。"
    else:
        message = f"生图接口返回 HTTP {status}{suffix}"

    return PainterError(
        message, detail=detail, status=status, retryable=status >= 500 or status == 429
    )
