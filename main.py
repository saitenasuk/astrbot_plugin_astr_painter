"""Astr Painter：调用 OpenAI 兼容 Images API 的文生图 / 图生图插件。

- ``/生图 <提示词>`` 文生图
- ``/改图 <提示词>`` 图生图（同消息带图，或回复/引用一张图）
- ``generate_image`` LLM 工具，聊天中自然语言触发

生成放在后台任务里执行：长耗时请求不会阻塞 AstrBot 的消息处理，结果出来后再
通过 ``event.send`` 推送到原会话。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .painter.client import GeneratedImage, ImagesClient
from .painter.config import Settings
from .painter.errors import PainterError
from .painter.options import Options, build_prompt, parse_inline_options, resolve_size
from .painter.references import ReferenceImage, normalize_reference
from .painter.storage import ImageStore

PLUGIN_NAME = "astrbot_plugin_astr_painter"
AUTHOR = "saitenasuk"
DESC = "调用 OpenAI 兼容 Images API 的文生图 / 图生图插件"
VERSION = "0.1.0"
MAX_CHAIN_DEPTH = 3


@register(PLUGIN_NAME, AUTHOR, DESC, VERSION)
class AstrPainterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.config: Any = config or {}
        self.settings = Settings.from_config(self.config)
        self._data_dir = StarTools.get_data_dir(plugin_name=PLUGIN_NAME)
        self._store = ImageStore(self._data_dir / "images", self.settings.storage_max_files)
        self._client: ImagesClient | None = None
        # 并发上限。AstrBot 要求 Python >= 3.12，Semaphore.acquire 被取消时框架
        # 会自己把 permit 还回去，所以超时分支不需要额外补偿。
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._cooldowns: dict[str, float] = {}

    async def initialize(self) -> None:
        self._store.ensure_ready()
        removed = self._store.prune()
        for problem in self.settings.validate():
            logger.warning("Astr Painter 配置未完成：%s", problem)
        logger.info(
            "Astr Painter 已就绪：模型=%s，最多同时生成 %s 张，已清理 %s 个过期文件。",
            self.settings.model,
            self.settings.max_concurrent,
            removed,
        )

    async def terminate(self) -> None:
        pending = [*self._tasks, *self._inflight.values()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        self._inflight.clear()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ 指令

    @filter.command("生图")
    async def cmd_generate(self, event: AstrMessageEvent):
        """根据提示词生成图片（文生图）。"""
        async for result in self._handle_command(event, command="生图", with_reference=False):
            yield result

    @filter.command("改图")
    async def cmd_edit(self, event: AstrMessageEvent):
        """基于当前消息或引用消息里的图片修改图片（图生图）。"""
        async for result in self._handle_command(event, command="改图", with_reference=True):
            yield result

    @filter.command("生图帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """查看生图插件的用法与当前模型配置。"""
        yield event.plain_result(self._help_text()).stop_event()

    async def _handle_command(self, event: AstrMessageEvent, *, command: str, with_reference: bool):
        settings = self.settings
        options, prompt = parse_inline_options(_strip_command(_event_text(event), command))
        if not prompt:
            yield event.plain_result(_usage_text(command)).stop_event()
            return

        denial = self._check_access(event)
        if denial:
            yield event.plain_result(denial).stop_event()
            return

        problems = settings.validate()
        if problems:
            yield event.plain_result("生图功能还没配置好：" + " ".join(problems)).stop_event()
            return

        note = ""
        references: list[ReferenceImage] = []
        try:
            if with_reference:
                paths = await self._collect_image_paths(event)
                if not paths:
                    yield event.plain_result(
                        "没有找到参考图。请在同一条消息里发图，或回复/引用一张图后再用 /改图。"
                    ).stop_event()
                    return
                references = self._load_references(paths)
            elif _walk_images(event.get_messages()):
                note = "（检测到图片：要基于图片修改请用 /改图）"
            size = resolve_size(options.size, options.ratio, settings.size)
        except PainterError as exc:
            yield event.plain_result(exc.user_message).stop_event()
            return
        except ValueError as exc:
            yield event.plain_result(str(exc)).stop_event()
            return

        denial, consumed = await self._consume_quota(event)
        if denial:
            yield event.plain_result(denial).stop_event()
            return

        if not await self._acquire_slot():
            await self._refund_quota(event, consumed)
            yield event.plain_result("当前生图任务较多，请稍后再试。").stop_event()
            return

        task = asyncio.create_task(
            self._generate_and_send(
                event=event,
                prompt=build_prompt(
                    prompt,
                    prefix=settings.prompt_prefix,
                    suffix=settings.prompt_suffix,
                    style=options.style,
                ),
                references=references,
                size=size,
                quality=options.quality or settings.quality,
                output_format=options.output_format or settings.output_format,
                background="transparent" if options.transparent else settings.background,
                consumed_quota=consumed,
                note=note,
            )
        )
        self._track(task)
        yield event.plain_result(settings.pending_message).stop_event()

    # -------------------------------------------------------------- LLM 工具

    @filter.llm_tool(name="generate_image")
    async def tool_generate_image(
        self,
        event: AstrMessageEvent,
        prompt: str,
        reference_images: bool = False,
        size: str = "",
        aspect_ratio: str = "",
        quality: str = "",
        style: str = "",
        transparent_background: bool = False,
    ) -> str:
        """生成或修改一张图片，并把结果直接发送到当前会话。

        当用户明确要求画图、出图、生成图片、修改图片、把某张图换成别的风格时调用。
        普通聊天、写作、代码、设计建议、询问参数不要调用。一次用户消息最多调用一次。

        Args:
            prompt(string): 必填。完整具体的图片提示词，包含主体、风格、构图、光线以及需要出现的文字。
            reference_images(boolean): 可选。需要基于当前消息或引用消息里的图片修改、合成、延续风格时设为 true。
            size(string): 可选。像素尺寸，例如 1024x1024、1536x1024、1024x1536；用户没有明确要求时留空。
            aspect_ratio(string): 可选。画面比例，例如 1:1、16:9、9:16；只在用户明确要求比例时填写。
            quality(string): 可选。画质档位 auto/low/medium/high；用户没有要求时留空。
            style(string): 可选。额外风格词，例如 anime、watercolor、pixel art。
            transparent_background(boolean): 可选。画图标、贴纸、需要透明背景时设为 true。
        """
        settings = self.settings
        if not settings.enable_llm_tool:
            return "生图工具当前已关闭，请让管理员在插件配置里开启后再试。"
        if not prompt or not prompt.strip():
            return "缺少图片提示词，无法生成图片。"

        denial = self._check_access(event)
        if denial:
            return denial
        problems = settings.validate()
        if problems:
            return "生图功能还没配置好：" + " ".join(problems)

        options = Options(
            size=size,
            ratio=aspect_ratio,
            quality=quality,
            style=style,
            transparent=transparent_background,
        )
        references: list[ReferenceImage] = []
        try:
            if reference_images:
                paths = await self._collect_image_paths(event)
                if not paths:
                    return "没有找到参考图。请先发送或引用一张图片，再说明要怎么修改。"
                references = self._load_references(paths)
            final_size = resolve_size(options.size, options.ratio, settings.size)
        except PainterError as exc:
            return exc.user_message
        except ValueError as exc:
            return str(exc)

        request_key = _request_digest(event, prompt, references)
        existing = self._inflight.get(request_key)
        if existing is not None and not existing.done():
            return "这张图已经在生成中了，请稍等，图片会直接发到会话里。"
        if existing is not None:
            self._inflight.pop(request_key, None)

        denial, consumed = await self._consume_quota(event)
        if denial:
            return denial

        if not await self._acquire_slot():
            await self._refund_quota(event, consumed)
            return "当前生图任务较多，请稍后再试。"

        task = asyncio.create_task(
            self._generate_and_send(
                event=event,
                prompt=build_prompt(
                    prompt,
                    prefix=settings.prompt_prefix,
                    suffix=settings.prompt_suffix,
                    style=options.style,
                ),
                references=references,
                size=final_size,
                quality=options.quality or settings.quality,
                output_format=settings.output_format,
                background="transparent" if options.transparent else settings.background,
                consumed_quota=consumed,
            )
        )
        self._inflight[request_key] = task
        self._track(task, request_key)
        return (
            "已提交图片生成任务，图片会直接发送到当前会话。"
            "请用一句话简短告知用户正在绘制，不要重复这段状态说明。"
        )

    @filter.on_llm_request(priority=-9000)
    async def inject_tool_instruction(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not self.settings.enable_llm_tool or not self.settings.tool_instruction:
            return
        req.system_prompt = (req.system_prompt or "") + "\n\n" + self.settings.tool_instruction

    # ------------------------------------------------------------ 生成与投递

    async def _generate_and_send(
        self,
        *,
        event: AstrMessageEvent,
        prompt: str,
        references: list[ReferenceImage],
        size: str,
        quality: str,
        output_format: str,
        background: str,
        consumed_quota: bool,
        note: str = "",
    ) -> None:
        started = time.monotonic()
        try:
            image = await self._generate(
                prompt=prompt,
                references=references,
                size=size,
                quality=quality,
                output_format=output_format,
                background=background,
            )
            elapsed = time.monotonic() - started
            path = self._store.save(image.data, image.ext)
            self._store.prune()
            chain: list[Any] = []
            caption = self._caption(size=size, quality=quality, elapsed=elapsed, note=note)
            if caption:
                chain.append(Plain(caption))
            chain.append(Image.fromFileSystem(str(path)))
            await self._safe_send(event, event.chain_result(chain))
            logger.info("Astr Painter 出图完成：%.1fs -> %s", elapsed, path.name)
        except asyncio.CancelledError:
            await self._refund_quota(event, consumed_quota)
            raise
        except PainterError as exc:
            logger.warning("Astr Painter 生成失败：%s（%s）", exc.user_message, exc.detail)
            await self._refund_quota(event, consumed_quota)
            await self._safe_send(event, event.plain_result(f"生图失败：{exc.user_message}"))
        except Exception:
            logger.exception("Astr Painter 生成时发生未预期的错误")
            await self._refund_quota(event, consumed_quota)
            await self._safe_send(
                event, event.plain_result("生图失败：插件内部错误，请查看 AstrBot 日志。")
            )
        finally:
            if self.settings.debug_log_response and self._client is not None:
                logger.info("Astr Painter 接口响应摘要：%s", self._client.last_response_preview)

    async def _generate(
        self,
        *,
        prompt: str,
        references: list[ReferenceImage],
        size: str,
        quality: str,
        output_format: str,
        background: str,
    ) -> GeneratedImage:
        client = self._ensure_client()
        if references:
            return await client.edit(
                prompt,
                references,
                size=size,
                quality=quality,
                output_format=output_format,
                background=background,
            )
        return await client.generate(
            prompt,
            size=size,
            quality=quality,
            output_format=output_format,
            background=background,
        )

    def _ensure_client(self) -> ImagesClient:
        if self._client is None:
            self._client = ImagesClient(self.settings)
        return self._client

    def _caption(self, *, size: str, quality: str, elapsed: float, note: str) -> str:
        parts: list[str] = []
        if self.settings.show_meta:
            parts.append(
                f"{self.settings.model} · {size or 'auto'} · {quality or 'auto'} · {elapsed:.1f}s"
            )
        if note:
            parts.append(note)
        return "\n".join(parts)

    async def _safe_send(self, event: AstrMessageEvent, result: Any) -> None:
        try:
            await event.send(result)
        except Exception:
            logger.error("Astr Painter 发送消息失败", exc_info=True)

    async def _acquire_slot(self) -> bool:
        timeout = max(0.01, float(self.settings.queue_wait_seconds))
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _track(self, task: asyncio.Task[Any], request_key: str | None = None) -> None:
        self._tasks.add(task)

        def _done(done: asyncio.Task[Any]) -> None:
            # permit 在这里归还而不是协程的 finally 里：任务在协程体启动前就被取消
            # （比如插件卸载）时，finally 根本不会执行，会永久泄漏一个并发名额。
            self._semaphore.release()
            self._tasks.discard(done)
            if request_key and self._inflight.get(request_key) is done:
                self._inflight.pop(request_key, None)
            if done.cancelled():
                return
            error = done.exception()
            if error:
                logger.error("Astr Painter 后台任务异常：%s", error)

        task.add_done_callback(_done)

    # ---------------------------------------------------------- 权限与额度

    def _check_access(self, event: AstrMessageEvent) -> str | None:
        settings = self.settings
        user_id = str(event.get_sender_id())
        if user_id in settings.blacklist:
            return "你没有使用生图功能的权限。"
        if settings.whitelist and user_id not in settings.whitelist:
            return "你没有使用生图功能的权限。"
        if settings.admin_only and not event.is_admin():
            return "生图功能仅管理员可用。"
        return None

    async def _consume_quota(self, event: AstrMessageEvent) -> tuple[str | None, bool]:
        settings = self.settings
        if not settings.quota_enabled and settings.cooldown_seconds <= 0:
            return None, False
        if settings.admin_bypass and event.is_admin():
            return None, False

        user_id = str(event.get_sender_id())
        if settings.quota_enabled:
            today = datetime.now().strftime("%Y-%m-%d")
            record = await self.get_kv_data(f"quota:{user_id}", {})
            count = 0
            if isinstance(record, dict) and record.get("date") == today:
                try:
                    count = int(record.get("count", 0))
                except (TypeError, ValueError):
                    count = 0
            if count >= settings.daily_limit:
                return f"今日生图次数已用完（上限 {settings.daily_limit} 次）。", False

        now = time.monotonic()
        if settings.cooldown_seconds > 0:
            last = self._cooldowns.get(user_id)
            if last is not None:
                remaining = settings.cooldown_seconds - (now - last)
                if remaining > 0:
                    return f"操作太频繁，请 {int(remaining) + 1} 秒后再试。", False

        consumed = False
        if settings.quota_enabled:
            await self.put_kv_data(f"quota:{user_id}", {"date": today, "count": count + 1})
            consumed = True

        self._cooldowns[user_id] = now
        return None, consumed

    async def _refund_quota(self, event: AstrMessageEvent, consumed: bool) -> None:
        user_id = str(event.get_sender_id())
        self._cooldowns.pop(user_id, None)
        if not consumed:
            return
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            record = await self.get_kv_data(f"quota:{user_id}", {})
            if isinstance(record, dict) and record.get("date") == today:
                count = max(0, int(record.get("count", 1)) - 1)
                await self.put_kv_data(f"quota:{user_id}", {"date": today, "count": count})
        except Exception:
            logger.warning("Astr Painter 退回额度失败", exc_info=True)

    # ---------------------------------------------------------------- 参考图

    async def _collect_image_paths(self, event: AstrMessageEvent) -> list[str]:
        components = _walk_images(event.get_messages())[: self.settings.max_reference_images]
        paths: list[str] = []
        seen: set[str] = set()
        for component in components:
            try:
                path = await component.convert_to_file_path()
            except Exception:
                logger.warning("Astr Painter 读取参考图失败，已跳过该图", exc_info=True)
                continue
            resolved = str(Path(path).resolve())
            if resolved not in seen:
                seen.add(resolved)
                paths.append(resolved)
        return paths

    def _load_references(self, paths: list[str]) -> list[ReferenceImage]:
        references: list[ReferenceImage] = []
        for path in paths[: self.settings.max_reference_images]:
            try:
                data = Path(path).read_bytes()
            except OSError as exc:
                raise PainterError("读取参考图失败，请重新发送图片。", detail=str(exc)) from exc
            references.append(normalize_reference(data, Path(path).name, self.settings.max_side))
        return references

    # ------------------------------------------------------------------ 帮助

    def _help_text(self) -> str:
        settings = self.settings
        lines = [
            "Astr Painter 用法：",
            "/生图 <提示词> [--ratio 16:9] [--size 1024x1024] [--quality high] [--format png] [--style 风格] [--transparent]",
            f"/改图 <提示词>：同一条消息带图，或回复/引用一张图；最多使用 {settings.max_reference_images} 张参考图",
            f"当前模型：{settings.model}；默认尺寸：{settings.size}；默认质量：{settings.quality}",
        ]
        if settings.quota_enabled:
            lines.append(
                f"每日上限：{settings.daily_limit} 次；冷却：{settings.cooldown_seconds} 秒"
            )
        if not settings.api_key:
            lines.append("提示：还没有配置 API Key，请先在插件配置里填写 api.api_key。")
        return "\n".join(lines)


def _walk_images(
    chain: Any,
    depth: int = 0,
    found: list[Any] | None = None,
) -> list[Any]:
    """递归收集消息链里的图片组件，覆盖引用消息与合并转发。"""
    if found is None:
        found = []
    if depth > MAX_CHAIN_DEPTH or not chain:
        return found
    if isinstance(chain, Image):
        found.append(chain)
        return found
    if not isinstance(chain, (list, tuple)):
        return found

    for component in chain:
        if isinstance(component, Image):
            found.append(component)
        for attribute in ("chain", "content"):
            nested = getattr(component, attribute, None)
            if nested:
                _walk_images(nested, depth + 1, found)
        nodes = getattr(component, "nodes", None)
        if nodes:
            for node in nodes:
                _walk_images(getattr(node, "content", None), depth + 1, found)
    return found


def _event_text(event: AstrMessageEvent) -> str:
    text = getattr(event, "message_str", "") or ""
    if not text:
        try:
            text = event.get_message_str() or ""
        except Exception:
            text = ""
    return str(text).strip()


def _strip_command(text: str, command: str) -> str:
    for prefix in (f"/{command}", command):
        if text == prefix:
            return ""
        if text.startswith(prefix):
            return text[len(prefix) :].lstrip()
    return text


def _request_digest(event: AstrMessageEvent, prompt: str, references: list[ReferenceImage]) -> str:
    digest = hashlib.sha1()
    digest.update(prompt.encode("utf-8", "ignore"))
    digest.update(str(event.unified_msg_origin).encode("utf-8", "ignore"))
    for image in references:
        digest.update(hashlib.sha1(image.data).digest())
    return digest.hexdigest()


def _usage_text(command: str) -> str:
    if command == "改图":
        return "用法：发送或引用一张图片后，输入 /改图 把背景换成夜晚城市。"
    return "用法：/生图 一只坐在霓虹窗边的赛博猫 --ratio 16:9 --quality high"
