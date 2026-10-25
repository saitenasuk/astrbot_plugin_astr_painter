"""OpenAI 兼容 Images API 客户端。

只做两件事：把请求发出去、把图片字节取回来。所有错误都转换成
:class:`~painter.errors.PainterError`，调用方不需要认识 httpx 的异常类型。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .errors import (
    PainterError,
    map_http_error,
    mentions_image_field,
    mentions_param_error,
    sanitize,
)
from .references import ReferenceImage, detect_image_type

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_BODY_CHARS = 2000
MAX_IMAGE_BYTES = 32 * 1024 * 1024

_FORMAT_EXT = {"png": "png", "jpeg": "jpg", "webp": "webp"}


@dataclass(frozen=True)
class GeneratedImage:
    """接口返回的图片字节。"""

    data: bytes
    ext: str
    revised_prompt: str | None = None


class ImagesClient:
    def __init__(self, settings: Settings, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = http_client
        self._owns_client = http_client is None
        # 兼容性记忆：接口拒绝参数或参考图字段名时，记住可用的那一套。
        self._edit_field = "image[]"
        self._minimal_payload = False
        self.last_response_preview = ""

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def generate(
        self,
        prompt: str,
        *,
        size: str = "",
        quality: str = "",
        output_format: str = "",
        background: str = "",
    ) -> GeneratedImage:
        payload = self._payload(
            prompt,
            size=size,
            quality=quality,
            output_format=output_format,
            background=background,
        )
        try:
            response = await self._post_json("generate", payload)
        except PainterError as exc:
            if self._minimal_payload or not self._shrinkable(exc):
                raise
            self._minimal_payload = True
            response = await self._post_json("generate", self._payload(prompt, size=size))
        return await self._to_image(response)

    async def edit(
        self,
        prompt: str,
        images: list[ReferenceImage],
        *,
        size: str = "",
        quality: str = "",
        output_format: str = "",
        background: str = "",
    ) -> GeneratedImage:
        if not images:
            raise PainterError("没有可用的参考图。")
        payload = self._payload(
            prompt,
            size=size,
            quality=quality,
            output_format=output_format,
            background=background,
        )
        field = self._edit_field
        try:
            response = await self._post_multipart("edit", payload, images, field)
        except PainterError as exc:
            if exc.status != 400:
                raise
            if mentions_image_field(exc.detail):
                field = "image" if field.endswith("[]") else "image[]"
                response = await self._post_multipart("edit", payload, images, field)
                self._edit_field = field
            elif self._shrinkable(exc):
                self._minimal_payload = True
                response = await self._post_multipart(
                    "edit", self._payload(prompt, size=size), images, field
                )
            else:
                raise
        return await self._to_image(response)

    def _shrinkable(self, exc: PainterError) -> bool:
        return exc.status == 400 and mentions_param_error(exc.detail)

    def _payload(
        self,
        prompt: str,
        *,
        size: str = "",
        quality: str = "",
        output_format: str = "",
        background: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self._settings.model, "prompt": prompt, "n": 1}
        if size and size != "auto":
            payload["size"] = size
        if self._minimal_payload:
            return payload
        if quality and quality != "auto":
            payload["quality"] = quality
        if background and background != "auto":
            payload["background"] = background
        if output_format:
            payload["output_format"] = output_format
        return payload

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = self._settings.timeout
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(timeout, connect=min(15, timeout)),
                "follow_redirects": True,
                "trust_env": True,
            }
            if self._settings.proxy:
                kwargs["proxy"] = self._settings.proxy
            self._client = httpx.AsyncClient(**kwargs)
            self._owns_client = True
        return self._client

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "User-Agent": USER_AGENT,
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        headers.update(self._settings.extra_headers)
        return headers

    async def _post_json(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._ensure_client()
        try:
            response = await client.post(
                self._settings.endpoint(operation),
                json=payload,
                headers=self._headers(json_body=True),
            )
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from exc
        except httpx.HTTPError as exc:
            raise self._network_error(exc) from exc
        return self._parse(response)

    async def _post_multipart(
        self,
        operation: str,
        payload: dict[str, Any],
        images: list[ReferenceImage],
        field: str,
    ) -> dict[str, Any]:
        client = self._ensure_client()
        files = [(field, (item.filename, item.data, item.content_type)) for item in images]
        try:
            response = await client.post(
                self._settings.endpoint(operation),
                data={key: str(value) for key, value in payload.items()},
                files=files,
                headers=self._headers(json_body=False),
            )
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from exc
        except httpx.HTTPError as exc:
            raise self._network_error(exc) from exc
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        raw = response.text or ""
        self.last_response_preview = sanitize(raw, (self._settings.api_key,), MAX_BODY_CHARS)
        if response.status_code >= 400:
            raise map_http_error(
                response.status_code, raw[:MAX_BODY_CHARS], (self._settings.api_key,)
            )
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise PainterError(
                "生图接口返回了非 JSON 内容，可能被网关或安全策略拦截。",
                detail=self.last_response_preview,
            ) from exc
        if not isinstance(payload, dict):
            raise PainterError("生图接口返回的数据结构异常。")
        return payload

    async def _to_image(self, payload: dict[str, Any]) -> GeneratedImage:
        item = _first_image_item(payload)
        if item is None:
            raise PainterError("生图接口没有返回图片数据（缺少 data[0]）。")
        revised = item.get("revised_prompt")
        revised_prompt = revised if isinstance(revised, str) and revised.strip() else None

        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded.strip():
            raw = _decode_base64(encoded)
            return self._build(raw, revised_prompt)

        url = item.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            raw = await self._download(url)
            return self._build(raw, revised_prompt)

        raise PainterError("生图接口既没有返回 b64_json，也没有返回图片链接。")

    def _build(self, data: bytes, revised_prompt: str | None) -> GeneratedImage:
        if not data:
            raise PainterError("生图接口返回了空图片数据。")
        if len(data) > MAX_IMAGE_BYTES:
            raise PainterError("生成的图片超过 32MB，无法发送。")
        detected = detect_image_type(data)
        ext = detected[0] if detected else _FORMAT_EXT.get(self._settings.output_format, "png")
        return GeneratedImage(data=data, ext=ext, revised_prompt=revised_prompt)

    async def _download(self, url: str) -> bytes:
        client = self._ensure_client()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await client.get(url, headers={"User-Agent": USER_AGENT})
                response.raise_for_status()
                content = response.content
                if len(content) > MAX_IMAGE_BYTES:
                    raise PainterError("生成的图片超过 32MB，无法发送。")
                return content
            except PainterError:
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise PainterError(
            "生成的图片下载失败，请稍后再试。",
            detail=str(last_error) if last_error else "",
        )

    def _timeout_error(self, exc: Exception) -> PainterError:
        return PainterError(
            f"生图请求超时（{self._settings.timeout} 秒），生成可能仍在服务端进行，请稍后查看或重试。",
            detail=str(exc),
            retryable=True,
        )

    def _network_error(self, exc: Exception) -> PainterError:
        return PainterError(
            "无法连接生图接口，请检查 api.base_url 与网络/代理设置。",
            detail=str(exc),
            retryable=True,
        )


def _first_image_item(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    item = data[0]
    if isinstance(item, dict):
        return item
    if isinstance(item, str) and item:
        return {"b64_json": item}
    return None


def _decode_base64(value: str) -> bytes:
    payload = value.strip()
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise PainterError("生图接口返回的图片数据无法解码。", detail=str(exc)) from exc
