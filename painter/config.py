"""配置读取：AstrBotConfig(dict) -> 不可变 Settings。

不做任何抛异常式的读取——配置错误在 :meth:`Settings.validate` 里汇总成用户
可读的提示，插件加载本身不因为配置没填而失败。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-1"
DEFAULT_PENDING_MESSAGE = "🎨 收到，正在绘制…"
DEFAULT_TOOL_INSTRUCTION = (
    "你可以使用 generate_image 工具为用户生成或修改图片。"
    "仅在用户明确要求出图、改图、画一张时调用；普通聊天、写作、设计建议、参数咨询不要调用。"
    "用户要求基于图片修改、合成、延续风格时，把 reference_images 设为 true。"
    "提示词要具体、完整，包含主体、风格、构图、光线与需要出现的文字。"
)

VALID_SIZES = ("auto", "1024x1024", "1536x1024", "1024x1536")
VALID_QUALITY = ("auto", "low", "medium", "high")
VALID_FORMATS = ("png", "jpeg", "webp")
VALID_BACKGROUND = ("auto", "transparent", "opaque")

_KNOWN_ENDPOINT_SUFFIXES = ("/images/generations", "/images/edits", "/images")


@dataclass(frozen=True)
class Settings:
    """插件全部配置项。"""

    # api
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout: int = 180
    proxy: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    # image
    size: str = "auto"
    quality: str = "auto"
    output_format: str = "png"
    background: str = "auto"
    max_reference_images: int = 4
    max_side: int = 2048

    # prompt
    prompt_prefix: str = ""
    prompt_suffix: str = ""

    # access
    whitelist: frozenset[str] = frozenset()
    blacklist: frozenset[str] = frozenset()
    admin_only: bool = False

    # quota
    quota_enabled: bool = False
    daily_limit: int = 20
    cooldown_seconds: int = 10
    admin_bypass: bool = True

    # runtime
    max_concurrent: int = 1
    queue_wait_seconds: int = 30
    enable_llm_tool: bool = True
    tool_instruction: str = DEFAULT_TOOL_INSTRUCTION
    pending_message: str = DEFAULT_PENDING_MESSAGE
    show_meta: bool = False
    storage_max_files: int = 500
    debug_log_response: bool = False

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> Settings:
        api = _section(config, "api")
        image = _section(config, "image")
        prompt = _section(config, "prompt")
        access = _section(config, "access")
        quota = _section(config, "quota")
        runtime = _section(config, "runtime")

        return cls(
            base_url=_str(api, "base_url", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            api_key=_str(api, "api_key", "").strip(),
            model=_str(api, "model", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            timeout=_int(api, "timeout", 180, minimum=10, maximum=900),
            proxy=_str(api, "proxy", "").strip(),
            extra_headers=_str_dict(api.get("extra_headers")),
            size=_choice(image, "size", "auto", VALID_SIZES),
            quality=_choice(image, "quality", "auto", VALID_QUALITY),
            output_format=_choice(image, "output_format", "png", VALID_FORMATS),
            background=_choice(image, "background", "auto", VALID_BACKGROUND),
            max_reference_images=_int(image, "max_reference_images", 4, minimum=1, maximum=16),
            max_side=_int(image, "max_side", 2048, minimum=256, maximum=4096),
            prompt_prefix=_str(prompt, "prefix", "").strip(),
            prompt_suffix=_str(prompt, "suffix", "").strip(),
            whitelist=_ids(access.get("whitelist")),
            blacklist=_ids(access.get("blacklist")),
            admin_only=_bool(access, "admin_only", False),
            quota_enabled=_bool(quota, "enabled", False),
            daily_limit=_int(quota, "daily_limit", 20, minimum=1, maximum=100000),
            cooldown_seconds=_int(quota, "cooldown_seconds", 10, minimum=0, maximum=86400),
            admin_bypass=_bool(quota, "admin_bypass", True),
            max_concurrent=_int(runtime, "max_concurrent", 1, minimum=1, maximum=8),
            queue_wait_seconds=_int(runtime, "queue_wait_seconds", 30, minimum=0, maximum=600),
            enable_llm_tool=_bool(runtime, "enable_llm_tool", True),
            tool_instruction=_str(runtime, "tool_instruction", DEFAULT_TOOL_INSTRUCTION).strip(),
            pending_message=_str(runtime, "pending_message", DEFAULT_PENDING_MESSAGE).strip()
            or DEFAULT_PENDING_MESSAGE,
            show_meta=_bool(runtime, "show_meta", False),
            storage_max_files=_int(runtime, "storage_max_files", 500, minimum=20, maximum=100000),
            debug_log_response=_bool(runtime, "debug_log_response", False),
        )

    def validate(self) -> list[str]:
        """返回阻塞性问题列表；空列表表示可以发起请求。"""
        problems: list[str] = []
        if not self.api_key:
            problems.append("还没有配置 API Key（api.api_key）。")
        if not self.base_url.startswith(("http://", "https://")):
            problems.append("api.base_url 需要是 http(s) 地址，例如 https://api.openai.com/v1。")
        if not self.model:
            problems.append("请配置 api.model。")
        return problems

    @property
    def normalized_base_url(self) -> str:
        url = self.base_url.strip().rstrip("/")
        lowered = url.lower()
        for suffix in _KNOWN_ENDPOINT_SUFFIXES:
            if lowered.endswith(suffix):
                url = url[: -len(suffix)].rstrip("/")
                break
        return url

    def endpoint(self, operation: str) -> str:
        leaf = "generations" if operation == "generate" else "edits"
        return f"{self.normalized_base_url}/images/{leaf}"


def _section(config: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    value = config.get(name)
    return value if isinstance(value, Mapping) else {}


def _str(section: Mapping[str, Any], key: str, default: str) -> str:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and value == 0:
        return default
    return str(value)


def _int(
    section: Mapping[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = section.get(key, default)
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _bool(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是"}
    return bool(value)


def _choice(
    section: Mapping[str, Any],
    key: str,
    default: str,
    allowed: tuple[str, ...],
) -> str:
    value = _str(section, key, default).strip().lower()
    return value if value in allowed else default


def _ids(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, (list, tuple, set)):
        raw = " ".join(str(item) for item in value)
    else:
        raw = str(value)
    parts = raw.replace(",", " ").replace("，", " ").split()
    return frozenset(part.strip() for part in parts if part.strip())


def _str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}
